-- Roles are cluster-wide, so create them only if missing.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ledger_owner') THEN
    CREATE ROLE ledger_owner NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ledger_writer') THEN
    CREATE ROLE ledger_writer NOLOGIN;
  END IF;
END $$;

CREATE SCHEMA ledger AUTHORIZATION ledger_owner;

-- Everything below is owned by ledger_owner, never by the API role.
SET LOCAL ROLE ledger_owner;
SET LOCAL search_path = ledger;

CREATE TYPE account_type AS ENUM ('asset', 'liability', 'equity', 'revenue', 'expense');

CREATE TABLE accounts (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  external_ref   text UNIQUE NOT NULL,
  type           account_type NOT NULL,
  currency       char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'frozen', 'closed')),
  allow_negative boolean NOT NULL DEFAULT false,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE journal_entries (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  memo              text,
  effective_at      timestamptz NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  reverses_entry_id bigint REFERENCES journal_entries (id)
);

-- An entry can be reversed at most once.
CREATE UNIQUE INDEX journal_entries_one_reversal
  ON journal_entries (reverses_entry_id) WHERE reverses_entry_id IS NOT NULL;

-- Amounts are signed minor units, debit-positive.
CREATE TABLE postings (
  id           bigint GENERATED ALWAYS AS IDENTITY,
  entry_id     bigint NOT NULL,
  account_id   bigint NOT NULL,
  amount       bigint NOT NULL CHECK (amount <> 0),
  currency     char(3) NOT NULL,
  effective_at timestamptz NOT NULL,
  PRIMARY KEY (id, effective_at)
) PARTITION BY RANGE (effective_at);

CREATE TABLE postings_default PARTITION OF postings DEFAULT;

CREATE INDEX postings_entry_id ON postings (entry_id);
CREATE INDEX postings_account_time ON postings (account_id, effective_at DESC);

-- Raw debit-positive sum of the account's postings.
CREATE TABLE account_balances (
  account_id bigint PRIMARY KEY REFERENCES accounts (id),
  balance    bigint NOT NULL,
  version    bigint NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL
);

-- Response columns are set in the same transaction that claims the key.
-- json, not jsonb, so replays return byte-identical bodies.
CREATE TABLE idempotency_keys (
  client_id     text NOT NULL,
  key           text NOT NULL,
  request_hash  bytea NOT NULL CHECK (length(request_hash) = 32),
  response_code int,
  response      json,
  entry_id      bigint REFERENCES journal_entries (id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, key)
);

CREATE TABLE audit_log (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor      text NOT NULL,
  action     text NOT NULL,
  target     text,
  payload    jsonb NOT NULL,
  trace_id   text,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Invariant: >= 2 postings, accounts exist, currencies match, zero sum per currency.
CREATE FUNCTION assert_entry_valid(p_entry_id bigint) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  v_effective timestamptz;
  v_count     int;
  v_currency  text;
BEGIN
  SELECT effective_at INTO v_effective FROM ledger.journal_entries WHERE id = p_entry_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'entry % does not exist', p_entry_id USING ERRCODE = '23503';
  END IF;

  SELECT count(*) INTO v_count
  FROM ledger.postings
  WHERE entry_id = p_entry_id AND effective_at = v_effective;
  IF v_count < 2 THEN
    RAISE EXCEPTION 'entry % has % postings, needs at least 2', p_entry_id, v_count
      USING ERRCODE = '23514';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM ledger.postings p
    LEFT JOIN ledger.accounts a ON a.id = p.account_id
    WHERE p.entry_id = p_entry_id AND p.effective_at = v_effective
      AND (a.id IS NULL OR a.currency <> p.currency)
  ) THEN
    RAISE EXCEPTION 'entry % has a posting with a missing account or wrong currency', p_entry_id
      USING ERRCODE = '23514';
  END IF;

  SELECT currency INTO v_currency
  FROM ledger.postings
  WHERE entry_id = p_entry_id AND effective_at = v_effective
  GROUP BY currency
  HAVING sum(amount) <> 0
  LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION 'entry % does not balance in %', p_entry_id, v_currency
      USING ERRCODE = '23514';
  END IF;
END $$;

CREATE FUNCTION check_posting() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.effective_at IS DISTINCT FROM
     (SELECT effective_at FROM ledger.journal_entries WHERE id = NEW.entry_id) THEN
    RAISE EXCEPTION 'posting % effective_at differs from entry %', NEW.id, NEW.entry_id
      USING ERRCODE = '23514';
  END IF;
  PERFORM ledger.assert_entry_valid(NEW.entry_id);
  RETURN NULL;
END $$;

CREATE FUNCTION check_entry() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM ledger.assert_entry_valid(NEW.id);
  RETURN NULL;
END $$;

-- Deferred: runs at COMMIT, after all postings of the entry exist.
CREATE CONSTRAINT TRIGGER postings_balanced
  AFTER INSERT ON postings
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION check_posting();

-- Catches entries with no postings at all.
CREATE CONSTRAINT TRIGGER entries_balanced
  AFTER INSERT ON journal_entries
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION check_entry();

-- Append-only: the writer can insert and read, and update only working state.
GRANT USAGE ON SCHEMA ledger TO ledger_writer;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA ledger TO ledger_writer;
GRANT UPDATE ON account_balances, idempotency_keys TO ledger_writer;

RESET ROLE;
RESET search_path;
