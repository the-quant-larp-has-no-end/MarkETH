"""Building blocks of the API contract (SPEC §10, Appendix A.2).

The base classes of every request and response, the amount types that keep floats away
from credits, the one error shape, and cursor pagination.

Amounts. Credits and shares cross the API as decimal strings with at most six places
(`"0.25"`), never as JSON numbers: a float cannot hold 0.1, and golden rule 1 forbids
one anywhere near a balance. In Python the same field is a `Decimal`. Convert with
`app.core.units`:

    to_micro(request.amount_cr)            # request  -> µCR
    QuoteResponse(amount_cr=to_cr(micro))  # µCR      -> response

To put a response into JSON yourself (an idempotency record, the `details` of an
error), use `model.model_dump(mode="json")`; it writes amounts as strings.

Error details. `ErrorResponse` lists what `details` holds for each code. An amount in
there is named `*_cr` and written like every other amount: pass `to_cr(micro)` or
`format_cr(micro)`, both arrive as `"0.25"`. What a `LIMIT_EXCEEDED` ran into is
named by a `Limit` member under `details.limit`.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    SerializerFunctionWrapHandler,
    field_serializer,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

from app.core.errors import ErrorCode
from app.core.units import DECIMAL_PLACES, PrecisionError, format_cr, to_micro

AMOUNT_INTEGER_DIGITS: Final[int] = 12
"""Digits an amount may have before the decimal point, so its µ-value fits a BIGINT."""

REQUEST_INTEGER_DIGITS: Final[int] = 9
"""Digits a size or budget in a request may have before the decimal point.

Fewer than a response holds, on purpose: what the server computes from a request can be
larger than the request. A budget buys more shares than it has credits (many more when
the price is near zero), and that number of shares must still fit into the answer.
"""

DEFAULT_PAGE_SIZE: Final[int] = 50
MAX_PAGE_SIZE: Final[int] = 200

_PLAIN_DECIMAL: Final[re.Pattern[str]] = re.compile(r"-?[0-9]+(\.[0-9]+)?")


class RequestModel(BaseModel):
    """Base of every request body and query model: unknown fields are an error.

    Strings are stripped of surrounding whitespace before their length is checked.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        use_attribute_docstrings=True,
    )


class ResponseModel(BaseModel):
    """Base of every response body and of the models nested inside one.

    The string under a field is its description in the OpenAPI document.
    """

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)


@dataclass(frozen=True, slots=True)
class _DecimalString:
    """Pydantic annotation of an amount: a `Decimal` that is a string on the wire.

    Input accepts a plain decimal string (`"1"`, `"0.25"`), an integer, or a `Decimal`
    built by a service. A JSON float, a boolean, an exponent, a blank or a seventh
    decimal place is rejected. Output trims trailing zeros (`"0.5"`, `"2"`). The JSON
    schema is `string` in both directions.
    """

    signed: bool = False
    positive: bool = False
    integer_digits: int = AMOUNT_INTEGER_DIGITS

    def __get_pydantic_core_schema__(
        self, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_plain_validator_function(
            self._parse,
            serialization=core_schema.plain_serializer_function_ser_schema(
                _to_string, when_used="json"
            ),
        )

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        sign = "-?" if self.signed else ""
        whole = f"[0-9]{{1,{self.integer_digits}}}"
        fraction = f"[0-9]{{1,{DECIMAL_PLACES}}}"
        return {
            "type": "string",
            "pattern": rf"^{sign}{whole}(\.{fraction})?$",
            "examples": ["0.25"],
        }

    def _parse(self, value: object) -> Decimal:
        amount = _to_decimal(value)
        try:
            to_micro(amount)
        except PrecisionError as exc:
            raise PydanticCustomError(
                "decimal_places",
                "Input should have at most {places} decimal places",
                {"places": DECIMAL_PLACES},
            ) from exc
        if self.positive and amount <= 0:
            raise PydanticCustomError("decimal_not_positive", "Input should be greater than 0")
        if not self.signed and amount < 0:
            raise PydanticCustomError("decimal_negative", "Input should not be negative")
        if abs(amount) >= Decimal(10) ** self.integer_digits:
            raise PydanticCustomError(
                "decimal_too_large",
                "Input should have at most {digits} digits before the decimal point",
                {"digits": self.integer_digits},
            )
        return amount


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal) and value.is_finite():
        return value
    # bool is an int in Python; `true` must not become 1 CR.
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, str) and _PLAIN_DECIMAL.fullmatch(value):
        return Decimal(value)
    raise PydanticCustomError("decimal_string", 'Input should be a decimal string such as "0.25"')


def _to_string(value: Decimal) -> str:
    return format_cr(to_micro(value))


