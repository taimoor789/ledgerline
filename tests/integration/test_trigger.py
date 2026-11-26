"""The database rejects bad entries on its own, whatever the application does."""

from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest

from tests.integration.helpers import account

AT = datetime(2026, 1, 1, tzinfo=UTC)
pytestmark = pytest.mark.db


async def raw_entry(
    pool: asyncpg.Pool,
    postings: list[tuple[int, int, str]],
    posting_at: datetime = AT,
) -> int:
    async with pool.acquire() as conn, conn.transaction():
        entry_id = int(
            await conn.fetchval(
                "INSERT INTO ledger.journal_entries (effective_at) VALUES ($1) RETURNING id", AT
            )
        )
        for account_id, amount, currency in postings:
            await conn.execute(
                "INSERT INTO ledger.postings (entry_id, account_id, amount, currency, effective_at)"
                " VALUES ($1, $2, $3, $4, $5)",
                entry_id,
                account_id,
                amount,
                currency,
                posting_at,
            )
    return entry_id


@pytest.fixture
async def accts(client: httpx.AsyncClient) -> dict[str, int]:
    return {
        "a": await account(client, "a"),
        "b": await account(client, "b"),
        "eur": await account(client, "eur", currency="EUR"),
    }


async def test_balanced_entry_commits(pool: asyncpg.Pool, accts: dict[str, int]) -> None:
    await raw_entry(pool, [(accts["a"], 500, "USD"), (accts["b"], -500, "USD")])


async def test_unbalanced_rejected_at_commit(pool: asyncpg.Pool, accts: dict[str, int]) -> None:
    with pytest.raises(asyncpg.CheckViolationError, match="does not balance in USD"):
        await raw_entry(pool, [(accts["a"], 500, "USD"), (accts["b"], -499, "USD")])


async def test_single_posting_rejected(pool: asyncpg.Pool, accts: dict[str, int]) -> None:
    with pytest.raises(asyncpg.CheckViolationError, match="has 1 postings"):
        await raw_entry(pool, [(accts["a"], 500, "USD")])


async def test_entry_without_postings_rejected(pool: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.CheckViolationError, match="has 0 postings"):
        await raw_entry(pool, [])


async def test_zero_sum_across_currencies_rejected(
    pool: asyncpg.Pool, accts: dict[str, int]
) -> None:
    with pytest.raises(asyncpg.CheckViolationError, match="does not balance"):
        await raw_entry(pool, [(accts["a"], 500, "USD"), (accts["eur"], -500, "EUR")])


async def test_posting_currency_must_match_account(
    pool: asyncpg.Pool, accts: dict[str, int]
) -> None:
    with pytest.raises(asyncpg.CheckViolationError, match="wrong currency"):
        await raw_entry(pool, [(accts["a"], 500, "EUR"), (accts["eur"], -500, "EUR")])


async def test_missing_account_rejected(pool: asyncpg.Pool, accts: dict[str, int]) -> None:
    with pytest.raises(asyncpg.CheckViolationError, match="missing account"):
        await raw_entry(pool, [(accts["a"], 500, "USD"), (999_999, -500, "USD")])


async def test_posting_date_must_match_entry(pool: asyncpg.Pool, accts: dict[str, int]) -> None:
    # A valid pair on the entry's date, plus a pair hidden on another date.
    later = AT + timedelta(days=40)
    postings = [(500, AT), (-500, AT), (7, later), (-7, later)]

    async def attempt() -> None:
        async with pool.acquire() as conn, conn.transaction():
            entry_id = await conn.fetchval(
                "INSERT INTO ledger.journal_entries (effective_at) VALUES ($1) RETURNING id", AT
            )
            for amount, at in postings:
                await conn.execute(
                    "INSERT INTO ledger.postings"
                    " (entry_id, account_id, amount, currency, effective_at)"
                    " VALUES ($1, $2, $3, 'USD', $4)",
                    entry_id,
                    accts["a"] if amount > 0 else accts["b"],
                    amount,
                    at,
                )

    with pytest.raises(asyncpg.CheckViolationError, match="effective_at differs"):
        await attempt()


async def test_cannot_add_posting_to_committed_entry(
    pool: asyncpg.Pool, accts: dict[str, int]
) -> None:
    entry_id = await raw_entry(pool, [(accts["a"], 500, "USD"), (accts["b"], -500, "USD")])
    with pytest.raises(asyncpg.CheckViolationError, match="does not balance"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO ledger.postings (entry_id, account_id, amount, currency, effective_at)"
                " VALUES ($1, $2, 1, 'USD', $3)",
                entry_id,
                accts["a"],
                AT,
            )


async def test_trigger_covers_new_partitions(
    admin: asyncpg.Pool, pool: asyncpg.Pool, accts: dict[str, int]
) -> None:
    await admin.execute(
        "CREATE TABLE IF NOT EXISTS ledger.postings_2026_01 PARTITION OF ledger.postings"
        " FOR VALUES FROM ('2026-01-01') TO ('2026-02-01')"
    )
    await admin.execute("ALTER TABLE ledger.postings_2026_01 OWNER TO ledger_owner")
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await raw_entry(pool, [(accts["a"], 500, "USD")])
    finally:
        await admin.execute("DROP TABLE ledger.postings_2026_01")
