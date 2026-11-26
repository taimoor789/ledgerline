import pytest
from pydantic import TypeAdapter, ValidationError

from ledgerline.money import (
    MAX_AMOUNT,
    AccountType,
    Amount,
    Direction,
    direction_of,
    normal_balance,
    normal_sign,
    signed,
)

amount = TypeAdapter(Amount)


@pytest.mark.parametrize("value", [1, 500, MAX_AMOUNT])
def test_amount_accepts_positive_ints(value: int) -> None:
    assert amount.validate_python(value) == value


@pytest.mark.parametrize("value", [0, -1, MAX_AMOUNT + 1, 1.0, 10.5, "100", True, None])
def test_amount_rejects_everything_else(value: object) -> None:
    with pytest.raises(ValidationError):
        amount.validate_python(value)


@pytest.mark.parametrize("raw", ["1.0", "true", '"5"', "1e3"])
def test_amount_rejects_non_int_json(raw: str) -> None:
    with pytest.raises(ValidationError):
        amount.validate_json(raw)


def test_amount_cap_is_safe_for_json_clients() -> None:
    assert MAX_AMOUNT < 2**53


@pytest.mark.parametrize(
    ("account_type", "sign"),
    [
        (AccountType.ASSET, 1),
        (AccountType.EXPENSE, 1),
        (AccountType.LIABILITY, -1),
        (AccountType.EQUITY, -1),
        (AccountType.REVENUE, -1),
    ],
)
def test_normal_sign(account_type: AccountType, sign: int) -> None:
    assert normal_sign(account_type) == sign
    assert normal_balance(account_type, 700) == sign * 700


def test_signed_and_direction_roundtrip() -> None:
    assert signed(Direction.DEBIT, 5) == 5
    assert signed(Direction.CREDIT, 5) == -5
    assert direction_of(5) is Direction.DEBIT
    assert direction_of(-5) is Direction.CREDIT
