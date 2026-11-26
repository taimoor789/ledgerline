import asyncpg
import httpx
import pytest

from ledgerline.api.app import create_app
from ledgerline.config import Settings
from ledgerline.ledger import consistency
from tests.conftest import Db
from tests.integration.helpers import account, transfer

pytestmark = pytest.mark.db


async def test_app_owns_its_pool(db: Db) -> None:
    app = create_app(Settings(database_url=db.writer))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            assert (await c.get("/accounts/999999/balance")).status_code == 404
    assert app.state.pool.is_closing()


async def test_checker_passes_clean_ledger(client: httpx.AsyncClient, pool: asyncpg.Pool) -> None:
    world = await account(client, "world", type="equity", allow_negative=True)
    alice = await account(client, "alice")
    await transfer(client, world, alice, 50)
    async with pool.acquire() as conn:
        assert await consistency.find_violations(conn) == {}


async def test_checker_flags_tampering(
    client: httpx.AsyncClient, admin: asyncpg.Pool, pool: asyncpg.Pool
) -> None:
    world = await account(client, "world", type="equity", allow_negative=True)
    alice = await account(client, "alice")
    await transfer(client, world, alice, 50)
    # Tamper as superuser, bypassing the trigger and the write path.
    await admin.execute("SET session_replication_role = replica")
    await admin.execute(
        "UPDATE ledger.account_balances SET balance = balance - 1 WHERE account_id = $1", alice
    )
    await admin.execute(
        "UPDATE ledger.postings SET amount = amount - 1 WHERE account_id = $1", world
    )
    await admin.execute("RESET session_replication_role")

    async with pool.acquire() as conn:
        found = await consistency.find_violations(conn)

    assert set(found) == {"entry_unbalanced", "balance_mismatch", "currency_total_nonzero"}


async def test_checker_cli(
    db: Db, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEDGERLINE_DATABASE_URL", db.writer)
    assert await consistency._main() == 0
