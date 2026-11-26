from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from ledgerline.api.deps import idempotent_write
from ledgerline.api.schemas import EntryCreate, ReversalCreate
from ledgerline.db import Conn
from ledgerline.ledger import service
from ledgerline.money import signed

router = APIRouter(prefix="/entries", tags=["entries"])
KeyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post("", status_code=201)
async def create_entry(
    request: Request, body: EntryCreate, idempotency_key: KeyHeader = None
) -> JSONResponse:
    legs = [
        service.Leg(leg.account_id, signed(leg.direction, leg.amount), body.currency)
        for leg in body.legs
    ]

    async def work(conn: Conn) -> dict[str, object]:
        return await service.post_entry(
            conn, legs=legs, memo=body.memo, effective_at=body.effective_at, action="entry"
        )

    return await idempotent_write(request, idempotency_key, body, work)


@router.post("/{entry_id}/reversals", status_code=201)
async def reverse_entry(
    request: Request,
    entry_id: int,
    body: ReversalCreate | None = None,
    idempotency_key: KeyHeader = None,
) -> JSONResponse:
    payload = body or ReversalCreate()

    async def work(conn: Conn) -> dict[str, object]:
        return await service.reverse(conn, entry_id=entry_id, memo=payload.memo)

    return await idempotent_write(request, idempotency_key, payload, work)
