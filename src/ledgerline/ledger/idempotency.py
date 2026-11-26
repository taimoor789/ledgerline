"""Exactly-once writes: the key, the ledger rows and the stored response share one transaction."""

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import asyncpg

from ledgerline.db import Conn
from ledgerline.ledger.errors import LedgerError, RequestError

KEY_PATTERN = re.compile(r"^[\x21-\x7e]{1,255}$")

Handler = Callable[[Conn], Awaitable[tuple[int, dict[str, Any], int | None]]]


@dataclass(frozen=True)
class Outcome:
    status: int
    body: dict[str, Any]
    replayed: bool


def validate_key(key: str | None) -> str:
    if key is None:
        raise RequestError(400, "idempotency_key_missing", "Idempotency-Key header is required")
    if not KEY_PATTERN.match(key):
        raise RequestError(400, "idempotency_key_invalid", "Idempotency-Key must be 1-255 ASCII")
    return key


def request_hash(method: str, path: str, body: dict[str, Any]) -> bytes:
    canonical = json.dumps(
        {"method": method.upper(), "path": path, "body": body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).digest()


async def execute(
    pool: asyncpg.Pool, *, client_id: str, key: str, req_hash: bytes, handler: Handler
) -> Outcome:
    async with pool.acquire() as conn, conn.transaction():
        # A concurrent request with the same key blocks here until the first one finishes.
        claimed = await conn.fetchval(
            """
            INSERT INTO ledger.idempotency_keys (client_id, key, request_hash)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
            RETURNING true
            """,
            client_id,
            key,
            req_hash,
        )
        if not claimed:
            row = await conn.fetchrow(
                """
                SELECT request_hash, response_code, response FROM ledger.idempotency_keys
                WHERE client_id = $1 AND key = $2
                """,
                client_id,
                key,
            )
            assert row is not None
            assert row["response_code"] is not None
            if bytes(row["request_hash"]) != req_hash:
                raise RequestError(
                    422, "idempotency_key_reused", "key was used with a different request"
                )
            return Outcome(int(row["response_code"]), dict(row["response"]), replayed=True)

        # Business rejections roll back to the savepoint and are stored as final answers.
        # Anything else aborts the whole transaction, releasing the key for a retry.
        try:
            async with conn.transaction():
                status, body, entry_id = await handler(conn)
        except LedgerError as e:
            status, body, entry_id = e.status, e.body(), None

        await conn.execute(
            """
            UPDATE ledger.idempotency_keys
            SET response_code = $3, response = $4, entry_id = $5
            WHERE client_id = $1 AND key = $2
            """,
            client_id,
            key,
            status,
            body,
            entry_id,
        )
        return Outcome(status, body, replayed=False)
