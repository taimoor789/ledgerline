"""Random operation sequences against real Postgres keep every ledger invariant.

Each example commits for real, because deferred triggers only fire at COMMIT.
A Python model predicts every response, and invariants run after every step.
"""

import asyncio
import uuid
from collections import Counter
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx
import pytest
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from ledgerline.api.app import create_app
from ledgerline.db import create_pool
from ledgerline.ledger.consistency import find_violations
from ledgerline.money import MAX_AMOUNT, AccountType, normal_sign
from tests.conftest import Db, truncate_sql

pytestmark = pytest.mark.db

Legs = list[tuple[int, int]]  # (account_id, raw debit-positive amount)

# Outcomes seen across all examples; the test fails if a path was never exercised.
SEEN: Counter[str] = Counter()
MUST_SEE = {
    "/accounts 201",
    "/transfers 201",
    "/transfers 409 insufficient_funds",
    "/transfers 422 incompatible_accounts",
    "/transfers 422 currency_mismatch",
    "/entries 201",
    "/entries 409 insufficient_funds",
    "/entries/{id}/reversals 201",
    "/entries/{id}/reversals 409 already_reversed",
    "/entries/{id}/reversals 409 cannot_reverse_reversal",
    "replay",
    "key_reused",
    "raw_unbalanced_rejected",
}


@dataclass
class Acct:
    id: int
    type: AccountType
    currency: str
    allow_negative: bool
    balance: int = 0  # normal side


@dataclass
class Entry:
    id: int
    legs: Legs
    is_reversal: bool
    reversed: bool = False


@dataclass(frozen=True)
class Sent:
    path: str
    body: dict[str, Any]
    key: str
    status: int
    text: str


