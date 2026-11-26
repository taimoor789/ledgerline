from typing import Any


class LedgerError(Exception):
    """A deterministic business rejection. Stored under the idempotency key."""

    status = 409
    code = "ledger_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


class AccountNotFound(LedgerError):
    status = 404
    code = "account_not_found"


class EntryNotFound(LedgerError):
    status = 404
    code = "entry_not_found"


class AccountNotOpen(LedgerError):
    code = "account_not_open"


class InsufficientFunds(LedgerError):
    code = "insufficient_funds"


class CurrencyMismatch(LedgerError):
    status = 422
    code = "currency_mismatch"


class IncompatibleAccounts(LedgerError):
    status = 422
    code = "incompatible_accounts"


class DuplicateAccount(LedgerError):
    code = "duplicate_account"


class AlreadyReversed(LedgerError):
    code = "already_reversed"


class CannotReverseReversal(LedgerError):
    code = "cannot_reverse_reversal"


class RequestError(Exception):
    """Rejected before the key is claimed. Not stored."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}
