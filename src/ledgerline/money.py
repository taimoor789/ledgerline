from enum import StrEnum
from typing import Annotated

from pydantic import Field, StrictInt, StrictStr

# Caps every amount well below 2**53, so JSON clients never lose precision.
MAX_AMOUNT = 10**12

Amount = Annotated[StrictInt, Field(ge=1, le=MAX_AMOUNT)]
Currency = Annotated[StrictStr, Field(pattern=r"^[A-Z]{3}$")]


class AccountType(StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


class Direction(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


_DEBIT_NORMAL = {AccountType.ASSET, AccountType.EXPENSE}


def normal_sign(account_type: AccountType) -> int:
    """+1 for debit-normal accounts, -1 for credit-normal ones."""
    return 1 if account_type in _DEBIT_NORMAL else -1


def normal_balance(account_type: AccountType, raw: int) -> int:
    """Balance as the account holder sees it, from the raw debit-positive sum."""
    return normal_sign(account_type) * raw


def signed(direction: Direction, amount: int) -> int:
    return amount if direction is Direction.DEBIT else -amount


def direction_of(raw: int) -> Direction:
    return Direction.DEBIT if raw > 0 else Direction.CREDIT