def _amounts_as_strings(value: object) -> object:
    """A copy of an error's details in which every bare `Decimal` is an API amount string.

    Without it `to_cr(250_000)` would leave as `"0.250000"` where `format_cr` gives
    `"0.25"`. Schema objects are left alone; they serialise themselves.
    """
    if isinstance(value, Decimal):
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, dict):
        return {key: _amounts_as_strings(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_amounts_as_strings(item) for item in value]
    return value


CreditAmount = Annotated[Decimal, _DecimalString()]
"""Credits (CR), zero or more."""

PositiveCreditAmount = Annotated[
    Decimal, _DecimalString(positive=True, integer_digits=REQUEST_INTEGER_DIGITS)
]
"""Credits (CR) a request asks for: more than zero, below 10^9."""

SignedCreditAmount = Annotated[Decimal, _DecimalString(signed=True)]
"""Credits (CR) that may be negative: a ledger line, a market account, VIS's exposure."""

ShareAmount = Annotated[Decimal, _DecimalString()]
"""Shares, zero or more. One winning share pays 1 CR."""

PositiveShareAmount = Annotated[
    Decimal, _DecimalString(positive=True, integer_digits=REQUEST_INTEGER_DIGITS)
]
"""Shares a request asks for: more than zero, below 10^9."""

Probability = Annotated[float, Field(ge=0, le=1)]
"""A price in [0, 1], rounded to `PRICE_DISPLAY_DECIMALS + 2` places (Appendix A.2)."""


class Limit(StrEnum):
    """`details.limit` of a `LIMIT_EXCEEDED` error: which limit the request ran into."""

    MAX_STAKE_PER_USER_MARKET = "MAX_STAKE_PER_USER_MARKET"
    """A buy would take the user's net stake in the market above the limit (SPEC §4)."""

    MAX_PROPOSALS_PER_USER_PER_DAY = "MAX_PROPOSALS_PER_USER_PER_DAY"
    MAX_OPEN_MARKETS = "MAX_OPEN_MARKETS"
    MAX_MARKET_BUDGET = "MAX_MARKET_BUDGET"
    MAX_AGGREGATE_LOSS_BUDGET = "MAX_AGGREGATE_LOSS_BUDGET"
    MAX_CODE_ISSUANCE_PER_MONTH = "MAX_CODE_ISSUANCE_PER_MONTH"
    ONE_CODE_PER_USER = "ONE_CODE_PER_USER"
    """The user already redeemed a code of a batch that allows one per user (SPEC §2)."""


class ErrorResponse(ResponseModel):
    """The body of every non-2xx response (SPEC §10, Appendix A.2).

    `message` is written for the user and can be shown as it is. `details` is an
    object, empty unless the code is one of these:

    * `VALIDATION_ERROR`. A request that does not match its schema: `errors`, a list
      of `{loc, msg, type}`, one per field. A rule a service checks: `field`, the name
      of the offending field. An email address that is not allowed: `allowed_domains`,
      a list.
    * `RATE_LIMITED`: `retry_after_seconds`, an integer, also sent as the
      `Retry-After` header.
    * `QUOTE_STALE`: `quote`, a fresh `QuoteResponse` for the same trade.
    * `LIMIT_EXCEEDED`: `limit`, which names the limit: `MAX_STAKE_PER_USER_MARKET`,
      `MAX_PROPOSALS_PER_USER_PER_DAY`, `MAX_OPEN_MARKETS`, `MAX_MARKET_BUDGET`,
      `MAX_AGGREGATE_LOSS_BUDGET`, `MAX_CODE_ISSUANCE_PER_MONTH` or
      `ONE_CODE_PER_USER`. With `MAX_STAKE_PER_USER_MARKET` also `stake_limit_cr` and
      `stake_left_cr`; with `MAX_CODE_ISSUANCE_PER_MONTH` also `issued_cr`,
      `requested_cr` and `cap_cr`.
    * `INSUFFICIENT_BALANCE`: `balance_cr` and `required_cr`.
    * `SERVICE_UNAVAILABLE`, from `GET /health` only: `postgres`, `redis` and
      `scylla`, each `up` or `down`.

    Amounts in `details` are decimal strings like every `*_cr` field.
    """

    code: ErrorCode
    message: str
    details: dict[str, Any]

    @field_serializer("details", mode="wrap", when_used="json")
    def _details_with_api_amounts(
        self, details: dict[str, Any], handler: SerializerFunctionWrapHandler
    ) -> object:
        return handler(_amounts_as_strings(details))


class PageQuery(RequestModel):
    """Query string of a list that can grow (Appendix A.2)."""

    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    cursor: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="`next_cursor` of the previous page; omit it for the first page.",
    )


class CursorPage[ItemT](ResponseModel):
    """One page of a list that can grow, newest first.

    `next_cursor` is opaque to the client. It is null on the last page.
    """

    items: list[ItemT]
    next_cursor: str | None
