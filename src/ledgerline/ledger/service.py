"""The only code that writes ledger rows. Callers own the transaction."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from ledgerline.db import Conn
from ledgerline.ledger.errors import (
    AccountNotFound,
    AccountNotOpen,
    AlreadyReversed,
    CannotReverseReversal,
    CurrencyMismatch,
    DuplicateAccount,
    EntryNotFound,
    IncompatibleAccounts,
    InsufficientFunds,
)
from ledgerline.money import AccountType, direction_of, normal_balance, normal_sign

ACTOR = "api"


@dataclass(frozen=True)
class Leg:
    account_id: int
    amount: int  # signed, debit-positive
    currency: str


@dataclass(frozen=True)
class _Locked:
    type: AccountType
    currency: str
    status: str
    allow_negative: bool
    balance: int


async def _lock_accounts(conn: Conn, ids: list[int]) -> dict[int, _Locked]:
    # Fixed lock order (ascending id) so two transfers can't deadlock each other.
    rows = await conn.fetch(
        """
        SELECT a.id, a.type::text AS type, a.currency, a.status, a.allow_negative, b.balance
        FROM ledger.account_balances b
        JOIN ledger.accounts a ON a.id = b.account_id
        WHERE b.account_id = ANY($1::bigint[])
        ORDER BY b.account_id
        FOR UPDATE OF b
        """,
        sorted(ids),
    )
    locked = {
        int(r["id"]): _Locked(
            type=AccountType(r["type"]),
            currency=str(r["currency"]),
            status=str(r["status"]),
            allow_negative=bool(r["allow_negative"]),
            balance=int(r["balance"]),
        )
        for r in rows
    }
    missing = sorted(set(ids) - locked.keys())
    if missing:
        raise AccountNotFound(f"account {missing[0]} not found")
    return locked


def _check(locked: dict[int, _Locked], legs: list[Leg], deltas: dict[int, int]) -> None:
    for account_id in sorted(locked):
        if locked[account_id].status != "open":
            raise AccountNotOpen(f"account {account_id} is {locked[account_id].status}")
    for leg in legs:
        if locked[leg.account_id].currency != leg.currency:
            raise CurrencyMismatch(
                f"account {leg.account_id} is {locked[leg.account_id].currency}, not {leg.currency}"
            )
    for account_id in sorted(deltas):
        acct = locked[account_id]
        after = normal_balance(acct.type, acct.balance + deltas[account_id])
        if after < 0 and not acct.allow_negative:
            raise InsufficientFunds(f"account {account_id} would go to {after}")


async def post_entry(
    conn: Conn,
    *,
    legs: list[Leg],
    memo: str | None,
    effective_at: datetime | None,
    action: str,
    reverses_entry_id: int | None = None,
) -> dict[str, Any]:
    deltas: dict[int, int] = defaultdict(int)
    for leg in legs:
        deltas[leg.account_id] += leg.amount

    locked = await _lock_accounts(conn, list(deltas))
    _check(locked, legs, deltas)

    try:
        entry = await conn.fetchrow(
            """
            INSERT INTO ledger.journal_entries (memo, effective_at, reverses_entry_id)
            VALUES ($1, COALESCE($2, now()), $3)
            RETURNING id, effective_at, created_at
            """,
            memo,
            effective_at,
            reverses_entry_id,
        )
    except asyncpg.UniqueViolationError:
        raise AlreadyReversed(f"entry {reverses_entry_id} is already reversed") from None
    assert entry is not None
    entry_id = int(entry["id"])

    await conn.execute(
        """
        INSERT INTO ledger.postings (entry_id, account_id, amount, currency, effective_at)
        SELECT $1, t.account_id, t.amount, t.currency, $5
        FROM unnest($2::bigint[], $3::bigint[], $4::text[]) AS t(account_id, amount, currency)
        """,
        entry_id,
        [leg.account_id for leg in legs],
        [leg.amount for leg in legs],
        [leg.currency for leg in legs],
        entry["effective_at"],
    )
    await conn.execute(
        """
        UPDATE ledger.account_balances b
        SET balance = b.balance + d.delta, version = b.version + 1, updated_at = now()
        FROM unnest($1::bigint[], $2::bigint[]) AS d(account_id, delta)
        WHERE b.account_id = d.account_id
        """,
        list(deltas),
        list(deltas.values()),
    )

    body = {
        "entry_id": entry_id,
        "memo": memo,
        "effective_at": entry["effective_at"].isoformat(),
        "created_at": entry["created_at"].isoformat(),
        "reverses_entry_id": reverses_entry_id,
        "postings": [
            {
                "account_id": leg.account_id,
                "direction": direction_of(leg.amount).value,
                "amount": abs(leg.amount),
                "currency": leg.currency,
            }
            for leg in legs
        ],
    }
    await _audit(conn, action, f"entry:{entry_id}", body)
    return body


async def _account_types(conn: Conn, ids: list[int]) -> dict[int, AccountType]:
    # Unlocked read is safe: the writer role can't UPDATE accounts, so type never changes.
    rows = await conn.fetch(
        "SELECT id, type::text AS type FROM ledger.accounts WHERE id = ANY($1::bigint[])", ids
    )
    found = {int(r["id"]): AccountType(r["type"]) for r in rows}
    for account_id in ids:
        if account_id not in found:
            raise AccountNotFound(f"account {account_id} not found")
    return found


async def transfer(
    conn: Conn,
    *,
    from_account_id: int,
    to_account_id: int,
    amount: int,
    currency: str,
    memo: str | None,
    effective_at: datetime | None,
) -> dict[str, Any]:
    """Move `amount` of normal-side balance from one account to another of the same side."""
    types = await _account_types(conn, [from_account_id, to_account_id])
    sign = normal_sign(types[from_account_id])
    if normal_sign(types[to_account_id]) != sign:
        raise IncompatibleAccounts("transfers need accounts on the same normal side; use /entries")
    legs = [
        Leg(from_account_id, -sign * amount, currency),
        Leg(to_account_id, sign * amount, currency),
    ]
    return await post_entry(
        conn, legs=legs, memo=memo, effective_at=effective_at, action="transfer"
    )


async def _raise_if_reversed(conn: Conn, entry_id: int) -> None:
    if await conn.fetchval(
        "SELECT 1 FROM ledger.journal_entries WHERE reverses_entry_id = $1", entry_id
    ):
        raise AlreadyReversed(f"entry {entry_id} is already reversed")


async def reverse(conn: Conn, *, entry_id: int, memo: str | None) -> dict[str, Any]:
    original = await conn.fetchrow(
        "SELECT effective_at, reverses_entry_id FROM ledger.journal_entries WHERE id = $1",
        entry_id,
    )
    if original is None:
        raise EntryNotFound(f"entry {entry_id} not found")
    if original["reverses_entry_id"] is not None:
        raise CannotReverseReversal(f"entry {entry_id} is itself a reversal")
    await _raise_if_reversed(conn, entry_id)

    postings = await conn.fetch(
        """
        SELECT account_id, amount, currency FROM ledger.postings
        WHERE entry_id = $1 AND effective_at = $2
        ORDER BY id
        """,
        entry_id,
        original["effective_at"],
    )
    legs = [Leg(int(p["account_id"]), -int(p["amount"]), str(p["currency"])) for p in postings]

    # A concurrent reversal holds these same locks until it commits, so re-check once we
    # hold them. Otherwise the loser can fail on funds and store the wrong final answer.
    await _lock_accounts(conn, sorted({leg.account_id for leg in legs}))
    await _raise_if_reversed(conn, entry_id)

    return await post_entry(
        conn,
        legs=legs,
        memo=memo if memo is not None else f"reversal of entry {entry_id}",
        effective_at=None,
        action="reverse",
        reverses_entry_id=entry_id,
    )


async def create_account(
    conn: Conn,
    *,
    external_ref: str,
    account_type: AccountType,
    currency: str,
    allow_negative: bool,
) -> dict[str, Any]:
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO ledger.accounts (external_ref, type, currency, allow_negative)
            VALUES ($1, $2::ledger.account_type, $3, $4)
            RETURNING id, status, created_at
            """,
            external_ref,
            account_type.value,
            currency,
            allow_negative,
        )
    except asyncpg.UniqueViolationError:
        raise DuplicateAccount(f"account {external_ref!r} already exists") from None
    assert row is not None
    account_id = int(row["id"])
    await conn.execute(
        "INSERT INTO ledger.account_balances (account_id, balance, updated_at)"
        " VALUES ($1, 0, now())",
        account_id,
    )
    body = {
        "id": account_id,
        "external_ref": external_ref,
        "type": account_type.value,
        "currency": currency,
        "status": str(row["status"]),
        "allow_negative": allow_negative,
        "created_at": row["created_at"].isoformat(),
    }
    await _audit(conn, "create_account", f"account:{account_id}", body)
    return body


async def get_balance(conn: Conn, account_id: int) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT a.type::text AS type, a.currency, b.balance, b.updated_at
        FROM ledger.accounts a JOIN ledger.account_balances b ON b.account_id = a.id
        WHERE a.id = $1
        """,
        account_id,
    )
    if row is None:
        raise AccountNotFound(f"account {account_id} not found")
    return {
        "account_id": account_id,
        "currency": str(row["currency"]),
        "balance": normal_balance(AccountType(row["type"]), int(row["balance"])),
        "updated_at": row["updated_at"].isoformat(),
    }


async def _audit(conn: Conn, action: str, target: str, payload: Any) -> None:
    await conn.execute(
        "INSERT INTO ledger.audit_log (actor, action, target, payload) VALUES ($1, $2, $3, $4)",
        ACTOR,
        action,
        target,
        payload,
    )