def make_machine(db: Db) -> type[RuleBasedStateMachine]:
    class LedgerMachine(RuleBasedStateMachine):
        def __init__(self) -> None:
            super().__init__()
            self.loop = asyncio.new_event_loop()
            self.run(self._truncate())
            self.pool = self.run(create_pool(db.writer, 1, 2))
            app = create_app(pool=self.pool)
            self.client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            )
            self.accounts: list[Acct] = []
            self.entries: list[Entry] = []
            self.sent: list[Sent] = []

        def run[T](self, coro: Coroutine[Any, Any, T]) -> T:
            return self.loop.run_until_complete(coro)

        async def _truncate(self) -> None:
            conn = await asyncpg.connect(db.admin)
            try:
                await conn.execute(truncate_sql())
            finally:
                await conn.close()

        def teardown(self) -> None:
            self.run(self.client.aclose())
            self.run(self.pool.close())
            self.loop.close()

        def send(self, path: str, body: dict[str, Any]) -> httpx.Response:
            key = uuid.uuid4().hex
            r = self.run(self.client.post(path, json=body, headers={"Idempotency-Key": key}))
            self.sent.append(Sent(path, body, key, r.status_code, r.text))
            kind = "/entries/{id}/reversals" if path.endswith("/reversals") else path
            code = r.json().get("error", {}).get("code", "") if r.status_code >= 400 else ""
            SEEN[f"{kind} {r.status_code} {code}".rstrip()] += 1
            return r

        def acct(self, account_id: int) -> Acct:
            return next(a for a in self.accounts if a.id == account_id)

        def overdraws(self, legs: Legs) -> bool:
            deltas: dict[int, int] = {}
            for account_id, raw in legs:
                deltas[account_id] = deltas.get(account_id, 0) + raw
            for account_id, delta in deltas.items():
                a = self.acct(account_id)
                if a.balance + normal_sign(a.type) * delta < 0 and not a.allow_negative:
                    return True
            return False

        def apply(self, legs: Legs) -> None:
            for account_id, raw in legs:
                a = self.acct(account_id)
                a.balance += normal_sign(a.type) * raw

        def expect(self, r: httpx.Response, status: int, code: str | None = None) -> None:
            assert r.status_code == status, r.text
            if code is not None:
                assert r.json()["error"]["code"] == code

        @rule(
            account_type=st.sampled_from(
                [AccountType.LIABILITY, AccountType.EQUITY, AccountType.ASSET]
            ),
            currency=st.sampled_from(["USD", "EUR"]),
            allow_negative=st.booleans(),
        )
        def create_account(
            self, account_type: AccountType, currency: str, allow_negative: bool
        ) -> None:
            r = self.send(
                "/accounts",
                {
                    "external_ref": uuid.uuid4().hex,
                    "type": account_type.value,
                    "currency": currency,
                    "allow_negative": allow_negative,
                },
            )
            self.expect(r, 201)
            self.accounts.append(Acct(r.json()["id"], account_type, currency, allow_negative))

        @precondition(lambda self: len(self.accounts) >= 2)
        @rule(data=st.data(), amount=st.integers(1, 5_000))
        def transfer(self, data: st.DataObject, amount: int) -> None:
            src, dst = data.draw(st.permutations(self.accounts))[:2]
            r = self.send(
                "/transfers",
                {
                    "from_account_id": src.id,
                    "to_account_id": dst.id,
                    "amount": amount,
                    "currency": src.currency,
                },
            )
            sign = normal_sign(src.type)
            legs = [(src.id, -sign * amount), (dst.id, sign * amount)]
            if normal_sign(dst.type) != sign:
                self.expect(r, 422, "incompatible_accounts")
            elif dst.currency != src.currency:
                self.expect(r, 422, "currency_mismatch")
            elif self.overdraws(legs):
                self.expect(r, 409, "insufficient_funds")
            else:
                self.expect(r, 201)
                self.apply(legs)
                self.entries.append(Entry(r.json()["entry_id"], legs, is_reversal=False))

        @precondition(lambda self: len(self.accounts) >= 2)
        @rule(data=st.data())
        def multi_leg_entry(self, data: st.DataObject) -> None:
            currency = data.draw(st.sampled_from(sorted({a.currency for a in self.accounts})))
            candidates = [a for a in self.accounts if a.currency == currency]
            if len(candidates) < 2:
                return
            n = data.draw(st.integers(2, 4))
            chosen = [data.draw(st.sampled_from(candidates)) for _ in range(n)]
            raws = [data.draw(st.integers(-5_000, 5_000).filter(bool)) for _ in range(n - 1)]
            last = -sum(raws)
            if last == 0 or abs(last) > MAX_AMOUNT:
                return
            legs = [(a.id, raw) for a, raw in zip(chosen, [*raws, last], strict=True)]
            r = self.send(
                "/entries",
                {
                    "currency": currency,
                    "legs": [
                        {
                            "account_id": account_id,
                            "direction": "debit" if raw > 0 else "credit",
                            "amount": abs(raw),
                        }
                        for account_id, raw in legs
                    ],
                },
            )
            if self.overdraws(legs):
                self.expect(r, 409, "insufficient_funds")
            else:
                self.expect(r, 201)
                self.apply(legs)
                self.entries.append(Entry(r.json()["entry_id"], legs, is_reversal=False))

        @precondition(lambda self: bool(self.entries))
        @rule(data=st.data())
        def reverse(self, data: st.DataObject) -> None:
            entry = data.draw(st.sampled_from(self.entries))
            r = self.send(f"/entries/{entry.id}/reversals", {})
            negated = [(account_id, -raw) for account_id, raw in entry.legs]
            if entry.is_reversal:
                self.expect(r, 409, "cannot_reverse_reversal")
            elif entry.reversed:
                self.expect(r, 409, "already_reversed")
            elif self.overdraws(negated):
                self.expect(r, 409, "insufficient_funds")
            else:
                self.expect(r, 201)
                self.apply(negated)
                entry.reversed = True
                self.entries.append(Entry(r.json()["entry_id"], negated, is_reversal=True))

        @precondition(lambda self: bool(self.sent))
        @rule(data=st.data())
        def replay(self, data: st.DataObject) -> None:
            s = data.draw(st.sampled_from(self.sent))
            r = self.run(self.client.post(s.path, json=s.body, headers={"Idempotency-Key": s.key}))
            assert r.status_code == s.status
            assert r.text == s.text
            assert r.headers.get("Idempotent-Replayed") == "true"
            SEEN["replay"] += 1

        @precondition(lambda self: bool(self.sent))
        @rule(data=st.data())
        def reuse_key_with_other_body(self, data: st.DataObject) -> None:
            s = data.draw(st.sampled_from(self.sent))
            r = self.run(
                self.client.post(
                    "/entries/1/reversals",
                    json={"memo": uuid.uuid4().hex},
                    headers={"Idempotency-Key": s.key},
                )
            )
            self.expect(r, 422, "idempotency_key_reused")
            SEEN["key_reused"] += 1

        @precondition(lambda self: bool(self.accounts))
        @rule(data=st.data(), amount=st.integers(1, 5_000))
        def raw_unbalanced_insert(self, data: st.DataObject, amount: int) -> None:
            a = data.draw(st.sampled_from(self.accounts))

            async def attempt() -> None:
                async with self.pool.acquire() as conn, conn.transaction():
                    row = await conn.fetchrow(
                        "INSERT INTO ledger.journal_entries (effective_at) VALUES (now())"
                        " RETURNING id, effective_at"
                    )
                    assert row is not None
                    await conn.execute(
                        "INSERT INTO ledger.postings"
                        " (entry_id, account_id, amount, currency, effective_at)"
                        " VALUES ($1, $2, $3, $4, $5)",
                        row["id"],
                        a.id,
                        amount,
                        a.currency,
                        row["effective_at"],
                    )

            with pytest.raises(asyncpg.CheckViolationError):
                self.run(attempt())
            SEEN["raw_unbalanced_rejected"] += 1

        @invariant()
        def ledger_matches_model(self) -> None:
            async def snapshot() -> tuple[dict[str, Any], dict[int, int], int]:
                async with self.pool.acquire() as conn:
                    violations = await find_violations(conn)
                    rows = await conn.fetch(
                        "SELECT account_id, balance FROM ledger.account_balances"
                    )
                    n = await conn.fetchval("SELECT count(*) FROM ledger.journal_entries")
                return violations, {r["account_id"]: r["balance"] for r in rows}, int(n)

            violations, balances, n_entries = self.run(snapshot())
            assert violations == {}
            assert balances == {a.id: normal_sign(a.type) * a.balance for a in self.accounts}
            assert n_entries == len(self.entries)

    return LedgerMachine


def test_ledger_state_machine(db: Db) -> None:
    SEEN.clear()
    run_state_machine_as_test(make_machine(db))
    print(dict(sorted(SEEN.items())))
    assert not MUST_SEE - SEEN.keys(), f"never exercised: {sorted(MUST_SEE - SEEN.keys())}"
