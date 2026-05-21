# Verificador & Emissor DPP — Workshop Cardano

**De Jequitinhonha à Europa: o Passaporte da Bateria**

Starter project em Python 3.11+ que **emite** e **verifica** uma
cadeia de Passaportes Digitais de Produto (DPP) ancorados em
Cardano **preprod** — com **duas implementações paralelas** para
cada operação:

**Emissão** tem três opções (A, B, C); **verificação** é unificada num único módulo que cobre qualquer mistura:

| Emissão | Como | Verificação (única) |
|---|---|---|
| **A — Python direto** | `emissor_direto.py` (PyCardano `TransactionBuilder`) | `verificador.py` |
| **B — Python via SDK** | `emissor_sdk.py` (`uverify-sdk`) | `verificador.py` |
| **C — UI UVerify** | <https://app.preprod.uverify.io> (sem código) | `verificador.py` |

> ⚠️ **Rede:** o UVerify público opera em **preprod testnet**. Todo
> o starter aponta para preprod (Blockfrost preprod, faucet preprod,
> Cexplorer preprod, API UVerify preprod).

---

## Pré-requisitos

| Componente | Versão |
|---|---|
| Python | 3.11+ |
| [uv](https://docs.astral.sh/uv/) | gerenciador de pacotes/venv (instale com `curl -LsSf https://astral.sh/uv/install.sh \| sh`) |
| IDE | VS Code com Python extension, PyCharm Community ou similar |
| Carteira Cardano | [Eternl](https://eternl.io) ou [Lace](https://lace.io) em **preprod** |
| tADA | [Faucet preprod](https://docs.cardano.org/cardano-testnets/tools/faucet/) |
| Blockfrost | Conta gratuita em [blockfrost.io](https://blockfrost.io), projeto **preprod** |

## Setup

```bash
uv sync                  # cria .venv e instala dependencias com lock reproducivel
cp .env.example .env     # preencha BLOCKFROST_PROJECT_ID e WALLET_MNEMONIC (TESTNET ONLY)
```

`uv sync` é idempotente — roda quantas vezes quiser. Não precisa
ativar o venv: use `uv run <comando>` em qualquer diretório do
projeto e o uv resolve tudo.

## Emissão (Seção 2 do hands-on)

Antes de emitir: tenha tADA na carteira preprod cuja mnemônica está
em `WALLET_MNEMONIC`.

### Opção A — direto via PyCardano

```bash
uv run python -m verificador_dpp.emissor_direto --ator origem
# o tx_hash vai automaticamente para ATOR1_TX no .env, depois:
uv run python -m verificador_dpp.emissor_direto --ator celula
uv run python -m verificador_dpp.emissor_direto --ator pack
uv run python -m verificador_dpp.emissor_direto --ator reciclagem
```

### Opção B — via UVerify SDK

```bash
uv run python -m verificador_dpp.emissor_sdk --ator origem
# o tx_hash vai automaticamente para ATOR1_TX no .env, depois:
uv run python -m verificador_dpp.emissor_sdk --ator celula
uv run python -m verificador_dpp.emissor_sdk --ator pack
uv run python -m verificador_dpp.emissor_sdk --ator reciclagem
```

Os dois caminhos Python usam os mesmos payloads (`_payloads.py`) e a
mesma carteira HD (`wallet.py`).

### Opção C — via UI UVerify (sem código)

1. Abra <https://app.preprod.uverify.io>, conecte a carteira preprod.
2. *Issue Certificate* → template **Digital Product Passport**.
3. Cole os campos do payload do ator (referência: `_payloads.py` ou
   Seção 2.2 do hands-on). Para atores 2-4, preencha
   `cert_*_credential_tx` com os tx hashes anteriores.
4. **Issue** → assine na carteira → copie o tx hash para o `.env`
   como `ATOR<N>_TX=…`.

## Verificação (Seção 3 do hands-on)

Pré-requisitos no `.env`: `TX_HASH_PACK` e — se algum ator foi
emitido via UVerify (B ou C) — `DATA_HASH_PACK`.

```bash
uv run python -m verificador_dpp.verificador
# ou:
uv run python -m verificador_dpp.verificador <txHashPack>
```

`verificador` caminha qualquer cadeia, independente de qual
opção emitiu cada credencial. Para cada tx:

1. Tenta a metadata nativa Cardano via Blockfrost — funciona se
   foi emitida pelo `emissor_direto`.
2. Se não achar `uverify_template_id`, lê o **inline datum** do
   output de script, extrai todas as bytes de 32 bytes (candidatos
   a `data_hash`) e tenta cada um contra a API do UVerify.

Walks `cert_*_credential_tx` references até montar o passaporte
completo (origem → célula → pack).

### Atalho — verificação ad-hoc via URL UVerify (sem código)

Para inspecionar **uma** credencial individual via browser (útil
para demos ou para o consumidor final que só escaneia um QR):

```
https://app.preprod.uverify.io/verify/by-transaction-hash/<TX_HASH>/<DATA_HASH>
https://app.preprod.uverify.io/verify/<DATA_HASH>
https://app.preprod.uverify.io/verify/<DATA_HASH>?serial=<SERIAL>
```

Funciona apenas em credenciais emitidas via UVerify (B ou C). Não
monta a cadeia — para reconstruir origem→célula→pack, use
`verificador`.

## Estrutura

```
starter/
├── pyproject.toml
├── uv.lock
├── .env.example
├── README.md
└── src/verificador_dpp/
    ├── __init__.py
    ├── __main__.py            # help dispatcher
    ├── _payloads.py           # payloads DPP por ator (compartilhado)
    ├── wallet.py              # HD wallet CIP-1852 (compartilhado)
    ├── emissor_direto.py      # Opção A — PyCardano TransactionBuilder
    ├── emissor_sdk.py         # Opção B — uverify-sdk
    ├── verificador.py   # único verificador (cobre A + B + C)
    ├── cliente_blockfrost.py  # wrapper Blockfrost (usado por verificador)
    ├── parser_credencial.py   # parse de metadata UVerify
    ├── relatorio_passaporte.py # relatório pt-BR
    └── modelos.py             # dataclasses CredencialDPP / PassaporteBateria
```

## Dependências principais

- `pycardano` (>= 0.11) — biblioteca canônica Python para Cardano
- `blockfrost-python` (>= 0.6) — cliente REST do Blockfrost
- `uverify-sdk` (>= 0.1.8) — SDK oficial do UVerify
- `python-dotenv` (>= 1.0) — carrega variáveis do `.env`
- `cbor2 < 6` — pin necessário até o `cbor2pure` suportar a 6.x

Versões exatas ficam pinadas no `uv.lock` (commitado para builds reproduzíveis).

## Troubleshooting

Veja a Seção 6 do guia hands-on (`02-mao-na-massa.md`).
