"""The API contract: what `backend/openapi.json` promises the frontend (SPEC §10, A.2, A.4).

The frontend's types are generated from the OpenAPI document, so these tests pin what
that document must say: every endpoint of the spec, one error shape, amounts as strings,
and a committed file that matches the code.

Nothing here needs Docker: the application is built without a data store behind it, the
way `scripts/export_openapi.py` builds it. The module therefore carries no `integration`
marker.
"""

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, ValidationError

from app.api.access import DOCUMENTED_SESSION_COOKIE
from app.container import Container
from app.core.config import Settings
from app.core.enums import MarketStatus, MarketTier, Outcome, TradeSide
from app.core.errors import ErrorCode, NotFoundError, ServiceUnavailableError
from app.core.units import MICRO, format_cr, to_cr
from app.main import create_app
from app.pricing import lmsr
from app.schemas.admin.code_batches import CreateCodeBatchRequest
from app.schemas.admin.markets import ApproveMarketRequest
from app.schemas.common import (
    CreditAmount,
    ErrorResponse,
    Limit,
    PageQuery,
    PositiveCreditAmount,
    SignedCreditAmount,
)
from app.schemas.live import QuoteEvent, StatusEvent
from app.schemas.markets import ProposeMarketRequest
from app.schemas.trading import QuoteQuery, QuoteResponse, TradeRequest
from app.services.live_quotes import Heartbeat, LiveEvent
from scripts.export_openapi import OPENAPI_FILE, render_openapi
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.mail import MemoryMailer

type Operation = tuple[str, str]

