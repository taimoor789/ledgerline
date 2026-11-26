from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from ledgerline.api.deps import idempotent_write, pool
from ledgerline.api.schemas import AccountCreate
from ledgerline.db import Conn
from ledgerline.ledger import service

router = APIRouter(prefix="/accounts", tags=["accounts"])
KeyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post("", status_code=201)
async def create_account(
    request: Request, body: AccountCreate, idempotency_key: KeyHeader = None
) -> JSONResponse:
    async def work(conn: Conn) -> dict[str, object]:
        return await service.create_account(
            conn,
            external_ref=body.external_ref,
            account_type=body.type,
            currency=body.currency,
            allow_negative=body.allow_negative,
        )

    return await idempotent_write(request, idempotency_key, body, work)


@router.get("/{account_id}/balance")
async def get_balance(request: Request, account_id: int) -> JSONResponse:
    async with pool(request).acquire() as conn:
        return JSONResponse(await service.get_balance(conn, account_id))
