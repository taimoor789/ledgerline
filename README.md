# Ledgerline

A PostgreSQL double-entry ledger service with idempotent transfers and strict balance invariants, plus a read-only investigation agent over synthetic AML transaction data.

All data is synthetic (IBM AML synthetic transactions). No real customer data.

Status: in progress. Week 1 (ledger core) is done; results and design notes land with each milestone.

## Ledger core

- Money is `bigint` minor units. No floats, including JSON.
- Postings are signed and debit-positive. A deferred constraint trigger checks every entry at commit: at least two postings, matching currencies, zero sum per currency.
- Every write takes an `Idempotency-Key`. The key, the ledger rows and the stored response commit in one transaction. Retries replay the stored response; reusing a key with a different body returns 422.
- The API role can insert and read, never update or delete history. Corrections are reversing entries.

## Run locally

Needs Docker and [uv](https://docs.astral.sh/uv/).

```sh
cp .env.example .env
docker compose up -d --wait
uv run alembic upgrade head
uv run uvicorn --factory ledgerline.api.app:create_app
```

Then try a transfer, and retry it with the same key:

```sh
curl -X POST localhost:8000/accounts -H 'Idempotency-Key: a1' -H 'content-type: application/json' \
  -d '{"external_ref":"world","type":"equity","currency":"USD","allow_negative":true}'
curl -X POST localhost:8000/accounts -H 'Idempotency-Key: a2' -H 'content-type: application/json' \
  -d '{"external_ref":"alice","type":"liability","currency":"USD"}'
curl -i -X POST localhost:8000/transfers -H 'Idempotency-Key: t1' -H 'content-type: application/json' \
  -d '{"from_account_id":1,"to_account_id":2,"amount":2500,"currency":"USD"}'
```

## Tests

```sh
uv run pytest                          # unit, property and integration (starts a Postgres container)
HYPOTHESIS_PROFILE=ci uv run pytest    # smaller property runs, as in CI
uv run python -m ledgerline.ledger.consistency   # full-ledger invariant check
```
