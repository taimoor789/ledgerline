"""The API role can append, but never rewrite history or switch the invariant off."""

import asyncpg
import pytest

pytestmark = pytest.mark.db

FORBIDDEN = [
    "UPDATE ledger.postings SET amount = 1",
    "UPDATE ledger.journal_entries SET memo = 'x'",
    "UPDATE ledger.accounts SET status = 'open'",
    "UPDATE ledger.audit_log SET actor = 'x'",
    "DELETE FROM ledger.postings",
    "DELETE FROM ledger.journal_entries",
    "DELETE FROM ledger.audit_log",
    "DELETE FROM ledger.idempotency_keys",
    "DELETE FROM ledger.account_balances",
    "TRUNCATE ledger.postings",
    "TRUNCATE ledger.audit_log",
    "ALTER TABLE ledger.postings DISABLE TRIGGER postings_balanced",
    "ALTER TABLE ledger.journal_entries DISABLE TRIGGER entries_balanced",
    "DROP TRIGGER postings_balanced ON ledger.postings",
    "CREATE TABLE ledger.sneaky (id int)",
    "SET session_replication_role = replica",
    "CREATE OR REPLACE FUNCTION ledger.assert_entry_valid(bigint) RETURNS void"
    " LANGUAGE sql AS 'SELECT'",
]


@pytest.mark.parametrize("sql", FORBIDDEN)
async def test_writer_forbidden(pool: asyncpg.Pool, clean: None, sql: str) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await pool.execute(sql)


async def test_everything_owned_by_ledger_owner(admin: asyncpg.Pool) -> None:
    owners = await admin.fetch(
        """
        SELECT c.relname, pg_get_userbyid(c.relowner) AS owner
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'ledger' AND c.relkind IN ('r', 'p', 'i', 'I', 'S')
        """
    )
    assert owners
    assert {r["owner"] for r in owners} == {"ledger_owner"}


async def test_writer_is_not_superuser_or_owner(pool: asyncpg.Pool) -> None:
    row = await pool.fetchrow(
        "SELECT rolsuper, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    assert row is not None
    assert not any(row.values())
    assert not await pool.fetchval("SELECT pg_has_role(current_user, 'ledger_owner', 'MEMBER')")
