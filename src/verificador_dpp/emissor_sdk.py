"""Emissor DPP via uverify-sdk - opcao B do hands-on (Secao 2).

Usa o cliente oficial UVerify para construir + submeter a transacao;
o codigo Python so prove a callback de assinatura, que delega
ao PyCardano.

Uso:
    PYTHONPATH=src python -m verificador_dpp.emissor_sdk --ator origem
    PYTHONPATH=src python -m verificador_dpp.emissor_sdk --ator celula
    PYTHONPATH=src python -m verificador_dpp.emissor_sdk --ator pack
    PYTHONPATH=src python -m verificador_dpp.emissor_sdk --ator reciclagem

Pre-requisitos no .env (ver .env.example):
    WALLET_MNEMONIC        24 palavras (TESTNET ONLY)
    ATOR1_TX, ATOR2_TX...  preenchidos sequencialmente apos cada emissao

Fluxo da emissao (cada `--ator <X>`):
    1. Carregar payload DPP do ator
    2. Derivar carteira HD do mnemonico
    3. Calcular o data_hash = sha256(gtin + serial)
    4. Embrulhar tudo num CertificateData
    5. Pedir ao SDK que monte+submeta a tx, fornecendo a callback
       de assinatura que assina com a chave HD via PyCardano
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Callable

from dotenv import load_dotenv
from pycardano import (
    ExtendedSigningKey,
    Transaction,
    TransactionWitnessSet,
    VerificationKeyWitness,
)
from uverify_sdk import UVerifyClient
from uverify_sdk.models import CertificateData

from ._payloads import ATORES, PROXIMO_ATOR_ENV, data_hash
from .wallet import carregar_carteira


def fazer_callback_assinatura(
    payment_skey: ExtendedSigningKey,
) -> Callable[[str], str]:
    """Cria a callback que o UVerify SDK chama para assinar a tx.

    Contrato (verificado contra uverify-sdk 0.1.7):
        sign_tx(unsigned_cbor_hex: str) -> witness_set_cbor_hex: str

    Fluxo da assinatura (4 passos):
        1. Decodificar a tx CBOR-hex que o UVerify acabou de montar
        2. Calcular o hash do transaction_body (32 bytes)
        3. Assinar esse hash com a chave de pagamento (Ed25519, 64 bytes)
        4. Embrulhar (vkey + signature) num TransactionWitnessSet e
           devolver em CBOR-hex para o SDK submeter
    """

    def sign_tx(unsigned_cbor_hex: str) -> str:
        # Passo 1 — decodifica a tx que veio do UVerify (CBOR-hex string).
        tx = Transaction.from_cbor(unsigned_cbor_hex)

        # Passo 2 — hash do body (o que o Cardano espera ver assinado).
        body_hash = tx.transaction_body.hash()

        # Passo 3 — assinatura Ed25519 sobre o body_hash.
        signature = payment_skey.sign(body_hash)

        # Passo 4 — Cardano espera vkey Ed25519 normal de 32 bytes
        # (sem o chain code de 32 bytes do CIP-1852 estendido).
        vkey = payment_skey.to_verification_key().to_non_extended()
        witness = VerificationKeyWitness(vkey, signature)

        # Devolve o witness set em CBOR-hex - formato que o SDK espera.
        return TransactionWitnessSet(vkey_witnesses=[witness]).to_cbor_hex()

    return sign_tx


def emitir_via_sdk(
    ator: str, env: dict[str, str], mnemonic: str
) -> tuple[str, str]:
    """Emite a credencial DPP via UVerify SDK; devolve (tx_hash, data_hash)."""

    # ----------------------------------------------------------------
    # Passo 1 — Construir o payload DPP e extrair gtin/serial.
    # ----------------------------------------------------------------
    payload, serial, gtin = ATORES[ator](env)

    # ----------------------------------------------------------------
    # Passo 2 — Carregar a carteira HD do mnemonico.
    # ----------------------------------------------------------------
    payment_skey, address = carregar_carteira(mnemonic)

    # ----------------------------------------------------------------
    # Passo 3 — Embrulhar tudo num CertificateData:
    #   - hash:      sha256(gtin + serial) - identificador do produto
    #   - algorithm: SHA-256
    #   - metadata:  o payload DPP (template digitalProductPassport)
    # ----------------------------------------------------------------
    cert = CertificateData(
        hash=data_hash(gtin, serial),
        algorithm="SHA-256",
        metadata=payload,
    )

    # ----------------------------------------------------------------
    # Passo 4 — Criar o cliente UVerify (default: api.preprod.uverify.io)
    # e pedir que ele faca emissao.
    # O SDK:
    #   1. POSTa /api/v1/transaction/build  -> recebe tx CBOR-hex
    #   2. chama nossa sign_tx callback     -> recebe witness CBOR-hex
    #   3. POSTa /api/v1/transaction/submit -> recebe tx_hash
    # ----------------------------------------------------------------
    client = UVerifyClient()
    tx_hash = client.issue_certificates(
        address=str(address),
        certificates=[cert],
        sign_tx=fazer_callback_assinatura(payment_skey),
    )
    return tx_hash, cert.hash


def main() -> None:
    # Carrega variaveis do .env (WALLET_MNEMONIC, ATOR*_TX)
    load_dotenv()

    parser = argparse.ArgumentParser(
        description=(
            "Emissor DPP via UVerify SDK. Opcao B do hands-on."
        )
    )
    parser.add_argument(
        "--ator",
        required=True,
        choices=list(ATORES.keys()),
        help="Qual ator emitir: origem, celula, pack ou reciclagem.",
    )
    args = parser.parse_args()

    # Validacao basica: precisamos do mnemonico para assinar.
    mnemonic = os.environ.get("WALLET_MNEMONIC", "").strip()
    if not mnemonic:
        sys.exit(
            "ERRO: defina WALLET_MNEMONIC no .env (24 palavras, TESTNET ONLY)."
        )

    print(f"Emitindo DPP do Ator '{args.ator}' via UVerify SDK (preprod)...")
    print()

    # Executa o fluxo de 4 passos definido em emitir_via_sdk().
    tx_hash, dh = emitir_via_sdk(args.ator, dict(os.environ), mnemonic)
    proxima_chave = PROXIMO_ATOR_ENV[args.ator]

    # Imprime tx_hash + data_hash (este ultimo e usado pelo
    # verificador_misto como hint quando o pack veio da Opcao B/C —
    # guarde como DATA_HASH_PACK no .env quando for `pack`).
    print("OK - credencial publicada em Cardano preprod.")
    print(f"  tx_hash:        {tx_hash}")
    print(f"  data_hash:      {dh}")
    print(
        f"  CardanoScan:    https://preprod.cardanoscan.io/transaction/{tx_hash}"
    )
    print()
    print(f"Proximo passo: cole no .env como  {proxima_chave}={tx_hash}")


if __name__ == "__main__":
    main()
