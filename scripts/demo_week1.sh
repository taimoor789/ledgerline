#!/bin/sh
# Week 1 demo. Needs a fresh database and the API on :8000.
API=localhost:8000
j() { curl -s -i -X POST "$API$1" -H 'content-type: application/json' -H "Idempotency-Key: $2" -d "$3" | grep -iE '^HTTP|^idempotent|^\{' ; echo; }
echo "== accounts"; j /accounts acct-world '{"external_ref":"world","type":"equity","currency":"USD","allow_negative":true}'
j /accounts acct-alice '{"external_ref":"alice","type":"liability","currency":"USD"}'
j /accounts acct-bob '{"external_ref":"bob","type":"liability","currency":"USD"}'
echo "== fund alice"; j /transfers fund-1 '{"from_account_id":1,"to_account_id":2,"amount":10000,"currency":"USD"}'
echo "== transfer alice -> bob, key t-1"; j /transfers t-1 '{"from_account_id":2,"to_account_id":3,"amount":2500,"currency":"USD"}'
echo "== retry same key, same body"; j /transfers t-1 '{"from_account_id":2,"to_account_id":3,"amount":2500,"currency":"USD"}'
echo "== same key, different amount"; j /transfers t-1 '{"from_account_id":2,"to_account_id":3,"amount":9999,"currency":"USD"}'
echo "== balances"; for a in 1 2 3; do curl -s $API/accounts/$a/balance; echo; done
echo "== unbalanced entry via API"; j /entries e-bad '{"currency":"USD","legs":[{"account_id":2,"direction":"debit","amount":100},{"account_id":3,"direction":"credit","amount":99}]}'
echo "== same entry straight into Postgres as the API role"
PGPASSWORD=ledger_api psql -h localhost -p 55432 -U ledger_api -d ledgerline <<'SQL'
BEGIN;
INSERT INTO ledger.journal_entries (effective_at) VALUES (now());
INSERT INTO ledger.postings (entry_id, account_id, amount, currency, effective_at)
  SELECT max(id), 2, 100, 'USD', now() FROM ledger.journal_entries;
INSERT INTO ledger.postings (entry_id, account_id, amount, currency, effective_at)
  SELECT max(id), 3, -99, 'USD', now() FROM ledger.journal_entries;
COMMIT;
SQL
