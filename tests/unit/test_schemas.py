import pytest
from pydantic import ValidationError

from ledgerline.api.schemas import AccountCreate, EntryCreate, TransferCreate

TRANSFER = {"from_account_id": 1, "to_account_id": 2, "amount": 100, "currency": "USD"}


def test_transfer_valid() -> None:
    assert TransferCreate.model_validate(TRANSFER).amount == 100


@pytest.mark.parametrize(
    "change",
    [
        {"extra": 1},
        {"amount": 1.5},
        {"amount": "100"},
        {"amount": 0},
        {"currency": "usd"},
        {"currency": "USDX"},
        {"to_account_id": 1},
        {"from_account_id": 0},
        {"effective_at": "2026-01-01T00:00:00"},
    ],
)
def test_transfer_rejects(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TransferCreate.model_validate(TRANSFER | change)


def test_transfer_accepts_aware_datetime() -> None:
    t = TransferCreate.model_validate(TRANSFER | {"effective_at": "2026-01-01T00:00:00Z"})
    assert t.effective_at is not None


def test_entry_balanced() -> None:
    e = EntryCreate.model_validate(
        {
            "currency": "USD",
            "legs": [
                {"account_id": 1, "direction": "debit", "amount": 300},
                {"account_id": 2, "direction": "credit", "amount": 100},
                {"account_id": 3, "direction": "credit", "amount": 200},
            ],
        }
    )
    assert len(e.legs) == 3


@pytest.mark.parametrize(
    "legs",
    [
        [{"account_id": 1, "direction": "debit", "amount": 300}],
        [
            {"account_id": 1, "direction": "debit", "amount": 300},
            {"account_id": 2, "direction": "credit", "amount": 299},
        ],
        [
            {"account_id": 1, "direction": "debit", "amount": 1},
            {"account_id": 2, "direction": "sideways", "amount": 1},
        ],
    ],
)
def test_entry_rejects(legs: list[dict[str, object]]) -> None:
    with pytest.raises(ValidationError):
        EntryCreate.model_validate({"currency": "USD", "legs": legs})


def test_account_rejects_unknown_type_and_fields() -> None:
    base = {"external_ref": "a", "type": "liability", "currency": "USD"}
    assert AccountCreate.model_validate(base).allow_negative is False
    with pytest.raises(ValidationError):
        AccountCreate.model_validate(base | {"type": "cash"})
    with pytest.raises(ValidationError):
        AccountCreate.model_validate(base | {"allow_negative": "yes"})
    with pytest.raises(ValidationError):
        AccountCreate.model_validate(base | {"balance": 5})
