import asyncio

import asyncpg
import httpx
import pytest

from tests.integration.helpers import account, balance, new_key, post, transfer

pytestmark = pytest.mark.db


@pytest.fixture
async def world(client: httpx.AsyncClient) -> int:
    """Funding source: credit-normal, allowed to go negative."""
    return await account(client, "world", type="equity", allow_negative=True)


async def funded(client: httpx.AsyncClient, world: int, ref: str, amount: int) -> int:
    acct = await account(client, ref)
    assert (await transfer(client, world, acct, amount)).status_code == 201
    return acct


async def test_transfer_moves_money(client: httpx.AsyncClient, world: int) -> None:
    alice = await funded(client, world, "alice", 1_000)
    bob = await account(client, "bob")

    r = await transfer(client, alice, bob, 300)

    assert r.status_code == 201
    body = r.json()
    assert body["postings"] == [
        {"account_id": alice, "direction": "debit", "amount": 300, "currency": "USD"},
        {"account_id": bob, "direction": "credit", "amount": 300, "currency": "USD"},
    ]
    assert "Idempotent-Replayed" not in r.headers
    assert await balance(client, alice) == 700
    assert await balance(client, bob) == 300
    assert await balance(client, world) == -1_000


async def test_replay_returns_same_response(
    client: httpx.AsyncClient, world: int, pool: asyncpg.Pool
) -> None:
    alice = await funded(client, world, "alice", 1_000)
    bob = await account(client, "bob")
    key = new_key()

    first = await transfer(client, alice, bob, 300, key=key)
    second = await transfer(client, alice, bob, 300, key=key)

    assert second.status_code == 201
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    assert await balance(client, alice) == 700
    assert await pool.fetchval("SELECT count(*) FROM ledger.journal_entries") == 2


async def test_key_reuse_with_different_body(client: httpx.AsyncClient, world: int) -> None:
    alice = await funded(client, world, "alice", 1_000)
    bob = await account(client, "bob")
    key = new_key()
    await transfer(client, alice, bob, 300, key=key)

    r = await transfer(client, alice, bob, 301, key=key)

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "idempotency_key_reused"
    assert await balance(client, alice) == 700


async def test_same_key_different_endpoint_rejected(client: httpx.AsyncClient, world: int) -> None:
    alice = await funded(client, world, "alice", 1_000)
    bob = await account(client, "bob")
    key = new_key()
    r1 = await transfer(client, alice, bob, 300, key=key)

    r2 = await post(client, f"/entries/{r1.json()['entry_id']}/reversals", {}, key)

    assert r2.status_code == 422


