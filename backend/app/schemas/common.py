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

What a field says when it is filled in wrongly. A 422 shows a person one sentence per
field, and a form puts it under that field as it is. `app/api/validation.py` makes
that sentence from the rule that was broken. A field whose rule needs words of its own
says them here, with three helpers:

    said(kind, sentence)                 an error in the field's own words
    in_its_own_words(type=sentence)      the same, for rules a `Field(...)` states
    on_field(field, kind, sentence)      a rule about several fields, shown on one

A sentence is short, says what to do and ends with a full stop. On screen a credit is
a token: no sentence says "credit" or "CR". No validator of a request model raises a
bare `ValueError`: its text would reach the screen as the framework frames it.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    SerializerFunctionWrapHandler,
    StrictBool,
    ValidationError,
    ValidatorFunctionWrapHandler,
    WithJsonSchema,
    WrapValidator,
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

SENTENCE: Final[str] = "sentence"
"""Key under which an error carries the sentence of its field, in its context (`said`).
`app/api/validation.py` shows that sentence as the error's `msg`."""

NOT_AN_AMOUNT_MESSAGE: Final[str] = "Enter an amount, for example 12.5."
TOO_MANY_PLACES_MESSAGE: Final[str] = f"Use at most {DECIMAL_PLACES} decimal places."
AMOUNT_TOO_LARGE_MESSAGE: Final[str] = "Enter a smaller amount."
AMOUNT_NOT_POSITIVE_MESSAGE: Final[str] = "Enter an amount above 0."
AMOUNT_NEGATIVE_MESSAGE: Final[str] = "Enter an amount of 0 or more."
AMOUNT_ZERO_MESSAGE: Final[str] = "Enter an amount other than 0."
"""What an amount field says for each rule an amount can break (`_DecimalString`)."""

ACCEPT_THE_POLICY_MESSAGE: Final[str] = "Accept the usage policy to continue."
"""What a request says that has to state the acceptance of the usage policy and does
not (`Accepted`)."""


def said(kind: str, sentence: str) -> PydanticCustomError:
    """A validation error in the words of the field that raises it.

    `sentence` is written for the person who filled the field in and is shown under
    it as it is. `kind` is the `type` the error is reported with: the framework's own
    name of the rule where it has one (`string_pattern_mismatch`, `less_than_equal`),
    so that a `type` names the same rule whoever worded the message, and a name of
    ours for a rule of ours (`decimal_places`).
    """
    return PydanticCustomError(kind, "{sentence}", {SENTENCE: sentence})


