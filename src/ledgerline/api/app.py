from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ledgerline.api.routes import accounts, entries, transfers
from ledgerline.config import Settings
from ledgerline.db import create_pool
from ledgerline.ledger.errors import LedgerError, RequestError


def create_app(settings: Settings | None = None, pool: asyncpg.Pool | None = None) -> FastAPI:
    """App factory. Pass `pool` to share one across tests; otherwise the lifespan owns it."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if pool is not None:
            yield
            return
        s = settings or Settings()
        app.state.client_id = s.client_id
        app.state.pool = await create_pool(s.database_url, s.pool_min_size, s.pool_max_size)
        try:
            yield
        finally:
            await app.state.pool.close()

    app = FastAPI(title="Ledgerline", lifespan=lifespan)
    if pool is not None:
        app.state.pool = pool
        app.state.client_id = settings.client_id if settings else "default"

    @app.exception_handler(RequestError)
    async def _request_error(_: Request, exc: RequestError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(LedgerError)
    async def _ledger_error(_: Request, exc: LedgerError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status)

    app.include_router(accounts.router)
    app.include_router(transfers.router)
    app.include_router(entries.router)
    return app
