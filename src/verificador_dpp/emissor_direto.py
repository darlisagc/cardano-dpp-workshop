"""Emissor DPP DIRETO via PyCardano - opcao A do hands-on (Secao 2).

NAO usa o uverify-sdk. Constroi a transacao do zero com
TransactionBuilder, anexa o payload DPP como metadata nativa do
Cardano e submete via Blockfrost.

Uso:
    PYTHONPATH=src python -m verificador_dpp.emissor_direto --ator origem
    PYTHONPATH=src python -m verificador_dpp.emissor_direto --ator celula
    PYTHONPATH=src python -m verificador_dpp.emissor_direto --ator pack
    PYTHONPATH=src python -m verificador_dpp.emissor_direto --ator reciclagem

Pre-requisitos no .env (ver .env.example):
    BLOCKFROST_PROJECT_ID  projeto preprod no blockfrost.io
    WALLET_MNEMONIC        24 palavras (TESTNET ONLY)
    ATOR1_TX, ATOR2_TX...  preenchidos sequencialmente apos cada emissao

Fluxo da emissao (cada `--ator <X>`):
    1. Carregar payload DPP do ator       (_payloads.py)
    2. Derivar carteira HD do mnemonico   (wallet.py)
    3. Conectar ao Blockfrost preprod
    4. Construir tx self-pay com metadata
    5. Assinar com a chave de pagamento
    6. Submeter a rede preprod
"""

from __future__ import annotations

import argparse
import os
import sys

from blockfrost import ApiUrls
from dotenv import find_dotenv, load_dotenv, set_key
from pycardano import (
    AuxiliaryData,
    BlockFrostChainContext,
    Metadata,
    TransactionBuilder,
)

from ._payloads import ATORES, PROXIMO_ATOR_ENV, data_hash
from .wallet import carregar_carteira

# Label de metadata Cardano. Inteiro arbitrario >= 1 reservado pelo
# workshop. O verificador escaneia TODOS os labels procurando
# "uverify_template_id", entao mudar este numero nao quebra nada.
METADATA_LABEL = 1990


def emitir_direto(
    ator: str, env: dict[str, str], mnemonic: str, project_id: str
) -> tuple[str, str]:
    """Emite a credencial DPP do ator informado.

    Devolve (tx_hash, data_hash). O data_hash = sha256(gtin + serial)
    e o identificador do produto per template UVerify; util para
    quem quiser inspecionar a credencial pela URL publica do UVerify
    ou usar como hint inicial no `verificador_misto` quando uma
    cadeia mistura A com B/C.
    """

    # ----------------------------------------------------------------
    # Passo 1 — Construir o payload DPP do ator escolhido.
    # `_payloads.py` contem os dados de cada ator (origem, celula,
    # pack, reciclagem) seguindo o template digitalProductPassport.
    # Atores 2-4 ainda exigem ATOR<N>_TX no env (para encadear).
    # ----------------------------------------------------------------
    payload, serial, gtin = ATORES[ator](env)
    dh = data_hash(gtin, serial)

    # ----------------------------------------------------------------
    # Passo 2 — Carregar a carteira HD a partir do mnemonico.
    # Deriva chave de pagamento + endereco preprod via CIP-1852
    # (mesmo caminho que Eternl/Lace usam).
    # ----------------------------------------------------------------
    payment_skey, address = carregar_carteira(mnemonic)

    # ----------------------------------------------------------------
    # Passo 3 — Conectar ao Blockfrost preprod.
    # `BlockFrostChainContext` e a abstracao do PyCardano que
    # consulta UTxOs e submete transacoes via API do Blockfrost.
    # ----------------------------------------------------------------
    context = BlockFrostChainContext(
        project_id=project_id,
        base_url=ApiUrls.preprod.value,
    )

    # ----------------------------------------------------------------
    # Passo 4 — Construir a transacao com TransactionBuilder.
    #   - input:           UTxOs encontrados no nosso endereco
    #   - output:          NENHUM explicito — `change_address` no
    #                      build_and_sign manda o leftover (input - fee)
    #                      de volta para o nosso proprio endereco
    #                      (volta como UTxO seu, voce nao perde nada
    #                      alem do fee de ~0.18 tADA)
    #   - auxiliary_data:  payload DPP como metadata nativa Cardano
    #                      sob o label 1990
    # ----------------------------------------------------------------
    builder = TransactionBuilder(context)
    builder.add_input_address(address)
    builder.auxiliary_data = AuxiliaryData(
        Metadata({METADATA_LABEL: payload})
    )

    # ----------------------------------------------------------------
    # Passo 5 — Assinar a transacao com a chave de pagamento.
    # `build_and_sign` calcula o fee, escolhe os UTxOs (coin-selection),
    # monta o body, assina, e devolve uma Transaction completa.
    # ----------------------------------------------------------------
    signed_tx = builder.build_and_sign(
        signing_keys=[payment_skey],
        change_address=address,
    )

    # ----------------------------------------------------------------
    # Passo 6 — Submeter a transacao a rede preprod.
    # Em ~20-40s a tx aparece em Cexplorer preprod.
    # `submit_tx` retorna None em sucesso; o tx_hash sai de signed_tx.id.
    # ----------------------------------------------------------------
    context.submit_tx(signed_tx)
    return str(signed_tx.id), dh


