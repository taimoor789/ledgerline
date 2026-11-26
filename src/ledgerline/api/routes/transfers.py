from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from ledgerline.api.deps import idempotent_write
from ledgerline.api.schemas import TransferCreate
from ledgerline.db import Conn
from ledgerline.ledger import service

router = APIRouter(prefix="/transfers", tags=["transfers"])
KeyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post("", status_code=201)
async def create_transfer(
    request: Request, body: TransferCreate, idempotency_key: KeyHeader = None
) -> JSONResponse:
    async def work(conn: Conn) -> dict[str, object]:
        return await service.transfer(
            conn,
            from_account_id=body.from_account_id,
            to_account_id=body.to_account_id,
            amount=body.amount,
            currency=body.currency,
            memo=body.memo,
            effective_at=body.effective_at,
        )

    return await idempotent_write(request, idempotency_key, body, work)
