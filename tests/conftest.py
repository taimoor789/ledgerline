import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import asyncpg
import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from hypothesis import HealthCheck, settings
from testcontainers.community.postgres import PostgresContainer

from ledgerline.api.app import create_app
from ledgerline.db import create_pool

IMAGE = "pgvector/pgvector:0.8.7-pg18-trixie"
TABLES = "accounts, journal_entries, postings, account_balances, idempotency_keys, audit_log"

settings.register_profile("ci", max_examples=50, stateful_step_count=30, deadline=None)
settings.register_profile("dev", max_examples=200, stateful_step_count=30, deadline=None)
settings.register_profile(
    "nightly",
    max_examples=1000,
    stateful_step_count=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


@dataclass(frozen=True)
class Db:
    host: str
    port: int

    def url(
        self, user: str = "postgres", password: str = "postgres", db: str = "ledgerline"
    ) -> str:
        return f"postgresql://{user}:{password}@{self.host}:{self.port}/{db}"

    @property
    def admin(self) -> str:
        return self.url()

    @property
    def writer(self) -> str:
        return self.url("ledger_api", "ledger_api")


def migrate(url: str, revision: str = "head", down: bool = False) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    if down:
        command.downgrade(cfg, revision)
    else:
        command.upgrade(cfg, revision)


@pytest.fixture(scope="session")
def db() -> Iterator[Db]:
    with PostgresContainer(
        IMAGE, username="postgres", password="postgres", dbname="ledgerline", driver=None
    ) as pg:
        d = Db(pg.get_container_host_ip(), int(pg.get_exposed_port(5432)))
        migrate(d.admin)
        with psycopg.connect(d.admin, autocommit=True) as conn:
            conn.execute("CREATE ROLE ledger_api LOGIN PASSWORD 'ledger_api' IN ROLE ledger_writer")
        yield d


def truncate_sql() -> str:
    tables = ", ".join(f"ledger.{t.strip()}" for t in TABLES.split(","))
    return f"TRUNCATE {tables} RESTART IDENTITY CASCADE"


@pytest.fixture(scope="session")
async def admin(db: Db) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(db.admin, 1, 2)
    yield pool
    await pool.close()


@pytest.fixture(scope="session")
async def pool(db: Db) -> AsyncIterator[asyncpg.Pool]:
    p = await create_pool(db.writer, 1, 25)
    yield p
    await p.close()


@pytest.fixture
async def clean(admin: asyncpg.Pool) -> None:
    await admin.execute(truncate_sql())


@pytest.fixture
async def client(pool: asyncpg.Pool, clean: None) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app(pool=pool))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
