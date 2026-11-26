from typing import Annotated, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from ledgerline.money import AccountType, Amount, Currency, Direction

Id = Annotated[StrictInt, Field(ge=1)]
Memo = Annotated[StrictStr, Field(max_length=500)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountCreate(_Strict):
    external_ref: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    type: AccountType
    currency: Currency
    allow_negative: StrictBool = False


class TransferCreate(_Strict):
    from_account_id: Id
    to_account_id: Id
    amount: Amount
    currency: Currency
    memo: Memo | None = None
    effective_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if self.from_account_id == self.to_account_id:
            raise ValueError("from_account_id and to_account_id must differ")
        return self


class LegIn(_Strict):
    account_id: Id
    direction: Direction
    amount: Amount


class EntryCreate(_Strict):
    currency: Currency
    legs: Annotated[list[LegIn], Field(min_length=2, max_length=50)]
    memo: Memo | None = None
    effective_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _balanced(self) -> Self:
        debits = sum(leg.amount for leg in self.legs if leg.direction is Direction.DEBIT)
        credits = sum(leg.amount for leg in self.legs if leg.direction is Direction.CREDIT)
        if debits != credits:
            raise ValueError(f"unbalanced entry: debits {debits} != credits {credits}")
        return self


class ReversalCreate(_Strict):
    memo: Memo | None = None
