"""Full-ledger invariant check, used by property tests and as a scheduled job."""

import asyncio
import sys

import asyncpg

from ledgerline.config import Settings
from ledgerline.db import Conn

CHECKS: dict[str, str] = {
    "entry_unbalanced": """
        SELECT entry_id, currency, sum(amount) FROM ledger.postings
        GROUP BY entry_id, currency HAVING sum(amount) <> 0
    """,
    "entry_too_few_postings": """
        SELECT e.id, count(p.id) FROM ledger.journal_entries e
        LEFT JOIN ledger.postings p ON p.entry_id = e.id
        GROUP BY e.id HAVING count(p.id) < 2
    """,
    "balance_mismatch": """
        SELECT b.account_id, b.balance, coalesce(s.total, 0)
        FROM ledger.account_balances b
        LEFT JOIN (
            SELECT account_id, sum(amount) AS total FROM ledger.postings GROUP BY account_id
        ) s ON s.account_id = b.account_id
        WHERE b.balance <> coalesce(s.total, 0)
    """,
    "postings_without_balance_row": """
        SELECT DISTINCT p.account_id FROM ledger.postings p
        LEFT JOIN ledger.account_balances b ON b.account_id = p.account_id
        WHERE b.account_id IS NULL
    """,
    "currency_total_nonzero": """
        SELECT currency, sum(amount) FROM ledger.postings
        GROUP BY currency HAVING sum(amount) <> 0
    """,
    "overdrawn": """
        SELECT a.id, b.balance FROM ledger.accounts a
        JOIN ledger.account_balances b ON b.account_id = a.id
        WHERE NOT a.allow_negative
          AND CASE WHEN a.type IN ('asset', 'expense') THEN b.balance ELSE -b.balance END < 0
    """,
}


async def find_violations(conn: Conn) -> dict[str, list[tuple[object, ...]]]:
    """Return every failing check with its offending rows. Empty means consistent."""
    found: dict[str, list[tuple[object, ...]]] = {}
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        for name, sql in CHECKS.items():
            rows = await conn.fetch(sql)
            if rows:
                found[name] = [tuple(r) for r in rows]
    return found


async def _main() -> int:
    conn = await asyncpg.connect(Settings().database_url)
    try:
        violations = await find_violations(conn)
    finally:
        await conn.close()
    for name, rows in violations.items():
        print(f"{name}: {len(rows)} rows, first: {rows[0]}")
    print("ok" if not violations else "FAILED")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
