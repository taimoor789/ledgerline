from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ledgerline.db import Conn
from ledgerline.ledger import idempotency

Work = Callable[[Conn], Awaitable[dict[str, Any]]]


def pool(request: Request) -> asyncpg.Pool:
    p: asyncpg.Pool = request.app.state.pool
    return p


async def idempotent_write(
    request: Request, key: str | None, payload: BaseModel, work: Work, status: int = 201
) -> JSONResponse:
    valid_key = idempotency.validate_key(key)
    req_hash = idempotency.request_hash(
        request.method, request.url.path, payload.model_dump(mode="json")
    )

    async def handler(conn: Conn) -> tuple[int, dict[str, Any], int | None]:
        body = await work(conn)
        entry_id = body.get("entry_id")
        return status, body, entry_id if isinstance(entry_id, int) else None

    outcome = await idempotency.execute(
        pool(request),
        client_id=request.app.state.client_id,
        key=valid_key,
        req_hash=req_hash,
        handler=handler,
    )
    headers = {"Idempotent-Replayed": "true"} if outcome.replayed else {}
    return JSONResponse(outcome.body, status_code=outcome.status, headers=headers)