def in_its_own_words(**sentences: str) -> WrapValidator:
    """Lets a field answer a broken rule in its own words.

    It goes last in the `Annotated[...]` of a field, after the `Field(...)` whose rules
    it speaks for:

        Annotated[str, Field(pattern=...), in_its_own_words(string_pattern_mismatch="...")]

    Each keyword is the `type` of an error those rules raise, and the sentence to say
    for it. An error of any other type passes as it is and gets the sentence of the
    table. The rules themselves are not touched, so the JSON schema of the field
    keeps its pattern and its bounds.
    """

    def validate(value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        try:
            return handler(value)
        except ValidationError as refused:
            (first, *_) = refused.errors(
                include_url=False, include_context=False, include_input=False
            )
            sentence = sentences.get(first["type"])
            if sentence is None:
                raise
            raise said(first["type"], sentence) from None

    return WrapValidator(validate)


def on_field(field: str, kind: str, sentence: str) -> ValidationError:
    """A rule about several fields of a request, reported on one of them.

    Raise it from a `model_validator(mode="after")`. The error then names `field` in
    its `loc`, like an error of that field's own rules, so a form shows `sentence`
    under it. Raising `said(...)` there instead reports the rule on the request as a
    whole, which no field of a form shows.
    """
    return ValidationError.from_exception_data(
        "request", [{"type": said(kind, sentence), "loc": (field,), "input": None}]
    )


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
    decimal place is rejected. So is a number of any length that is no amount: one
    with a million digits is too large, and one with a million decimal places has
    too many, like any other. Output trims trailing zeros (`"0.5"`, `"2"`). The
    JSON schema is `string` in both directions.
    """

    signed: bool = False
    positive: bool = False
    non_zero: bool = False
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
        # The size first, read off the exponent: no arithmetic is done with a number
        # of a million digits. Arithmetic on one either gives up (`decimal.Overflow`
        # in the default context) or takes seconds (the exact conversion below).
        # `adjusted()` is the power of ten of the first digit that is not zero.
        if amount and amount.adjusted() >= self.integer_digits:
            raise said("decimal_too_large", AMOUNT_TOO_LARGE_MESSAGE)
        try:
            to_micro(amount)
        except PrecisionError as exc:
            raise said("decimal_places", TOO_MANY_PLACES_MESSAGE) from exc
        if self.positive and amount <= 0:
            raise said("decimal_not_positive", AMOUNT_NOT_POSITIVE_MESSAGE)
        if not self.signed and amount < 0:
            raise said("decimal_negative", AMOUNT_NEGATIVE_MESSAGE)
        if self.non_zero and amount == 0:
            raise said("decimal_zero", AMOUNT_ZERO_MESSAGE)
        return amount


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal) and value.is_finite():
        return value
    # bool is an int in Python; `true` must not become 1 CR.
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, str) and _PLAIN_DECIMAL.fullmatch(value):
        return Decimal(value)
    raise said("decimal_string", NOT_AN_AMOUNT_MESSAGE)


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

NonZeroCreditAmount = Annotated[
    Decimal, _DecimalString(signed=True, non_zero=True, integer_digits=REQUEST_INTEGER_DIGITS)
]
"""Credits (CR) a request moves one way or the other: above zero to add, below zero to
take away, never zero, and between -10^9 and 10^9."""

ShareAmount = Annotated[Decimal, _DecimalString()]
"""Shares, zero or more. One winning share pays 1 CR."""

PositiveShareAmount = Annotated[
    Decimal, _DecimalString(positive=True, integer_digits=REQUEST_INTEGER_DIGITS)
]
"""Shares a request asks for: more than zero, below 10^9."""

Probability = Annotated[float, Field(ge=0, le=1)]
"""A price in [0, 1], rounded to `PRICE_DISPLAY_DECIMALS + 2` places (Appendix A.2)."""


def _accepted(value: bool) -> bool:
    if not value:
        raise said("not_accepted", ACCEPT_THE_POLICY_MESSAGE)
    return value


Accepted = Annotated[
    StrictBool,
    AfterValidator(_accepted),
    in_its_own_words(bool_type=ACCEPT_THE_POLICY_MESSAGE),
    WithJsonSchema({"type": "boolean", "const": True}),
]
"""The acceptance of the usage policy, which a request has to state: the JSON `true`
and nothing that merely counts as true. `false`, a number and a string are rejected
on the field."""


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

    MAX_ADMIN_ADJUSTMENT_CR = "MAX_ADMIN_ADJUSTMENT_CR"
    """An admin's adjustment would add more to a balance than one adjustment may."""


class ErrorResponse(ResponseModel):
    """The body of every non-2xx response (SPEC §10, Appendix A.2).

    `message` is written for the user and can be shown as it is. `details` is an
    object, empty unless the code is one of these:

    * `VALIDATION_ERROR`. A request that does not match its schema: `errors`, a list
      of `{loc, msg, type}`, one per field. `loc` ends with the name of the field,
      `msg` is a sentence for the person who filled it in, to be shown under the
      field as it is ("Use at most 200 characters."), and `type` names the rule
      that was broken. A rule a service checks: `field`, the name of the offending
      field, and `message` is the sentence for it. A profile created without
      `policy_accepted: true` is such a rule, and its `field` is `policy_accepted`;
      so is a filter of the leaderboard whose `min_…` is above its `max_…`, with
      the `min_…` as its `field`.
    * `FORBIDDEN`, when the portal's id is not an address MarkETH is open to:
      `allowed_domains`, a list of the domains it is open to.
    * `PROFILE_REQUIRED`: `name` and `email`, who the portal says is calling. `name`
      may be an empty string.
    * `USERNAME_TAKEN`: `field`, which is `username`.
    * `RATE_LIMITED`: `retry_after_seconds`, an integer, also sent as the
      `Retry-After` header.
    * `QUOTE_STALE`: `quote`, a fresh `QuoteResponse` for the same trade.
    * `LIMIT_EXCEEDED`: `limit`, which names the limit: `MAX_STAKE_PER_USER_MARKET`,
      `MAX_PROPOSALS_PER_USER_PER_DAY`, `MAX_OPEN_MARKETS`, `MAX_MARKET_BUDGET`,
      `MAX_AGGREGATE_LOSS_BUDGET`, `MAX_CODE_ISSUANCE_PER_MONTH`,
      `ONE_CODE_PER_USER` or `MAX_ADMIN_ADJUSTMENT_CR`. With
      `MAX_STAKE_PER_USER_MARKET` also `stake_limit_cr` and `stake_left_cr`; with
      `MAX_CODE_ISSUANCE_PER_MONTH` also `issued_cr`, `requested_cr` and `cap_cr`;
      with `MAX_ADMIN_ADJUSTMENT_CR` also `requested_cr`, what the adjustment would
      add, and `cap_cr`, the most one adjustment may add.
    * `INSUFFICIENT_BALANCE`: `balance_cr` and `required_cr`. Where an admin debits
      somebody's balance, they are what that account holds and what the debit takes.
    * `SERVICE_UNAVAILABLE`, from `GET /health` only: `postgres`, `redis` and
      `scylla`, each `up` or `down`.

    A quote is answered like its trade. `GET /markets/{id}/quote` gives a buy the
    caller could not make the error the trade would get, with the same details:
    `LIMIT_EXCEEDED` with `MAX_STAKE_PER_USER_MARKET`, `stake_limit_cr` and
    `stake_left_cr` above the stake left, `INSUFFICIENT_BALANCE` with `balance_cr`
    and `required_cr` above the balance.

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
