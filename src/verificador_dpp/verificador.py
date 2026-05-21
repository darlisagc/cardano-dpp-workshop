"""Verificador DPP — caminha cadeias heterogeneas (A + B/C).

Para cada credencial na cadeia:
  1. Tenta primeiro ler metadata nativa (Blockfrost) — funciona para
     credenciais emitidas por `emissor_direto` (Opção A).
  2. Se nao encontrar metadata UVerify, busca candidatos a `data_hash`
     no datum inline do output de script da transacao e consulta a
     API publica do UVerify — funciona para credenciais emitidas via
     `emissor_sdk` (Opção B) ou pela UI (Opção C).

Walks the chain via `cert_*_credential_tx` references — para cada
step, escolhe automaticamente entre metadata nativa ou API UVerify
de acordo com como aquela transacao foi emitida.

Uso:
    PYTHONPATH=src python -m verificador_dpp.verificador
    PYTHONPATH=src python -m verificador_dpp.verificador <tx_hash_pack>

Pre-requisitos no .env:
    BLOCKFROST_PROJECT_ID  projeto preprod
    TX_HASH_PACK           hash do pack (Ator 3) — entrada da cadeia
    DATA_HASH_PACK         (opcional) hint do data_hash do pack;
                           usado se o pack foi emitido via B/C
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any

import cbor2
from blockfrost import ApiUrls, BlockFrostApi
from dotenv import load_dotenv
from uverify_sdk import UVerifyApiError, UVerifyClient

from .modelos import CredencialDPP, PassaporteBateria
from .parser_credencial import ParserCredencial
from .relatorio_passaporte import RelatorioPassaporte


# -----------------------------------------------------------------
# Datum walker — encontra todas as bytes de 32 bytes em uma estrutura
# CBOR decodificada (recursivo). 32 bytes e o tamanho de um sha256,
# que e exatamente o data_hash do template UVerify.
# -----------------------------------------------------------------

def _walk_for_32byte(node: Any, out: list[str]) -> None:
    if isinstance(node, bytes):
        if len(node) == 32:
            out.append(node.hex())
        return
    if isinstance(node, cbor2.CBORTag):
        _walk_for_32byte(node.value, out)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _walk_for_32byte(item, out)
    elif isinstance(node, dict):
        for v in node.values():
            _walk_for_32byte(v, out)


def _extrair_candidatos_data_hash(
    blockfrost: BlockFrostApi, tx_hash: str
) -> list[str]:
    """Le os outputs da tx, parseia os inline_datums e devolve todos os
    32-byte bytes encontrados — candidatos para o `data_hash`."""
    candidatos: list[str] = []
    try:
        utxos = blockfrost.transaction_utxos(tx_hash)
    except Exception:
        return candidatos

    for output in getattr(utxos, "outputs", []) or []:
        inline_datum_hex = getattr(output, "inline_datum", None)
        if not inline_datum_hex:
            continue
        try:
            obj = cbor2.loads(bytes.fromhex(inline_datum_hex))
        except Exception:
            continue
        _walk_for_32byte(obj, candidatos)
    return candidatos


# -----------------------------------------------------------------
# Conversao do CertificateResponse do UVerify -> CredencialDPP
# -----------------------------------------------------------------

def _normalize_metadata(meta: Any) -> dict | None:
    if isinstance(meta, str):
        try:
            return json.loads(meta)
        except Exception:
            return None
    if isinstance(meta, dict):
        return meta
    if hasattr(meta, "__dict__"):
        return dict(vars(meta))
    return None


def _credencial_from_uverify_response(cert: Any) -> CredencialDPP:
    raw_meta = getattr(cert, "metadata", None)
    if raw_meta is None:
        raise ValueError("UVerify response sem metadata")
    meta = _normalize_metadata(raw_meta) or {}

    referencias: dict[str, str] = {}
    materiais: dict[str, str] = {}
    for k, v in meta.items():
        if k.startswith("cert_") and k.endswith("_credential_tx"):
            referencias[k[len("cert_"):]] = str(v)
        elif k.startswith("mat_"):
            materiais[k[len("mat_"):]] = str(v)

    return CredencialDPP(
        nome=meta.get("name"),
        emitente=meta.get("issuer"),
        gtin=meta.get("gtin"),
        origem=meta.get("origin"),
        fabricado_em=meta.get("manufactured"),
        pegada_carbono=meta.get("carbon_footprint"),
        conteudo_reciclado=meta.get("recycled_content"),
        materiais=materiais,
        referencias=referencias,
    )


# -----------------------------------------------------------------
# Lookup principal: metadata -> UVerify API fallback
# -----------------------------------------------------------------

def buscar_credencial(
    blockfrost: BlockFrostApi,
    uverify: UVerifyClient,
    parser: ParserCredencial,
    tx_hash: str,
    data_hash_hint: str | None = None,
) -> CredencialDPP:
    """Tenta metadata nativa primeiro (Opção A); se falhar, vai pela
    API do UVerify usando candidatos a `data_hash` extraidos do datum
    on-chain (ou do hint, se fornecido — caso pack emitido via B/C)."""

    # ----------------------------------------------------------------
    # Caminho 1 — metadata nativa Cardano (catches Opção A)
    # ----------------------------------------------------------------
    try:
        metadata_entries = blockfrost.transaction_metadata(tx_hash)
    except Exception:
        metadata_entries = None

    if metadata_entries:
        try:
            return parser.extrair_credencial(metadata_entries)
        except Exception:
            # metadata existe mas nao tem uverify_template_id —
            # provavelmente uma tx UVerify (B/C). Cai pro fallback.
            pass

    # ----------------------------------------------------------------
    # Caminho 2 — UVerify API (catches Opção B/C)
    # Reune candidatos a data_hash: hint primeiro (se fornecido),
    # depois todas as bytes de 32 bytes encontradas em inline datums.
    # ----------------------------------------------------------------
    candidatos: list[str] = []
    if data_hash_hint:
        candidatos.append(data_hash_hint)
    candidatos.extend(_extrair_candidatos_data_hash(blockfrost, tx_hash))

    if not candidatos:
        raise RuntimeError(
            f"Tx {tx_hash}: sem metadata uverify_template_id e sem "
            "32-byte candidates no inline datum — nao consegui localizar."
        )

    last_error: Exception | None = None
    seen: set[str] = set()
    for dh in candidatos:
        if dh in seen:
            continue
        seen.add(dh)
        try:
            cert = uverify.verify_by_transaction(tx_hash, dh)
            return _credencial_from_uverify_response(cert)
        except UVerifyApiError as e:
            last_error = e
            continue

    raise RuntimeError(
        f"Tx {tx_hash}: nenhum dos {len(seen)} hashes-candidatos foi "
        f"reconhecido pelo UVerify (ultimo erro: {last_error})"
    )


# -----------------------------------------------------------------
# CLI
# -----------------------------------------------------------------

def main() -> None:
    load_dotenv()

    args = sys.argv[1:]
    tx_hash_pack = (
        args[0] if args else os.environ.get("TX_HASH_PACK", "").strip()
    )
    data_hash_pack = os.environ.get("DATA_HASH_PACK", "").strip()
    project_id = os.environ.get("BLOCKFROST_PROJECT_ID", "").strip()

    if not tx_hash_pack:
        sys.exit("ERRO: informe TX_HASH_PACK no .env ou como 1o argumento.")
    if not project_id or project_id.startswith("preprodXXXX"):
        sys.exit("ERRO: BLOCKFROST_PROJECT_ID nao configurado no .env.")

    print("=" * 64)
    print("Verificador DPP - Workshop Cardano")
    print("De Jequitinhonha a Europa: o Passaporte da Bateria")
    print("=" * 64)
    print()

    blockfrost = BlockFrostApi(
        project_id=project_id, base_url=ApiUrls.preprod.value
    )
    uverify = UVerifyClient()
    parser = ParserCredencial()
    relatorio = RelatorioPassaporte()

    try:
        # ------------------------------------------------------------
        # Passo 1 — credencial do PACK (entrada do verificador).
        # data_hash_pack do .env e usado como hint caso o pack tenha
        # sido emitido via B/C (UVerify). Se foi via A, o hint e
        # ignorado e a metadata nativa resolve.
        # ------------------------------------------------------------
        print("[1/4] Buscando credencial do pack...")
        cred_pack = buscar_credencial(
            blockfrost, uverify, parser, tx_hash_pack,
            data_hash_pack or None,
        )
        print(f"      OK - {cred_pack.nome}")
        print()

        # ------------------------------------------------------------
        # Passo 2 — segue cert_celula_credential_tx do pack.
        # data_hash da celula e descoberto on-chain (sem hint).
        # ------------------------------------------------------------
        print("[2/4] Seguindo referencias para as celulas...")
        tx_celula = cred_pack.referencias.get("celula_credential_tx")
        cred_celula = None
        if tx_celula:
            cred_celula = buscar_credencial(
                blockfrost, uverify, parser, tx_celula
            )
            print(f"      OK - {cred_celula.nome}")
        else:
            print("      AVISO: pack nao referencia credencial de celula.")
        print()

        # ------------------------------------------------------------
        # Passo 3 — segue cert_origem_credential_tx da celula.
        # ------------------------------------------------------------
        print("[3/4] Seguindo referencias para a origem do litio...")
        cred_origem = None
        if cred_celula is not None:
            tx_origem = cred_celula.referencias.get("origem_credential_tx")
            if tx_origem:
                cred_origem = buscar_credencial(
                    blockfrost, uverify, parser, tx_origem
                )
                print(f"      OK - {cred_origem.nome}")
            else:
                print(
                    "      AVISO: celula nao referencia credencial de origem."
                )
        print()

        # ------------------------------------------------------------
        # Passo 4 — montar e imprimir o relatorio.
        # ------------------------------------------------------------
        print("[4/4] Montando relatorio do passaporte...")
        print()
        passaporte = PassaporteBateria(cred_origem, cred_celula, cred_pack)
        print(relatorio.gerar(passaporte))

    except Exception as e:  # noqa: BLE001
        print(f"FALHA: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
