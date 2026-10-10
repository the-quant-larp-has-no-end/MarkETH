"""Application errors. Every failure the API reports is one of these (SPEC §10).

Services raise them; `app/api/errors.py` turns them into the one response shape
`{code, message, details}`. To add an error: add an `ErrorCode`, subclass `AppError`.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, ClassVar, Final


class ErrorCode(StrEnum):
    # Codes named by the spec.
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    QUOTE_STALE = "QUOTE_STALE"
    MARKET_NOT_OPEN = "MARKET_NOT_OPEN"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    TRADE_TOO_SMALL = "TRADE_TOO_SMALL"
    CODE_INVALID = "CODE_INVALID"
    RATE_LIMITED = "RATE_LIMITED"
    # Codes the implementation adds.
    INSUFFICIENT_SHARES = "INSUFFICIENT_SHARES"
    PROFILE_REQUIRED = "PROFILE_REQUIRED"
    USERNAME_TAKEN = "USERNAME_TAKEN"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    """Base class. Subclasses fix `code`, `status_code` and a default message."""

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500
    default_message: ClassVar[str] = "Something went wrong."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details: dict[str, Any] = dict(details or {})
        self.headers: dict[str, str] = dict(headers or {})
        super().__init__(self.message)


class InsufficientBalanceError(AppError):
    code = ErrorCode.INSUFFICIENT_BALANCE
    status_code = 409
    default_message = "Not enough tokens for this."


class InsufficientSharesError(AppError):
    code = ErrorCode.INSUFFICIENT_SHARES
    status_code = 409
    default_message = "You cannot sell more shares than you hold."


class QuoteStaleError(AppError):
    """The price moved past the caller's guard. `details.quote` carries a fresh quote."""

    code = ErrorCode.QUOTE_STALE
    status_code = 409
    default_message = "The price moved. Review the new quote and confirm again."


class MarketNotOpenError(AppError):
    code = ErrorCode.MARKET_NOT_OPEN
    status_code = 409
    default_message = "This market is not open for trading."


class LimitExceededError(AppError):
    code = ErrorCode.LIMIT_EXCEEDED
    status_code = 409
    default_message = "This would exceed a limit."


class TradeTooSmallError(AppError):
    code = ErrorCode.TRADE_TOO_SMALL
    status_code = 422
    default_message = "This trade is below the minimum size."


class CodeInvalidError(AppError):
    """One generic error for unknown, used, expired and revoked codes (SPEC §2)."""

    code = ErrorCode.CODE_INVALID
    status_code = 400
    default_message = "This code is not valid."


class RateLimitedError(AppError):
    code = ErrorCode.RATE_LIMITED
    status_code = 429
    default_message = "Too many attempts. Try again later."

    def __init__(self, message: str | None = None, *, retry_after_seconds: int = 0) -> None:
        super().__init__(
            message,
            details={"retry_after_seconds": retry_after_seconds},
            headers={"Retry-After": str(max(retry_after_seconds, 1))},
        )


class IdempotencyKeyReusedError(AppError):
    code = ErrorCode.IDEMPOTENCY_KEY_REUSED
    status_code = 422
    default_message = "This Idempotency-Key was already used for a different request."


class ValidationFailedError(AppError):
    code = ErrorCode.VALIDATION_ERROR
    status_code = 422
    default_message = "The request is not valid."


class UnauthenticatedError(AppError):
    code = ErrorCode.UNAUTHENTICATED
    status_code = 401
    default_message = "Sign in through the ETH portal to continue."


class ForbiddenError(AppError):
    code = ErrorCode.FORBIDDEN
    status_code = 403
    default_message = "You are not allowed to do this."


SUSPENDED_MESSAGE: Final[str] = "This account is suspended."
"""Message of the `ForbiddenError` that refuses a suspended account, in the guards of
`app/api/deps.py` and in the one service that meets such an account behind them."""


class ProfileRequiredError(AppError):
    """The portal named somebody who has no profile yet. `details` says who, so the
    set-up screen can show it: `name` (may be empty) and `email`."""

    code = ErrorCode.PROFILE_REQUIRED
    status_code = 403
    default_message = "Set up your profile to continue."

    def __init__(self, *, name: str, email: str) -> None:
        super().__init__(details={"name": name, "email": email})


class NotFoundError(AppError):
    code = ErrorCode.NOT_FOUND
    status_code = 404
    default_message = "Not found."


class ConflictError(AppError):
    code = ErrorCode.CONFLICT
    status_code = 409
    default_message = "This is not possible in the current state."


class UsernameTakenError(AppError):
    """Somebody else holds the username."""

    code = ErrorCode.USERNAME_TAKEN
    status_code = 409
    default_message = "This username is taken."

    def __init__(self) -> None:
        super().__init__(details={"field": "username"})


class ServiceUnavailableError(AppError):
    code = ErrorCode.SERVICE_UNAVAILABLE
    status_code = 503
    default_message = "This is temporarily unavailable. Try again shortly."


class InvariantViolationError(Exception):
    """An accounting invariant failed inside a transaction (SPEC §5 circuit breaker).

    Deliberately not an `AppError`: it must never reach a client as-is. The trading
    service catches it, halts the market and raises `ServiceUnavailableError`.
    """

    def __init__(self, invariant: str, context: Mapping[str, Any]) -> None:
        self.invariant = invariant
        self.context: dict[str, Any] = dict(context)
        super().__init__(f"invariant violated: {invariant}")
