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

from app.api.access import DOCUMENTED_IDENTITY_HEADER
from app.api.deps import authenticate_request
from app.container import Container
from app.core.config import Settings
from app.core.enums import (
    DegreeLevel,
    MarketStatus,
    MarketTier,
    Outcome,
    TradeSide,
    UserRole,
    UserStatus,
)
from app.core.errors import ErrorCode, NotFoundError, ServiceUnavailableError
from app.core.identity import USERNAME_PATTERN, AuthenticatedUser, Caller, PortalIdentity
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
from tests.support.economy import MARKET_B
from tests.support.schema import check_constraint

type Operation = tuple[str, str]

SPEC_SECTION_10: Final[frozenset[Operation]] = frozenset(
    {
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
        ("POST", "/api/codes/redeem"),
        ("POST", "/api/vouchers"),
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
        ("POST", "/api/barista/vouchers/redeem"),
    }
)

APPENDIX_A2: Final[frozenset[Operation]] = frozenset(
    {
        ("GET", "/api/health"),
        ("GET", "/api/config"),
        ("PUT", "/api/me/profile"),
        ("GET", "/api/me/proposals"),
        ("GET", "/api/me/vouchers"),
        ("GET", "/api/vouchers/products"),
        ("POST", "/api/vouchers/{voucher_id}/code"),
        ("GET", "/api/leaderboard"),
        ("GET", "/api/admin/code-batches"),
        ("POST", "/api/admin/alerts/{alert_id}/ack"),
        ("POST", "/api/admin/grants/monthly"),
    }
)

SUCCESS_STATUS: Final[dict[Operation, str]] = {
    ("POST", "/api/markets"): "201",
    ("POST", "/api/markets/{market_id}/trades"): "201",
    ("POST", "/api/vouchers"): "201",
    ("POST", "/api/admin/code-batches"): "201",
}
"""Operations that do not answer 200: the four that create something."""

ERROR_REF: Final[dict[str, str]] = {"$ref": "#/components/schemas/ErrorResponse"}

# Who may call what (Appendix A.2), as the `security` of an operation says it.
type Security = list[dict[str, list[str]]]

NO_IDENTITY: Final[Security] = []
IDENTITY: Final[Security] = [{"portal": []}]
MEMBER: Final[Security] = [{"portal": ["REGISTERED"]}]
STAFF: Final[Security] = [{"portal": ["BARISTA"]}, {"portal": ["ADMIN"]}]
ADMIN: Final[Security] = [{"portal": ["ADMIN"]}]

PUBLIC_ACCESS: Final[frozenset[Operation]] = frozenset(
    {("GET", "/api/health"), ("GET", "/api/config")}
)
"""A.2: what needs nobody to be named."""

IDENTITY_ACCESS: Final[frozenset[Operation]] = frozenset({("PUT", "/api/me/profile")})
"""What whoever the portal named may call, with or without a profile."""

OPERATIONS_PER_ACCESS: Final[dict[str, int]] = {
    "public": 2,
    "identity": 1,
    "member": 18,
    "staff": 1,
    "admin": 13,
}
"""How the 35 operations fall into the five access levels of A.2."""

VOUCHER_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "product_id",
        "product_name",
        "status",
        "value_cr",
        "created_at",
        "expires_at",
        "redeemed_at",
    }
)
"""A voucher as its owner lists it: what it is for, never its code."""

MARKET_VIEWER_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "balance_cr",
        "holdings",
        "cost_cr",
        "proceeds_cr",
        "net_stake_cr",
        "payout_cr",
        "refund_cr",
        "stake_limit_cr",
        "stake_left_cr",
        "stake_presets",
        "sell_presets",
        "can_trade",
        "cannot_trade_reason",
        "can_settle",
    }
)
"""The viewer's side of a market: everything the market page shows of one person."""