def main() -> None:
    # Carrega variaveis do .env (BLOCKFROST_PROJECT_ID, WALLET_MNEMONIC, ATOR*_TX)
    load_dotenv()

    parser = argparse.ArgumentParser(
        description=(
            "Emissor DPP direto via PyCardano "
            "(sem uverify-sdk). Opcao A do hands-on."
        )
    )
    parser.add_argument(
        "--ator",
        required=True,
        choices=list(ATORES.keys()),
        help="Qual ator emitir: origem, celula, pack ou reciclagem.",
    )
    args = parser.parse_args()

    # Validacao basica: precisamos de mnemonico e project_id valido.
    mnemonic = os.environ.get("WALLET_MNEMONIC", "").strip()
    project_id = os.environ.get("BLOCKFROST_PROJECT_ID", "").strip()

    if not mnemonic:
        sys.exit(
            "ERRO: defina WALLET_MNEMONIC no .env (24 palavras, TESTNET ONLY)."
        )
    if not project_id or project_id.startswith("preprodXXXX"):
        sys.exit("ERRO: defina BLOCKFROST_PROJECT_ID (preprod) no .env.")

    print(f"Emitindo DPP do Ator '{args.ator}' DIRETO via PyCardano...")
    print()

    # Executa o fluxo de 6 passos definido em emitir_direto().
    tx_hash, dh = emitir_direto(args.ator, dict(os.environ), mnemonic, project_id)
    proxima_chave = PROXIMO_ATOR_ENV[args.ator]

    # Imprime resultado e atualiza .env automaticamente para encadear
    # o proximo ator. data_hash tambem vai pro .env (para a URL UVerify
    # ou como hint do verificador_misto).
    print("OK - tx submetida em Cardano preprod.")
    print(f"  tx_hash:        {tx_hash}")
    print(f"  data_hash:      {dh}")
    print(
        f"  Cexplorer:      https://preprod.cexplorer.io/tx/{tx_hash}"
    )
    print()

    # Auto-atualiza .env (sem aspas, no formato existente)
    env_path = find_dotenv(usecwd=True) or ".env"
    atualizadas = [f"{proxima_chave}={tx_hash}"]
    set_key(env_path, proxima_chave, tx_hash, quote_mode="never")
    if args.ator == "pack":
        set_key(env_path, "TX_HASH_PACK", tx_hash, quote_mode="never")
        set_key(env_path, "DATA_HASH_PACK", dh, quote_mode="never")
        atualizadas.append(f"TX_HASH_PACK={tx_hash}")
        atualizadas.append(f"DATA_HASH_PACK={dh}")
    print("✓ .env atualizado:")
    for linha in atualizadas:
        print(f"    {linha}")


if __name__ == "__main__":
    main()
