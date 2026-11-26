import uuid
from typing import Any

import httpx


def new_key() -> str:
    return uuid.uuid4().hex


async def post(
    client: httpx.AsyncClient, path: str, body: dict[str, Any] | None, key: str | None = None
) -> httpx.Response:
    return await client.post(path, json=body, headers={"Idempotency-Key": key or new_key()})


async def account(
    client: httpx.AsyncClient,
    ref: str,
    *,
    type: str = "liability",
    currency: str = "USD",
    allow_negative: bool = False,
) -> int:
    r = await post(
        client,
        "/accounts",
        {"external_ref": ref, "type": type, "currency": currency, "allow_negative": allow_negative},
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def transfer(
    client: httpx.AsyncClient,
    src: int,
    dst: int,
    amount: int,
    *,
    currency: str = "USD",
    key: str | None = None,
) -> httpx.Response:
    body = {"from_account_id": src, "to_account_id": dst, "amount": amount, "currency": currency}
    return await post(client, "/transfers", body, key)


async def balance(client: httpx.AsyncClient, account_id: int) -> int:
    r = await client.get(f"/accounts/{account_id}/balance")
    assert r.status_code == 200, r.text
    return int(r.json()["balance"])