CONFIG_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "timezone",
        "signup_grant_cr",
        "monthly_grant_cr",
        "username_min_length",
        "username_max_length",
        "degrees",
        "max_stake_per_market_cr",
        "stake_presets_cr",
        "min_trade_cr",
        "min_trade_shares",
        "price_display_decimals",
        "quote_debounce_ms",
        "live_poll_fallback_seconds",
        "tiers",
        "max_market_budget_cr",
        "max_market_lifetime_days",
        "max_resolution_delay_days",
        "max_proposals_per_day",
        "question_min_length",
        "question_max_length",
        "short_text_max_length",
        "long_text_max_length",
        "code_length",
        "code_min_value_cr",
        "code_max_value_cr",
        "code_default_value_cr",
        "max_code_issuance_per_month_cr",
        "voucher_code_length",
        "voucher_expiry_days",
    }
)
"""Everything `GET /config` tells the frontend, which reads no environment."""

LEADERBOARD_PARAMETERS: Final[frozenset[str]] = frozenset(
    {
        "sort",
        "order",
        "min_credits_cr",
        "max_credits_cr",
        "min_trades",
        "max_trades",
        "min_win_rate",
        "max_win_rate",
        "min_avg_payout_ratio",
        "max_avg_payout_ratio",
        "degree_level",
        "degree_programme",
        "limit",
        "cursor",
    }
)

LEADERBOARD_ENTRY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "rank",
        "username",
        "degree_level",
        "degree_programme",
        "credits_cr",
        "trades",
        "settled_markets",
        "win_rate",
        "avg_payout_ratio",
        "is_viewer",
    }
)

ME_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "email",
        "name",
        "username",
        "degree_level",
        "degree_programme",
        "role",
        "created_at",
        "balance_cr",
        "can_get_voucher",
        "monthly_grant_cr",
        "next_monthly_grant_on",
    }
)


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
    assert len(served) == 35


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
        "PROFILE_REQUIRED",
        "USERNAME_TAKEN",
    }


def test_every_operation_says_who_may_call_it(document: dict[str, Any]) -> None:
    """A.2 "Who may call what" is binding, and the frontend reads it from this document."""
    for operation, spec in each_operation(document):
        _, path = operation
        if operation in PUBLIC_ACCESS:
            expected = NO_IDENTITY
        elif operation in IDENTITY_ACCESS:
            expected = IDENTITY
        elif path.startswith("/api/admin/"):
            expected = ADMIN
        elif path.startswith("/api/barista/"):
            expected = STAFF
        else:
            expected = MEMBER
        assert spec.get("security", NO_IDENTITY) == expected, operation
        assert spec["description"].startswith("Access: "), operation
        assert ("public" in spec["description"]) == (operation in PUBLIC_ACCESS), operation


def test_the_operations_fall_into_the_five_access_levels(document: dict[str, Any]) -> None:
    """Two need nobody, one needs a name, eighteen a profile, one a barista, thirteen an
    admin: a route that moved to another level shows here."""
    levels = {
        json.dumps(NO_IDENTITY): "public",
        json.dumps(IDENTITY): "identity",
        json.dumps(MEMBER): "member",
        json.dumps(STAFF): "staff",
        json.dumps(ADMIN): "admin",
    }
    counted = dict.fromkeys(levels.values(), 0)
    for _, spec in each_operation(document):
        counted[levels[json.dumps(spec.get("security", NO_IDENTITY))]] += 1

    assert counted == OPERATIONS_PER_ACCESS
    assert sum(counted.values()) == 35


def test_the_one_security_scheme_is_the_header_in_which_the_portal_names_the_caller(
    document: dict[str, Any], base_settings: Settings
) -> None:
    schemes = document["components"]["securitySchemes"]
    assert list(schemes) == ["portal"]
    assert schemes["portal"] | {"type": "apiKey", "in": "header"} == schemes["portal"]
    # The document is built without settings, so it repeats the name of `.example.env`.
    assert schemes["portal"]["name"] == DOCUMENTED_IDENTITY_HEADER
    assert base_settings.identity.header_user_id == DOCUMENTED_IDENTITY_HEADER
    for requirement in ("REGISTERED", "BARISTA", "ADMIN"):
        assert f"`{requirement}`" in schemes["portal"]["description"], requirement