async def test_missing_key(client: httpx.AsyncClient, world: int) -> None:
    r = await client.post(
        "/transfers",
        json={"from_account_id": 1, "to_account_id": 2, "amount": 1, "currency": "USD"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "idempotency_key_missing"


async def test_validation_error_does_not_consume_key(client: httpx.AsyncClient, world: int) -> None:
    alice = await funded(client, world, "alice", 1_000)
    bob = await account(client, "bob")
    key = new_key()
    bad = {"from_account_id": alice, "to_account_id": bob, "amount": 1.5, "currency": "USD"}

    assert (await post(client, "/transfers", bad, key)).status_code == 422
    assert (await transfer(client, alice, bob, 2, key=key)).status_code == 201


async def test_insufficient_funds_is_stored(client: httpx.AsyncClient, world: int) -> None:
    alice = await funded(client, world, "alice", 100)
    bob = await account(client, "bob")
    key = new_key()

    r1 = await transfer(client, alice, bob, 101, key=key)
    assert r1.status_code == 409
    assert r1.json()["error"]["code"] == "insufficient_funds"

    # Funding later doesn't change the answer for this key: the outcome is final.
    await transfer(client, world, alice, 1_000)
    r2 = await transfer(client, alice, bob, 101, key=key)
    assert r2.status_code == 409
    assert r2.headers["Idempotent-Replayed"] == "true"
    assert await balance(client, bob) == 0


async def test_overdraw_to_exactly_zero_allowed(client: httpx.AsyncClient, world: int) -> None:
    alice = await funded(client, world, "alice", 100)
    bob = await account(client, "bob")
    assert (await transfer(client, alice, bob, 100)).status_code == 201
    assert await balance(client, alice) == 0


@pytest.mark.parametrize(
    ("src_kind", "code", "status"),
    [
        ("missing", "account_not_found", 404),
        ("eur", "currency_mismatch", 422),
        ("asset", "incompatible_accounts", 422),
        ("frozen", "account_not_open", 409),
    ],
)
async def test_transfer_rejections(
    client: httpx.AsyncClient,
    admin: asyncpg.Pool,
    world: int,
    src_kind: str,
    code: str,
    status: int,
) -> None:
    bob = await account(client, "bob")
    if src_kind == "missing":
        src_id = 999_999
    elif src_kind == "eur":
        src_id = await account(client, "e", currency="EUR", allow_negative=True)
    elif src_kind == "asset":
        src_id = await account(client, "cash", type="asset", allow_negative=True)
    else:
        src_id = await funded(client, world, "f", 500)
        await admin.execute("UPDATE ledger.accounts SET status = 'frozen' WHERE id = $1", src_id)

    r = await transfer(client, src_id, bob, 10)

    assert r.status_code == status
    assert r.json()["error"]["code"] == code


async def test_multi_leg_entry(client: httpx.AsyncClient, world: int) -> None:
    cash = await account(client, "cash", type="asset")
    alice = await account(client, "alice")
    fees = await account(client, "fees", type="revenue")
    legs = [
        {"account_id": cash, "direction": "debit", "amount": 1_000},
        {"account_id": alice, "direction": "credit", "amount": 990},
        {"account_id": fees, "direction": "credit", "amount": 10},
    ]

    r = await post(client, "/entries", {"currency": "USD", "legs": legs, "memo": "deposit"})

    assert r.status_code == 201, r.text
    assert await balance(client, cash) == 1_000
    assert await balance(client, alice) == 990
    assert await balance(client, fees) == 10


async def test_unbalanced_entry_rejected_by_api(client: httpx.AsyncClient, world: int) -> None:
    cash = await account(client, "cash", type="asset")
    alice = await account(client, "alice")
    legs = [
        {"account_id": cash, "direction": "debit", "amount": 1_000},
        {"account_id": alice, "direction": "credit", "amount": 999},
    ]
    r = await post(client, "/entries", {"currency": "USD", "legs": legs})
    assert r.status_code == 422
    assert "unbalanced" in r.text


async def test_reversal(client: httpx.AsyncClient, world: int) -> None:
    alice = await funded(client, world, "alice", 1_000)
    bob = await account(client, "bob")
    entry_id = (await transfer(client, alice, bob, 300)).json()["entry_id"]

    r = await post(client, f"/entries/{entry_id}/reversals", {"memo": "customer dispute"})

    assert r.status_code == 201, r.text
    assert r.json()["reverses_entry_id"] == entry_id
    assert await balance(client, alice) == 1_000
    assert await balance(client, bob) == 0

    again = await post(client, f"/entries/{entry_id}/reversals", None)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_reversed"

    of_reversal = await post(client, f"/entries/{r.json()['entry_id']}/reversals", None)
    assert of_reversal.status_code == 409
    assert of_reversal.json()["error"]["code"] == "cannot_reverse_reversal"


async def test_reversal_cannot_overdraw(client: httpx.AsyncClient, world: int) -> None:
    alice = await funded(client, world, "alice", 300)
    bob = await account(client, "bob")
    carol = await account(client, "carol")
    entry_id = (await transfer(client, alice, bob, 300)).json()["entry_id"]
    await transfer(client, bob, carol, 300)

    r = await post(client, f"/entries/{entry_id}/reversals", None)

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "insufficient_funds"


async def test_reverse_missing_entry(client: httpx.AsyncClient) -> None:
    r = await post(client, "/entries/999999/reversals", None)
    assert r.status_code == 404


async def test_duplicate_account_ref(client: httpx.AsyncClient) -> None:
    await account(client, "alice")
    r = await post(
        client, "/accounts", {"external_ref": "alice", "type": "liability", "currency": "USD"}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "duplicate_account"


async def test_balance_unknown_account(client: httpx.AsyncClient) -> None:
    r = await client.get("/accounts/999999/balance")
    assert r.status_code == 404


async def test_backdated_effective_at(client: httpx.AsyncClient, world: int) -> None:
    alice = await account(client, "alice")
    body = {
        "from_account_id": world,
        "to_account_id": alice,
        "amount": 5,
        "currency": "USD",
        "effective_at": "2025-06-30T12:00:00Z",
    }
    r = await post(client, "/transfers", body)
    assert r.status_code == 201
    assert r.json()["effective_at"].startswith("2025-06-30T12:00:00")


async def test_writes_are_audited(
    client: httpx.AsyncClient, world: int, pool: asyncpg.Pool
) -> None:
    alice = await funded(client, world, "alice", 10)
    rows = await pool.fetch("SELECT action, target FROM ledger.audit_log ORDER BY id")
    assert [r["action"] for r in rows] == ["create_account", "create_account", "transfer"]
    assert rows[1]["target"] == f"account:{alice}"


async def test_parallel_duplicates_apply_once(
    client: httpx.AsyncClient, world: int, pool: asyncpg.Pool
) -> None:
    alice = await funded(client, world, "alice", 1_000)
    bob = await account(client, "bob")
    key = new_key()

    results = await asyncio.gather(*(transfer(client, alice, bob, 10, key=key) for _ in range(20)))

    assert {r.status_code for r in results} == {201}
    assert len({r.text for r in results}) == 1
    assert sum("Idempotent-Replayed" not in r.headers for r in results) == 1
    assert await balance(client, bob) == 10
    assert await pool.fetchval("SELECT count(*) FROM ledger.journal_entries") == 2


async def test_parallel_drain_never_overdraws(client: httpx.AsyncClient, world: int) -> None:
    alice = await funded(client, world, "alice", 100)
    bob = await account(client, "bob")

    results = await asyncio.gather(*(transfer(client, alice, bob, 10) for _ in range(20)))

    assert sorted(r.status_code for r in results) == [201] * 10 + [409] * 10
    assert await balance(client, alice) == 0
    assert await balance(client, bob) == 100


async def test_entry_with_missing_account(client: httpx.AsyncClient) -> None:
    cash = await account(client, "cash", type="asset")
    legs = [
        {"account_id": cash, "direction": "debit", "amount": 5},
        {"account_id": 999_999, "direction": "credit", "amount": 5},
    ]
    r = await post(client, "/entries", {"currency": "USD", "legs": legs})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "account_not_found"


async def test_parallel_reversals_apply_once(
    client: httpx.AsyncClient, world: int, pool: asyncpg.Pool
) -> None:
    alice = await funded(client, world, "alice", 1_000)
    bob = await account(client, "bob")
    entry_id = (await transfer(client, alice, bob, 300)).json()["entry_id"]

    results = await asyncio.gather(
        *(post(client, f"/entries/{entry_id}/reversals", None) for _ in range(10))
    )

    assert sorted(r.status_code for r in results) == [201] + [409] * 9
    assert {r.json()["error"]["code"] for r in results if r.status_code == 409} == {
        "already_reversed"
    }
    assert await balance(client, bob) == 0
