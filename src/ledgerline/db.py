import json

import asyncpg
from asyncpg.pool import PoolConnectionProxy

# A plain connection or one borrowed from a pool.
type Conn = asyncpg.Connection | PoolConnectionProxy


async def _init(conn: asyncpg.Connection) -> None:
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(dsn: str, min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size, init=_init)
    assert pool is not None
    return pool
