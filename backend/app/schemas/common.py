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
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

from app.core.errors import ErrorCode
from app.core.units import DECIMAL_PLACES, PrecisionError, format_cr, to_micro

AMOUNT_INTEGER_DIGITS: Final[int] = 12
"""Digits an amount may have before the decimal point, so its µ-value fits a BIGINT."""

DEFAULT_PAGE_SIZE: Final[int] = 50
MAX_PAGE_SIZE: Final[int] = 200

_AMOUNT_LIMIT: Final[Decimal] = Decimal(10) ** AMOUNT_INTEGER_DIGITS
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
        whole = f"[0-9]{{1,{AMOUNT_INTEGER_DIGITS}}}"
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
        if abs(amount) >= _AMOUNT_LIMIT:
            raise PydanticCustomError(
                "decimal_too_large",
                "Input should have at most {digits} digits before the decimal point",
                {"digits": AMOUNT_INTEGER_DIGITS},
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


CreditAmount = Annotated[Decimal, _DecimalString()]
"""Credits (CR), zero or more."""

PositiveCreditAmount = Annotated[Decimal, _DecimalString(positive=True)]
"""Credits (CR), more than zero."""

SignedCreditAmount = Annotated[Decimal, _DecimalString(signed=True)]
"""Credits (CR) that may be negative: a ledger line, a market account, VIS's exposure."""

ShareAmount = Annotated[Decimal, _DecimalString()]
"""Shares, zero or more. One winning share pays 1 CR."""

PositiveShareAmount = Annotated[Decimal, _DecimalString(positive=True)]
"""Shares, more than zero."""

Probability = Annotated[float, Field(ge=0, le=1)]
"""A price in [0, 1], rounded to `PRICE_DISPLAY_DECIMALS + 2` places (Appendix A.2)."""


class ErrorResponse(ResponseModel):
    """The body of every non-2xx response (SPEC §10, Appendix A.2)."""

    code: ErrorCode
    message: str
    details: dict[str, Any]


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