def test_the_profile_route_takes_a_profile_and_answers_like_me(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    save = document["paths"]["/api/me/profile"]["put"]
    sent = save["requestBody"]["content"]["application/json"]["schema"]
    answered = save["responses"]["200"]["content"]["application/json"]["schema"]
    assert sent == {"$ref": "#/components/schemas/SaveProfileRequest"}
    assert answered == {"$ref": "#/components/schemas/ProfileSavedResponse"}

    assert set(schemas["MeResponse"]["properties"]) == ME_FIELDS
    assert set(schemas["ProfileSavedResponse"]["properties"]) == ME_FIELDS | {"signup_granted"}


def test_a_profile_request_states_a_username_and_a_degree(
    schemas: dict[str, dict[str, Any]],
) -> None:
    request = schemas["SaveProfileRequest"]
    fields = request["properties"]
    assert (
        set(request["required"])
        == set(fields)
        == {
            "username",
            "degree_level",
            "degree_programme",
        }
    )
    username = fields["username"]
    assert (username["minLength"], username["maxLength"]) == (1, 32)
    # Lower case only, and the very expression the database holds the column to.
    assert username["pattern"] == USERNAME_PATTERN == "^[a-z0-9_]{1,32}$"
    check = check_constraint("users", "ck_users_username_format")
    assert str(check.sqltext) == f"username ~ '{USERNAME_PATTERN}'"
    programme = fields["degree_programme"]
    assert (programme["minLength"], programme["maxLength"]) == (1, 100)
    assert schemas["DegreeLevel"]["enum"] == [level.value for level in DegreeLevel]
    assert len(DegreeLevel) == 6


def test_config_offers_the_degree_catalogue_and_the_username_lengths(
    schemas: dict[str, dict[str, Any]],
) -> None:
    config = schemas["PublicConfigResponse"]["properties"]
    assert config["username_min_length"]["type"] == "integer"
    assert config["username_max_length"]["type"] == "integer"
    assert config["degrees"]["items"] == {"$ref": "#/components/schemas/DegreeLevelOption"}

    option = schemas["DegreeLevelOption"]["properties"]
    assert set(option) == {"level", "name", "programmes"}
    assert option["programmes"]["items"] == {"type": "string"}, "a programme is its label"


def test_config_tells_the_frontend_exactly_these_things(
    schemas: dict[str, dict[str, Any]],
) -> None:
    config = schemas["PublicConfigResponse"]["properties"]
    assert set(config) == CONFIG_FIELDS
    # How long the texts of a proposal may be, so the form can say so before a 422 does.
    for limit in (
        "question_min_length",
        "question_max_length",
        "short_text_max_length",
        "long_text_max_length",
    ):
        assert config[limit]["type"] == "integer", limit
    # The request model holds a proposal to what `/config` announces.
    proposal = schemas["ProposeMarketRequest"]["properties"]
    assert (proposal["question"]["minLength"], proposal["question"]["maxLength"]) == (10, 200)
    assert proposal["resolution_source"]["maxLength"] == 500
    assert proposal["resolution_criteria"]["maxLength"] == 2000


def test_a_market_always_comes_with_the_viewers_side(schemas: dict[str, dict[str, Any]]) -> None:
    """Every reader of a market is a user, so `viewer` is an object, never null."""
    viewer = schemas["MarketDetailResponse"]["properties"]["viewer"]
    assert viewer["$ref"] == "#/components/schemas/MarketViewer"
    assert "anyOf" not in viewer
    assert schemas["TradeBlockReason"]["enum"] == ["MARKET_NOT_OPEN", "OWN_MARKET"]


def test_the_viewers_side_says_what_a_settled_market_paid_and_who_may_settle_it(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """The market page shows `payout_cr` or `refund_cr` and the admin screen reads
    `can_settle`; the frontend derives none of the three."""
    viewer = schemas["MarketViewer"]["properties"]
    assert set(viewer) == MARKET_VIEWER_FIELDS

    for amount in ("payout_cr", "refund_cr"):
        string, null = viewer[amount]["anyOf"]
        assert string["type"] == "string", amount
        assert null == {"type": "null"}, amount
    assert viewer["can_settle"]["type"] == "boolean"
    assert viewer["can_trade"]["type"] == "boolean"
    # The portfolio reports the same two amounts for the same market.
    entry = schemas["PortfolioEntry"]["properties"]
    assert entry["payout_cr"]["anyOf"] == viewer["payout_cr"]["anyOf"]
    assert entry["refund_cr"]["anyOf"] == viewer["refund_cr"]["anyOf"]


def test_the_market_list_takes_the_status_any_number_of_times(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    """`?status=OPEN&status=HALTED`: one request lists what a filter chip stands for."""
    listing = document["paths"]["/api/markets"]["get"]
    (status,) = listing["parameters"]
    assert (status["name"], status["in"], status["required"]) == ("status", "query", False)
    several, none = status["schema"]["anyOf"]
    assert several == {"type": "array", "items": {"$ref": "#/components/schemas/MarketStatus"}}
    assert none == {"type": "null"}
    assert schemas["MarketStatus"]["enum"] == [
        "PROPOSED",
        "REJECTED",
        "OPEN",
        "HALTED",
        "CLOSED",
        "RESOLVED",
        "VOID",
    ]
    answered = listing["responses"]["200"]["content"]["application/json"]["schema"]
    assert answered == {"$ref": "#/components/schemas/MarketListResponse"}


def test_the_risk_page_says_what_voiding_a_market_would_cost(
    schemas: dict[str, dict[str, Any]],
) -> None:
    row = schemas["RiskMarketRow"]["properties"]
    assert {"exposure_cr", "void_cost_cr", "account_balance_cr", "budget_cr"} <= set(row)
    assert row["void_cost_cr"]["type"] == "string"
    assert row["void_cost_cr"]["pattern"] == row["exposure_cr"]["pattern"], "signed, like exposure"
    assert row["void_cost_cr"]["pattern"].startswith("^-?")


def test_the_leaderboard_takes_its_order_and_its_filters_as_query_parameters(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    board = document["paths"]["/api/leaderboard"]["get"]
    assert "requestBody" not in board
    parameters = {parameter["name"]: parameter for parameter in board["parameters"]}
    assert set(parameters) == LEADERBOARD_PARAMETERS
    assert all(parameter["in"] == "query" for parameter in parameters.values())
    assert not any(parameter["required"] for parameter in parameters.values())

    def kinds(name: str) -> list[dict[str, Any]]:
        """The alternatives of an optional parameter other than null."""
        return [
            option for option in parameters[name]["schema"]["anyOf"] if option != {"type": "null"}
        ]

    assert parameters["sort"]["schema"]["$ref"] == "#/components/schemas/LeaderboardSort"
    assert parameters["sort"]["schema"]["default"] == "credits"
    assert parameters["order"]["schema"]["$ref"] == "#/components/schemas/SortOrder"
    assert parameters["order"]["schema"]["default"] == "desc"
    assert schemas["LeaderboardSort"]["enum"] == [
        "credits",
        "win_rate",
        "trades",
        "avg_payout_ratio",
    ]
    assert schemas["SortOrder"]["enum"] == ["asc", "desc"]
    for credits in ("min_credits_cr", "max_credits_cr"):
        (amount,) = kinds(credits)
        assert amount["type"] == "string", credits
        assert amount["pattern"] == "^[0-9]{1,12}(\\.[0-9]{1,6})?$", credits
    for trades in ("min_trades", "max_trades"):
        (count,) = kinds(trades)
        assert (count["type"], count["minimum"]) == ("integer", 0), trades
    for rate in ("min_win_rate", "max_win_rate"):
        assert kinds(rate) == [{"type": "number", "minimum": 0, "maximum": 1}], rate
    for ratio in ("min_avg_payout_ratio", "max_avg_payout_ratio"):
        (number,) = kinds(ratio)
        assert (number["type"], number["minimum"]) == ("number", 0), ratio
    assert kinds("degree_level") == [{"$ref": "#/components/schemas/DegreeLevel"}]
    (programme,) = kinds("degree_programme")
    assert (programme["type"], programme["maxLength"]) == ("string", 100)
    assert parameters["limit"]["schema"]["maximum"] == 200


def test_a_leaderboard_row_names_nobody_and_carries_rates_as_nullable_numbers(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    board = document["paths"]["/api/leaderboard"]["get"]
    answered = board["responses"]["200"]["content"]["application/json"]["schema"]
    assert answered == {"$ref": "#/components/schemas/LeaderboardResponse"}

    response = schemas["LeaderboardResponse"]["properties"]
    assert set(response) == {"items", "next_cursor", "viewer"}, "no total: the list is paged"
    assert response["items"]["items"] == {"$ref": "#/components/schemas/LeaderboardEntry"}
    assert response["viewer"]["anyOf"] == [
        {"$ref": "#/components/schemas/LeaderboardEntry"},
        {"type": "null"},
    ]

    entry = schemas["LeaderboardEntry"]["properties"]
    assert set(entry) == LEADERBOARD_ENTRY_FIELDS
    assert entry["credits_cr"]["type"] == "string"
    for count in ("rank", "trades", "settled_markets"):
        assert entry[count]["type"] == "integer", count
    assert entry["win_rate"]["anyOf"] == [
        {"type": "number", "minimum": 0.0, "maximum": 1.0},
        {"type": "null"},
    ]
    assert entry["avg_payout_ratio"]["anyOf"] == [
        {"type": "number", "minimum": 0.0},
        {"type": "null"},
    ]
    assert entry["is_viewer"]["type"] == "boolean"
    assert entry["degree_level"] == {"$ref": "#/components/schemas/DegreeLevel"}


def test_the_error_schema_says_what_details_carries(schemas: dict[str, dict[str, Any]]) -> None:
    """`details` is a free object; its keys per code are in the description."""
    description = schemas["ErrorResponse"]["description"]
    for code in (
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.FORBIDDEN,
        ErrorCode.PROFILE_REQUIRED,
        ErrorCode.USERNAME_TAKEN,
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
        "name",
        "email",
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
    assert "/wallet?code=" in description
    assert "CODE_INVALID" in description
    assert "/wallet?code=" in schemas["CreatedCode"]["properties"]["redeem_url"]["description"]
    assert set(schemas["RedeemCodeRequest"]["properties"]) == {"code"}


def test_a_voucher_is_issued_for_a_product_and_every_answer_names_it(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    issue = document["paths"]["/api/vouchers"]["post"]
    assert issue["requestBody"]["required"] is True
    sent = issue["requestBody"]["content"]["application/json"]["schema"]
    assert sent == {"$ref": "#/components/schemas/IssueVoucherRequest"}

    request = schemas["IssueVoucherRequest"]
    assert set(request["required"]) == set(request["properties"]) == {"product_id"}
    product_id = request["properties"]["product_id"]
    assert (product_id["minLength"], product_id["maxLength"]) == (1, 64)

    assert set(schemas["VoucherItem"]["properties"]) == VOUCHER_FIELDS
    assert set(schemas["VoucherIssuedResponse"]["properties"]) == VOUCHER_FIELDS | {
        "code",
        "balance_cr",
    }
    assert set(schemas["VoucherRedeemResponse"]["properties"]) == {
        "voucher_id",
        "product_id",
        "product_name",
        "value_cr",
        "redeemed_at",
    }


def test_a_barista_sends_the_code_of_a_voucher_in_the_body(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    redeem = document["paths"]["/api/barista/vouchers/redeem"]["post"]
    assert "parameters" not in redeem
    assert redeem["requestBody"]["required"] is True
    sent = redeem["requestBody"]["content"]["application/json"]["schema"]
    assert sent == {"$ref": "#/components/schemas/RedeemVoucherRequest"}
    answered = redeem["responses"]["200"]["content"]["application/json"]["schema"]
    assert answered == {"$ref": "#/components/schemas/VoucherRedeemResponse"}

    request = schemas["RedeemVoucherRequest"]
    assert set(request["required"]) == set(request["properties"]) == {"code"}
    code = request["properties"]["code"]
    assert (code["type"], code["minLength"], code["maxLength"]) == ("string", 1, 64)
    assert code == schemas["RedeemCodeRequest"]["properties"]["code"]


def test_no_operation_takes_a_code_in_its_path_or_its_query(document: dict[str, Any]) -> None:
    """The address of a request is what proxies and logs write down. A code that is
    worth credits, or a product, crosses the API in a body, in both directions."""
    for path in document["paths"]:
        assert "code}" not in path, path
    for operation, spec in each_operation(document):
        for parameter in spec.get("parameters", []):
            assert "code" not in parameter["name"].lower(), (operation, parameter["name"])
    # The one path that speaks of a code names the voucher by its id and takes no body.
    assert [path for path in document["paths"] if path.endswith("/code")] == [
        "/api/vouchers/{voucher_id}/code"
    ]


def test_the_products_say_what_each_takes_and_whether_the_caller_can_have_it(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    """The frontend compares no balance with a price: `affordable` and
    `can_get_voucher` are the comparisons, made by the server for the caller."""
    listing = document["paths"]["/api/vouchers/products"]["get"]
    assert "parameters" not in listing
    answered = listing["responses"]["200"]["content"]["application/json"]["schema"]
    assert answered == {"$ref": "#/components/schemas/VoucherProductListResponse"}
    items = schemas["VoucherProductListResponse"]["properties"]["items"]
    assert items["items"] == {"$ref": "#/components/schemas/VoucherProductItem"}

    product = schemas["VoucherProductItem"]["properties"]
    assert set(product) == {"id", "name", "price_cr", "affordable"}
    assert product["affordable"]["type"] == "boolean"
    assert schemas["MeResponse"]["properties"]["can_get_voucher"]["type"] == "boolean"
    # What a voucher takes depends on the product and on who asks, so `/config` says
    # two things about vouchers and neither is a price.
    config = schemas["PublicConfigResponse"]["properties"]
    assert {field for field in config if "voucher" in field or "product" in field} == {
        "voucher_code_length",
        "voucher_expiry_days",
    }


def test_a_voucher_gets_a_fresh_code_from_a_route_that_takes_no_body(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    """The one way to see a code again: nothing but the voucher's id goes in, and the
    code comes back in this answer alone. The wallet's list never holds one."""
    replace = document["paths"]["/api/vouchers/{voucher_id}/code"]["post"]
    assert "requestBody" not in replace
    (parameter,) = replace["parameters"]
    assert (parameter["name"], parameter["in"], parameter["required"]) == (
        "voucher_id",
        "path",
        True,
    )
    assert parameter["schema"] | {"type": "string", "format": "uuid"} == parameter["schema"]
    answered = replace["responses"]["200"]["content"]["application/json"]["schema"]
    assert answered == {"$ref": "#/components/schemas/VoucherCodeResponse"}

    assert set(schemas["VoucherCodeResponse"]["properties"]) == {
        "voucher_id",
        "code",
        "expires_at",
    }
    assert "code" not in schemas["VoucherItem"]["properties"]
    assert schemas["VoucherListResponse"]["properties"]["items"]["items"] == {
        "$ref": "#/components/schemas/VoucherItem"
    }


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


@pytest.mark.parametrize("b_micro", [MICRO, *MARKET_B])
def test_the_largest_budget_a_request_accepts_fits_into_a_quote_response(b_micro: int) -> None:
    """Found in review: the largest accepted budget bought more shares than
    `QuoteResponse` could carry, a 500 on the quote route. Tried at a b of 1 CR, at
    the b of each fixed tier and at the largest b a market can have."""
    query = QuoteQuery.model_validate(
        {"side": "BUY", "outcome": "YES", "amount_cr": "999999999.999999"}
    )
    assert query.amount_cr is not None
    trailing = lmsr.MarketState(q_yes=0, q_no=1000 * MICRO, b=b_micro)

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
    container = Container.build(base_settings, clock=FrozenClock())
    yield container
    await container.stop()


@pytest.fixture
def offline_app(offline_container: Container) -> FastAPI:
    """The application on that container, called by a registered user: the stream is
    for users, and who is calling would otherwise be asked of Postgres."""
    app = create_app(offline_container)
    user = AuthenticatedUser(
        id=uuid7(),
        email="viewer@ethz.ch",
        name="Vera Viewer",
        username="viewer",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    identity = PortalIdentity(email=user.email, name=user.name, role=user.role)
    app.dependency_overrides[authenticate_request] = lambda: Caller(identity=identity, user=user)
    return app


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
