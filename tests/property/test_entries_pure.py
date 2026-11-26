from hypothesis import given
from hypothesis import strategies as st

from ledgerline.api.schemas import EntryCreate
from ledgerline.money import MAX_AMOUNT, AccountType, Direction, normal_balance, signed

amounts = st.integers(1, 10_000)


@st.composite
def balanced_legs(draw: st.DrawFn) -> list[dict[str, object]]:
    debits = draw(st.lists(amounts, min_size=1, max_size=5))
    total = sum(debits)
    cuts = (
        sorted(draw(st.lists(st.integers(1, total - 1), max_size=3, unique=True)))
        if total > 1
        else []
    )
    credits = [b - a for a, b in zip([0, *cuts], [*cuts, total], strict=True)]
    legs = [{"account_id": i + 1, "direction": "debit", "amount": a} for i, a in enumerate(debits)]
    legs += [
        {"account_id": 100 + i, "direction": "credit", "amount": a} for i, a in enumerate(credits)
    ]
    return draw(st.permutations(legs))


@given(balanced_legs())
def test_balanced_legs_always_accepted_and_sum_to_zero(legs: list[dict[str, object]]) -> None:
    entry = EntryCreate.model_validate({"currency": "USD", "legs": legs})
    assert sum(signed(leg.direction, leg.amount) for leg in entry.legs) == 0


@given(balanced_legs(), st.integers(1, 1_000))
def test_any_skew_is_rejected(legs: list[dict[str, object]], skew: int) -> None:
    first = dict(legs[0])
    amount = int(str(first["amount"])) + skew
    if amount > MAX_AMOUNT:
        return
    first["amount"] = amount
    try:
        EntryCreate.model_validate({"currency": "USD", "legs": [first, *legs[1:]]})
    except ValueError:
        return
    raise AssertionError("skewed entry accepted")


@given(balanced_legs())
def test_reversal_legs_cancel_exactly(legs: list[dict[str, object]]) -> None:
    entry = EntryCreate.model_validate({"currency": "USD", "legs": legs})
    raw = [signed(leg.direction, leg.amount) for leg in entry.legs]
    reversed_raw = [-a for a in raw]
    assert all(a + b == 0 for a, b in zip(raw, reversed_raw, strict=True))


@given(st.sampled_from(list(AccountType)), st.integers(-(10**15), 10**15))
def test_normal_balance_is_an_involution(account_type: AccountType, raw: int) -> None:
    assert normal_balance(account_type, normal_balance(account_type, raw)) == raw


@given(st.sampled_from(list(Direction)), amounts)
def test_signed_magnitude(direction: Direction, amount: int) -> None:
    assert abs(signed(direction, amount)) == amount