SPEC_SECTION_10: Final[frozenset[Operation]] = frozenset(
    {
        ("POST", "/api/auth/request-link"),
        ("POST", "/api/auth/verify"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/me"),
        ("GET", "/api/markets"),
        ("GET", "/api/markets/{market_id}"),
        ("GET", "/api/markets/{market_id}/quote"),
        ("POST", "/api/markets/{market_id}/trades"),
        ("GET", "/api/markets/{market_id}/history"),
        ("GET", "/api/markets/{market_id}/stream"),
        ("GET", "/api/me/positions"),
        ("GET", "/api/me/trades"),
        ("GET", "/api/me/ledger"),
        ("POST", "/api/markets"),
        ("POST", "/api/vouchers/redeem-code"),
        ("POST", "/api/coffee-tickets"),
        ("POST", "/api/admin/markets/{market_id}/approve"),
        ("POST", "/api/admin/markets/{market_id}/reject"),
        ("POST", "/api/admin/markets/{market_id}/halt"),
        ("POST", "/api/admin/markets/{market_id}/resume"),
        ("POST", "/api/admin/markets/{market_id}/close"),
        ("POST", "/api/admin/markets/{market_id}/resolve"),
        ("POST", "/api/admin/markets/{market_id}/void"),
        ("POST", "/api/admin/code-batches"),
        ("POST", "/api/admin/code-batches/{batch_id}/revoke"),
        ("GET", "/api/admin/risk"),
        ("POST", "/api/barista/coffee-tickets/{code}/redeem"),
    }
)

APPENDIX_A2: Final[frozenset[Operation]] = frozenset(
    {
        ("GET", "/api/health"),
        ("GET", "/api/config"),
        ("GET", "/api/me/proposals"),
        ("GET", "/api/me/coffee-tickets"),
        ("GET", "/api/admin/code-batches"),
        ("POST", "/api/admin/alerts/{alert_id}/ack"),
        ("POST", "/api/admin/grants/monthly"),
    }
)

SUCCESS_STATUS: Final[dict[Operation, str]] = {
    ("POST", "/api/auth/request-link"): "202",
    ("POST", "/api/auth/logout"): "204",
    ("POST", "/api/markets"): "201",
    ("POST", "/api/markets/{market_id}/trades"): "201",
    ("POST", "/api/coffee-tickets"): "201",
    ("POST", "/api/admin/code-batches"): "201",
}
"""Operations that do not answer 200: accepted, no content, and the four creations."""

ERROR_REF: Final[dict[str, str]] = {"$ref": "#/components/schemas/ErrorResponse"}

# Who may call what (Appendix A.2), as the `security` of an operation says it.
NO_SESSION: Final[list[dict[str, list[str]]]] = []
VIEWER: Final[list[dict[str, list[str]]]] = [{}, {"session": []}]
SIGNED_IN: Final[list[dict[str, list[str]]]] = [{"session": []}]
STAFF: Final[list[dict[str, list[str]]]] = [{"session": ["BARISTA"]}, {"session": ["ADMIN"]}]
ADMIN: Final[list[dict[str, list[str]]]] = [{"session": ["ADMIN"]}]

PUBLIC_ACCESS: Final[dict[Operation, list[dict[str, list[str]]]]] = {
    ("GET", "/api/health"): NO_SESSION,
    ("GET", "/api/config"): NO_SESSION,
    ("POST", "/api/auth/request-link"): NO_SESSION,
    ("POST", "/api/auth/verify"): NO_SESSION,
    ("POST", "/api/auth/logout"): NO_SESSION,
    ("GET", "/api/markets"): VIEWER,
    ("GET", "/api/markets/{market_id}"): VIEWER,
    ("GET", "/api/markets/{market_id}/quote"): NO_SESSION,
    ("GET", "/api/markets/{market_id}/history"): NO_SESSION,
    ("GET", "/api/markets/{market_id}/stream"): NO_SESSION,
}
"""A.2: "Public, no session needed". Two of them show more to a signed-in viewer."""


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return create_app().openapi()


@pytest.fixture(scope="module")
def schemas(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    components: dict[str, dict[str, Any]] = document["components"]["schemas"]
    return components


def each_operation(document: dict[str, Any]) -> Iterator[tuple[Operation, dict[str, Any]]]:
    for path, item in document["paths"].items():
        for method, operation in item.items():
            yield (method.upper(), path), operation


def variants(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """The non-null alternatives of a property schema, looking through arrays."""
    found: list[dict[str, Any]] = []
    for option in schema.get("anyOf", [schema]):
        if option.get("type") == "null":
            continue
        found.extend(variants(option["items"]) if option.get("type") == "array" else [option])
    return found


# --- The document ----------------------------------------------------------------


def test_every_endpoint_of_the_spec_is_served_and_nothing_else(document: dict[str, Any]) -> None:
    served = {operation for operation, _ in each_operation(document)}
    expected = SPEC_SECTION_10 | APPENDIX_A2
    assert expected - served == set(), "in SPEC §10 or A.2 but not served"
    assert served - expected == set(), "served but in neither SPEC §10 nor A.2"
    assert len(served) == 34


def test_the_document_names_the_api_and_lives_under_api(document: dict[str, Any]) -> None:
    assert document["info"]["title"] == "MarkETH API"
    app = create_app()
    assert app.openapi_url == "/api/openapi.json"
    assert app.docs_url == "/api/docs"
    assert all(path.startswith("/api/") for path in document["paths"])


def test_every_operation_has_a_summary_a_tag_and_a_short_unique_id(
    document: dict[str, Any],
) -> None:
    ids: list[str] = []
    for operation, spec in each_operation(document):
        assert spec.get("summary"), operation
        assert len(spec.get("tags", [])) == 1, operation
        assert spec["operationId"].isidentifier(), operation
        ids.append(spec["operationId"])
    assert len(ids) == len(set(ids))


def test_every_operation_answers_with_the_documented_status(document: dict[str, Any]) -> None:
    for operation, spec in each_operation(document):
        success = [status for status in spec["responses"] if status != "default"]
        assert success == [SUCCESS_STATUS.get(operation, "200")], operation


def test_every_operation_declares_the_one_error_shape(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    """`ErrorResponse` replaces FastAPI's own 422 schema, for every operation (A.2)."""
    for operation, spec in each_operation(document):
        default = spec["responses"]["default"]
        assert default["content"] == {"application/json": {"schema": ERROR_REF}}, operation
        assert "422" not in spec["responses"], operation
    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas
    assert set(schemas["ErrorResponse"]["required"]) == {"code", "message", "details"}
    assert set(schemas["ErrorCode"]["enum"]) >= {
        "INSUFFICIENT_BALANCE",
        "QUOTE_STALE",
        "MARKET_NOT_OPEN",
        "LIMIT_EXCEEDED",
        "TRADE_TOO_SMALL",
        "CODE_INVALID",
        "RATE_LIMITED",
        "VALIDATION_ERROR",
        "UNAUTHENTICATED",
        "FORBIDDEN",
        "NOT_FOUND",
    }


def test_every_operation_says_who_may_call_it(document: dict[str, Any]) -> None:
    """A.2 "Who may call what" is binding, and the frontend reads it from this document."""
    for operation, spec in each_operation(document):
        _, path = operation
        if operation in PUBLIC_ACCESS:
            expected = PUBLIC_ACCESS[operation]
        elif path.startswith("/api/admin/"):
            expected = ADMIN
        elif path.startswith("/api/barista/"):
            expected = STAFF
        else:
            expected = SIGNED_IN
        assert spec.get("security", NO_SESSION) == expected, operation
        assert spec["description"].startswith("Access: "), operation
        assert ("public" in spec["description"]) == (operation in PUBLIC_ACCESS), operation


def test_the_one_security_scheme_is_the_session_cookie(
    document: dict[str, Any], base_settings: Settings
) -> None:
    schemes = document["components"]["securitySchemes"]
    assert list(schemes) == ["session"]
    assert schemes["session"] | {"type": "apiKey", "in": "cookie"} == schemes["session"]
    # The document is built without settings, so it repeats the name of `.example.env`.
    assert schemes["session"]["name"] == DOCUMENTED_SESSION_COOKIE
    assert base_settings.auth.session_cookie_name == DOCUMENTED_SESSION_COOKIE


def test_the_error_schema_says_what_details_carries(schemas: dict[str, dict[str, Any]]) -> None:
    """`details` is a free object; its keys per code are in the description."""
    description = schemas["ErrorResponse"]["description"]
    for code in (
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.RATE_LIMITED,
        ErrorCode.QUOTE_STALE,
        ErrorCode.LIMIT_EXCEEDED,
        ErrorCode.INSUFFICIENT_BALANCE,
        ErrorCode.SERVICE_UNAVAILABLE,
    ):
        assert f"`{code.value}`" in description, code
    for key in (
        "errors",
        "field",
        "allowed_domains",
        "retry_after_seconds",
        "quote",
        "limit",
        "stake_limit_cr",
        "stake_left_cr",
        "issued_cr",
        "requested_cr",
        "cap_cr",
        "balance_cr",
        "required_cr",
    ):
        assert f"`{key}`" in description, key
    for limit in Limit:
        assert f"`{limit.value}`" in description, limit


def test_the_redeem_request_says_that_the_client_sends_the_bare_code(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """A scanned code card holds a URL; posted as it is, it would cost an attempt."""
    description = schemas["RedeemCodeRequest"]["description"]
    assert "#code=" in description
    assert "CODE_INVALID" in description


def test_credits_and_shares_are_strings_and_probabilities_numbers(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """A.2: `*_cr` and `shares` fields are decimal strings in both directions."""
    amounts = probabilities = 0
    for name, schema in schemas.items():
        for field, spec in schema.get("properties", {}).items():
            where = f"{name}.{field}"
            if field.endswith("_cr") or field in ("shares", "min_shares", "q_yes", "q_no"):
                for variant in variants(spec):
                    assert variant["type"] == "string", where
                    assert variant["pattern"].endswith("(\\.[0-9]{1,6})?$"), where
                amounts += 1
            elif field.startswith("p_") or field == "average_price":
                assert spec == spec | {"type": "number", "minimum": 0, "maximum": 1}, where
                probabilities += 1
    assert amounts > 60
    assert probabilities > 15


def test_no_schema_field_is_typed_as_a_bare_number_of_credits(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """Micro-units never cross the API; nothing is named `*_micro`."""
    for name, schema in schemas.items():
        assert not [f for f in schema.get("properties", {}) if f.endswith("_micro")], name


def test_request_bodies_reject_unknown_fields(schemas: dict[str, dict[str, Any]]) -> None:
    requests = [name for name in schemas if name.endswith("Request")]
    assert len(requests) >= 14
    for name in requests:
        assert schemas[name]["additionalProperties"] is False, name


def test_response_fields_are_never_optional(schemas: dict[str, dict[str, Any]]) -> None:
    """What can be missing is nullable and always sent, so the client's types have no `?`."""
    for name, schema in schemas.items():
        if name.endswith("Request") or "properties" not in schema:
            continue
        assert set(schema.get("required", [])) == set(schema["properties"]), name


def test_the_trade_route_requires_an_idempotency_key_that_is_a_uuid(
    document: dict[str, Any],
) -> None:
    trade = document["paths"]["/api/markets/{market_id}/trades"]["post"]
    (header,) = [p for p in trade["parameters"] if p["in"] == "header"]
    assert header["name"] == "Idempotency-Key"
    assert header["required"] is True
    assert header["schema"] | {"type": "string", "format": "uuid"} == header["schema"]


def test_lists_that_can_grow_take_a_limit_and_a_cursor(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    for path, response in (
        ("/api/me/trades", "TradeListResponse"),
        ("/api/me/ledger", "LedgerListResponse"),
    ):
        parameters = {p["name"]: p for p in document["paths"][path]["get"]["parameters"]}
        assert set(parameters) == {"limit", "cursor"}
        assert parameters["limit"]["schema"]["maximum"] == 200
        assert not parameters["cursor"]["required"]
        assert set(schemas[response]["properties"]) == {"items", "next_cursor"}


def test_the_stream_is_documented_as_server_sent_events_with_its_payloads(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    stream = document["paths"]["/api/markets/{market_id}/stream"]["get"]
    content = stream["responses"]["200"]["content"]
    assert list(content) == ["text/event-stream"]
    assert {"QuoteEvent", "StatusEvent"} <= set(schemas)
    assert schemas["QuoteEvent"]["properties"]["type"]["const"] == "quote"
    assert schemas["StatusEvent"]["properties"]["type"]["const"] == "status"


def test_the_committed_openapi_json_is_up_to_date() -> None:
    """Run `make api-client` (or `uv run python -m scripts.export_openapi`) and commit."""
    assert OPENAPI_FILE.name == "openapi.json"
    assert OPENAPI_FILE.read_text(encoding="utf-8") == render_openapi()


def test_the_export_is_deterministic_sorted_and_ends_with_one_newline() -> None:
    text = render_openapi()
    assert text == render_openapi()
    assert text.endswith("}\n")
    assert not text.endswith("\n\n")
    document = json.loads(text)
    assert text == json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert list(document) == sorted(document)


def test_building_the_application_reads_no_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The export must work where no `.env` exists and no store is reachable."""

    def no_settings() -> Settings:
        raise AssertionError("create_app() must not load settings")

    monkeypatch.setattr("app.main.get_settings", no_settings)
    assert create_app().openapi()["paths"]


# --- Amounts (A.2) ----------------------------------------------------------------


class Wallet(BaseModel):
    balance_cr: CreditAmount
    change_cr: SignedCreditAmount
    stake_cr: PositiveCreditAmount | None = None


@pytest.mark.parametrize(
    ("sent", "parsed"),
    [("0.25", "0.25"), ("1", "1"), ("0", "0"), ("12.000000", "12"), ("0.000001", "0.000001")],
)
def test_an_amount_is_a_decimal_string_with_at_most_six_places(sent: str, parsed: str) -> None:
    wallet = Wallet.model_validate_json(f'{{"balance_cr": "{sent}", "change_cr": "0"}}')
    assert wallet.balance_cr == Decimal(sent)
    assert json.loads(wallet.model_dump_json())["balance_cr"] == parsed


@pytest.mark.parametrize(
    ("literal", "error"),
    [
        ("0.25", "decimal_string"),
        ("1.0", "decimal_string"),
        ("true", "decimal_string"),
        ("null", "decimal_string"),
        ('"1e3"', "decimal_string"),
        ('" 1"', "decimal_string"),
        ('"1,5"', "decimal_string"),
        ('".5"', "decimal_string"),
        ('"+1"', "decimal_string"),
        ('"NaN"', "decimal_string"),
        ('"Infinity"', "decimal_string"),
        ('""', "decimal_string"),
        ('"0.1234567"', "decimal_places"),
        ('"-0.01"', "decimal_negative"),
        ('"1000000000000"', "decimal_too_large"),
    ],
)
def test_what_is_not_an_exact_amount_is_rejected(literal: str, error: str) -> None:
    """JSON floats and booleans in particular: `0.25` the number is not `"0.25"`."""
    with pytest.raises(ValidationError) as rejected:
        Wallet.model_validate_json(f'{{"balance_cr": {literal}, "change_cr": "0"}}')
    assert [e["type"] for e in rejected.value.errors()] == [error]
    assert rejected.value.errors()[0]["loc"] == ("balance_cr",)


def test_an_integer_is_an_exact_amount() -> None:
    assert Wallet.model_validate_json('{"balance_cr": 3, "change_cr": 0}').balance_cr == 3


def test_only_a_signed_amount_may_be_negative_and_a_positive_one_not_zero() -> None:
    wallet = Wallet.model_validate({"balance_cr": "1", "change_cr": "-0.5", "stake_cr": "0.01"})
    assert wallet.change_cr == Decimal("-0.5")
    with pytest.raises(ValidationError) as rejected:
        Wallet.model_validate({"balance_cr": "1", "change_cr": "0", "stake_cr": "0"})
    assert rejected.value.errors()[0]["type"] == "decimal_not_positive"


def test_services_pass_decimals_and_json_gets_trimmed_strings() -> None:
    wallet = Wallet(balance_cr=to_cr(2_500_000), change_cr=to_cr(-1_000_000), stake_cr=None)
    assert wallet.balance_cr == Decimal("2.5")
    assert wallet.model_dump(mode="json") == {
        "balance_cr": "2.5",
        "change_cr": "-1",
        "stake_cr": None,
    }
    assert Wallet.model_validate(wallet.model_dump(mode="json")) == wallet


def test_a_decimal_with_too_many_places_cannot_even_be_put_into_a_response() -> None:
    with pytest.raises(ValidationError):
        Wallet(balance_cr=Decimal("0.0000001"), change_cr=Decimal(0))


def test_error_details_serialise_nested_amounts_as_strings() -> None:
    wallet = Wallet(balance_cr=to_cr(250_000), change_cr=to_cr(0))
    body = ErrorResponse(code="QUOTE_STALE", message="moved", details={"quote": wallet})
    assert body.model_dump(mode="json")["details"]["quote"]["balance_cr"] == "0.25"


def test_a_bare_amount_in_error_details_is_written_like_every_other_amount() -> None:
    """A.2: the server trims trailing zeros. `to_cr` and `format_cr` must give one form."""
    body = ErrorResponse(
        code=ErrorCode.LIMIT_EXCEEDED,
        message="m",
        details={
            "limit": Limit.MAX_STAKE_PER_USER_MARKET,
            "stake_limit_cr": to_cr(MICRO),
            "stake_left_cr": to_cr(250_000),
            "as_text_cr": format_cr(250_000),
            "nested": {"amounts_cr": [to_cr(0), to_cr(1_500_000)]},
            "retry_after_seconds": 42,
        },
    )

    assert body.model_dump(mode="json")["details"] == {
        "limit": "MAX_STAKE_PER_USER_MARKET",
        "stake_limit_cr": "1",
        "stake_left_cr": "0.25",
        "as_text_cr": "0.25",
        "nested": {"amounts_cr": ["0", "1.5"]},
        "retry_after_seconds": 42,
    }
    assert json.loads(body.model_dump_json())["details"]["stake_left_cr"] == "0.25"


def test_a_request_asks_for_less_than_a_response_can_hold() -> None:
    """A budget buys more shares than it has credits, and the answer must hold them."""
    largest = "999999999.999999"
    for field in ("amount_cr", "shares"):
        assert QuoteQuery.model_validate({"side": "BUY", "outcome": "YES", field: largest})
        with pytest.raises(ValidationError) as rejected:
            QuoteQuery.model_validate({"side": "BUY", "outcome": "YES", field: "1000000000"})
        assert rejected.value.errors()[0]["type"] == "decimal_too_large"
    # A response still holds twelve digits before the point.
    assert Wallet(balance_cr=Decimal("999999999999.999999"), change_cr=Decimal(0))


@pytest.mark.parametrize("b_cr", [1, 10, 1442])
def test_the_largest_budget_a_request_accepts_fits_into_a_quote_response(b_cr: int) -> None:
    """Found in review: the largest accepted budget bought more shares than
    `QuoteResponse` could carry, a 500 on a public route."""
    query = QuoteQuery.model_validate(
        {"side": "BUY", "outcome": "YES", "amount_cr": "999999999.999999"}
    )
    assert query.amount_cr is not None
    trailing = lmsr.MarketState(q_yes=0, q_no=1000 * MICRO, b=b_cr * MICRO)

    quote = lmsr.quote_buy_for_budget(trailing, Outcome.YES, int(query.amount_cr * MICRO))

    response = QuoteResponse(
        market_id=uuid7(),
        side=TradeSide.BUY,
        outcome=Outcome.YES,
        shares=to_cr(quote.shares_micro),
        amount_cr=to_cr(quote.amount_micro),
        average_price=1.0,
        p_yes_before=0.0,
        p_no_before=1.0,
        p_yes_after=1.0,
        p_no_after=0.0,
        quote_version=0,
    )
    assert response.shares > query.amount_cr


# --- Trade sizing and guards (A.4) --------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"side": "BUY", "outcome": "YES", "amount_cr": "0.25"},
        {"side": "BUY", "outcome": "NO", "amount_cr": "0.25", "min_shares": "0.4"},
        {"side": "BUY", "outcome": "YES", "shares": "1"},
        {"side": "BUY", "outcome": "YES", "shares": "1", "max_cost_cr": "0.6"},
        {"side": "SELL", "outcome": "YES", "shares": "1"},
        {"side": "SELL", "outcome": "NO", "shares": "1", "min_proceeds_cr": "0.4"},
        {"side": "SELL", "outcome": "NO", "shares": "1", "min_proceeds_cr": "0"},
        {"side": "BUY", "outcome": "YES", "amount_cr": "1", "quote_version": 12},
    ],
)
def test_the_three_trade_modes_and_their_guards_are_accepted(body: dict[str, Any]) -> None:
    assert TradeRequest.model_validate(body).model_dump(mode="json", exclude_none=True) == body


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"side": "BUY", "outcome": "YES"}, "exactly one of amount_cr and shares"),
        (
            {"side": "BUY", "outcome": "YES", "amount_cr": "1", "shares": "1"},
            "exactly one of amount_cr and shares",
        ),
        ({"side": "SELL", "outcome": "YES", "amount_cr": "1"}, "sized by shares"),
        (
            {"side": "BUY", "outcome": "YES", "amount_cr": "1", "max_cost_cr": "1"},
            "guarded by min_shares",
        ),
        (
            {"side": "BUY", "outcome": "YES", "shares": "1", "min_shares": "1"},
            "guarded by max_cost_cr",
        ),
        (
            {"side": "SELL", "outcome": "YES", "shares": "1", "max_cost_cr": "1"},
            "guarded by min_proceeds_cr",
        ),
        (
            {"side": "BUY", "outcome": "YES", "shares": "1", "min_proceeds_cr": "1"},
            "guarded by max_cost_cr",
        ),
    ],
)
def test_every_other_combination_is_a_validation_error(body: dict[str, Any], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        TradeRequest.model_validate(body)


@pytest.mark.parametrize(
    "body",
    [
        {"side": "BUY", "outcome": "YES", "amount_cr": "0"},
        {"side": "BUY", "outcome": "YES", "shares": "0"},
        {"side": "BUY", "outcome": "MAYBE", "amount_cr": "1"},
        {"side": "HOLD", "outcome": "YES", "amount_cr": "1"},
        {"side": "BUY", "outcome": "YES", "amount_cr": "1", "quote_version": -1},
        {"side": "BUY", "outcome": "YES", "amount_cr": "1", "quote_version": "3"},
        {"side": "BUY", "outcome": "YES", "amount_cr": "1", "leverage": 2},
    ],
)
def test_a_trade_needs_a_positive_size_known_enums_and_no_extras(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TradeRequest.model_validate(body)


def test_a_quote_is_sized_like_a_trade_but_takes_no_guard() -> None:
    assert QuoteQuery.model_validate({"side": "SELL", "outcome": "NO", "shares": "0.5"})
    with pytest.raises(ValidationError, match="sized by shares"):
        QuoteQuery.model_validate({"side": "SELL", "outcome": "NO", "amount_cr": "0.5"})
    with pytest.raises(ValidationError, match="Extra inputs"):
        QuoteQuery.model_validate(
            {"side": "BUY", "outcome": "NO", "amount_cr": "0.5", "min_shares": "1"}
        )


# --- Other request rules ------------------------------------------------------------


def proposal(**overrides: Any) -> dict[str, Any]:
    return {
        "question": "Will the Polymensa serve Älplermagronen on Friday?",
        "yes_definition": "It is on the menu.",
        "no_definition": "It is not on the menu.",
        "resolution_criteria": "The published menu of that day.",
        "resolution_source": "ethz.ch/menu",
        "close_at": (TEST_NOW + timedelta(days=7)).isoformat(),
        "resolve_by": (TEST_NOW + timedelta(days=9)).isoformat(),
        "requested_tier": "DEFAULT",
    } | overrides


def test_a_proposal_states_everything_spec_section_6_asks_for() -> None:
    request = ProposeMarketRequest.model_validate(proposal(question="  Will it snow in Zurich?  "))
    assert request.question == "Will it snow in Zurich?"
    assert request.close_at.tzinfo is not None
    for field in ProposeMarketRequest.model_fields:
        incomplete = proposal()
        del incomplete[field]
        with pytest.raises(ValidationError, match=field):
            ProposeMarketRequest.model_validate(incomplete)


@pytest.mark.parametrize(
    "overrides",
    [
        {"requested_tier": "CUSTOM"},
        {"question": "Too short"},
        {"question": "x" * 201},
        {"yes_definition": "   "},
        {"close_at": "2026-10-16T12:00:00"},
        {"resolve_by": (TEST_NOW + timedelta(days=6)).isoformat()},
    ],
    ids=["custom-tier", "short", "long", "blank", "naive-time", "resolves-before-close"],
)
def test_a_proposal_that_breaks_a_rule_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ProposeMarketRequest.model_validate(proposal(**overrides))


def test_approval_takes_a_budget_for_custom_and_only_for_custom() -> None:
    assert ApproveMarketRequest.model_validate({"tier": "DEFAULT"}).tier is MarketTier.DEFAULT
    custom = ApproveMarketRequest.model_validate({"tier": "CUSTOM", "budget_cr": "50"})
    assert custom.budget_cr == 50
    with pytest.raises(ValidationError, match="needs budget_cr"):
        ApproveMarketRequest.model_validate({"tier": "CUSTOM"})
    with pytest.raises(ValidationError, match="CUSTOM tier only"):
        ApproveMarketRequest.model_validate({"tier": "TIGHT", "budget_cr": "50"})


def test_a_code_batch_needs_strict_counts_and_flags() -> None:
    batch = {
        "label": "Erstsemestrigen-Apéro",
        "count": 50,
        "expires_at": (TEST_NOW + timedelta(days=30)).isoformat(),
        "one_per_user": True,
    }
    assert CreateCodeBatchRequest.model_validate(batch).value_cr is None
    for broken in ({"count": "50"}, {"count": 0}, {"count": 50.0}, {"one_per_user": "yes"}):
        with pytest.raises(ValidationError):
            CreateCodeBatchRequest.model_validate(batch | broken)


def test_a_page_has_a_default_and_a_maximum_size() -> None:
    assert PageQuery().limit == 50
    assert PageQuery().cursor is None
    assert PageQuery(limit=200).limit == 200
    for limit in (0, 201):
        with pytest.raises(ValidationError):
            PageQuery(limit=limit)


# --- The event stream ---------------------------------------------------------------


class ScriptedLiveQuotes:
    """Stands in for `LiveQuoteService`: plays a fixed list of events, then ends."""

    def __init__(self, events: list[LiveEvent], failure: Exception | None = None) -> None:
        self._events = events
        self._failure = failure
        self.subscribed_to: list[UUID] = []
        self.open_subscriptions = 0

    @asynccontextmanager
    async def subscribe(self, market_id: UUID) -> AsyncIterator[AsyncIterator[LiveEvent]]:
        if self._failure is not None:
            raise self._failure
        self.subscribed_to.append(market_id)
        self.open_subscriptions += 1
        try:
            yield self._play()
        finally:
            self.open_subscriptions -= 1

    async def _play(self) -> AsyncIterator[LiveEvent]:
        for event in self._events:
            yield event


@pytest.fixture
async def offline_container(base_settings: Settings) -> AsyncIterator[Container]:
    """A container that is built but never started: it connects to nothing."""
    container = Container.build(base_settings, clock=FrozenClock(), mailer=MemoryMailer())
    yield container
    await container.stop()


@pytest.fixture
def offline_app(offline_container: Container) -> FastAPI:
    return create_app(offline_container)


@pytest.fixture
async def offline_client(offline_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=offline_app), base_url="http://test"
    ) as client:
        yield client


async def test_the_stream_frames_each_event_as_a_named_server_sent_event(
    offline_client: httpx.AsyncClient,
    offline_container: Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    market_id = uuid7()
    quote = QuoteEvent(
        type="quote",
        market_id=market_id,
        version=4,
        p_yes=0.548,
        p_no=0.452,
        volume_cr=to_cr(1_250_000),
        ts=TEST_NOW,
    )
    status = StatusEvent(
        type="status",
        market_id=market_id,
        status=MarketStatus.HALTED,
        trading_open=False,
        ts=TEST_NOW,
    )
    live = ScriptedLiveQuotes([quote, Heartbeat(), status])
    monkeypatch.setattr(offline_container, "live_quotes", live)

    response = await offline_client.get(f"/api/markets/{market_id}/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert live.subscribed_to == [market_id]
    assert live.open_subscriptions == 0, "the subscription is released when the stream ends"

    frames = [frame.split("\n") for frame in response.text.strip("\n").split("\n\n")]
    assert [frame[0] for frame in frames] == ["event: quote", ": keep-alive", "event: status"]
    assert json.loads(frames[0][1].removeprefix("data: ")) == {
        "type": "quote",
        "market_id": str(market_id),
        "version": 4,
        "p_yes": 0.548,
        "p_no": 0.452,
        "volume_cr": "1.25",
        "ts": "2026-10-09T12:00:00Z",
    }
    assert json.loads(frames[2][1].removeprefix("data: "))["status"] == "HALTED"


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (ServiceUnavailableError("Live updates are unavailable."), 503, "SERVICE_UNAVAILABLE"),
        (NotFoundError(), 404, "NOT_FOUND"),
    ],
    ids=["redis-down", "unknown-market"],
)
async def test_a_stream_that_cannot_start_is_a_json_error_not_an_empty_stream(
    offline_client: httpx.AsyncClient,
    offline_container: Container,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    status_code: int,
    code: str,
) -> None:
    monkeypatch.setattr(offline_container, "live_quotes", ScriptedLiveQuotes([], failure))
    response = await offline_client.get(f"/api/markets/{uuid7()}/stream")
    assert response.status_code == status_code
    assert response.headers["content-type"] == "application/json"
    assert response.json()["code"] == code
