import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledgerline.ledger.errors import RequestError
from ledgerline.ledger.idempotency import request_hash, validate_key

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=5),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=3), inner),
    max_leaves=8,
)


def test_hash_is_32_bytes() -> None:
    assert len(request_hash("POST", "/transfers", {"a": 1})) == 32


@given(st.dictionaries(st.text(max_size=4), json_values, max_size=5))
def test_hash_ignores_key_order(body: dict[str, object]) -> None:
    reordered = dict(reversed(list(body.items())))
    assert request_hash("POST", "/x", body) == request_hash("POST", "/x", reordered)


def test_hash_changes_with_any_part() -> None:
    base = request_hash("POST", "/transfers", {"amount": 100})
    assert base != request_hash("POST", "/transfers", {"amount": 101})
    assert base != request_hash("POST", "/entries", {"amount": 100})
    assert base != request_hash("PUT", "/transfers", {"amount": 100})
    assert base == request_hash("post", "/transfers", {"amount": 100})


@pytest.mark.parametrize("key", ["k1", "a" * 255, "uuid-1234_5678.x"])
def test_valid_keys(key: str) -> None:
    assert validate_key(key) == key


@pytest.mark.parametrize(
    ("key", "code"),
    [
        (None, "idempotency_key_missing"),
        ("", "idempotency_key_invalid"),
        ("a" * 256, "idempotency_key_invalid"),
        ("has space", "idempotency_key_invalid"),
        ("café", "idempotency_key_invalid"),
    ],
)
def test_invalid_keys(key: str | None, code: str) -> None:
    with pytest.raises(RequestError) as exc:
        validate_key(key)
    assert exc.value.status == 400
    assert exc.value.code == code
