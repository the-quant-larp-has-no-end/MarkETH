"""The API contract: what `backend/openapi.json` promises the frontend (SPEC §10, A.2, A.4).

The frontend's types are generated from the OpenAPI document, so these tests pin what
that document must say: every endpoint of the spec, one error shape, amounts as strings,
and a committed file that matches the code.

Nothing here needs Docker: the application is built without a data store behind it, the
way `scripts/export_openapi.py` builds it. The module therefore carries no `integration`
marker.
"""

import json
import time
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
    BroadcastEndReason,
    BroadcastStatus,
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
from app.core.live import (
    TOPIC_MAX_LENGTH,
    TOPICS_HARD_MAX,
    Audience,
    Topic,
    TopicKind,
)
from app.core.live import LiveEvent as AnnouncedEvent
from app.core.text import CHAT_MESSAGE_STORED_MAX_LENGTH
from app.core.units import MICRO, format_cr, to_cr
from app.main import create_app
from app.pricing import lmsr
from app.schemas.admin.code_batches import (
    LABEL_MAX_LENGTH,
    MAX_CODES_PER_BATCH,
    CreateCodeBatchRequest,
)
from app.schemas.admin.markets import (
    BUDGET_NEEDED_MESSAGE,
    BUDGET_NOT_TAKEN_MESSAGE,
    ApproveMarketRequest,
)
from app.schemas.admin.users import AdjustBalanceRequest, AdminUserListQuery
from app.schemas.broadcasts import (
    CAPTURE_MIME_TYPES,
    PART_PATTERN,
    SEQ_LIMIT,
    BroadcastChunkQuery,
    RefuseBroadcastRequest,
    RequestBroadcastRequest,
)
from app.schemas.chat import ChatMessageItem, PostChatMessageRequest
from app.schemas.common import (
    MAX_ERRORS_LISTED,
    CreditAmount,
    ErrorResponse,
    Limit,
    NonZeroCreditAmount,
    PageQuery,
    PositiveCreditAmount,
    SignedCreditAmount,
)
from app.schemas.live import (
    LIVE_EVENTS,
    TAB_PATTERN,
    BeatEvent,
    BroadcastChangedEvent,
    ChatMessageEvent,
    ChatRemovedEvent,
    LeaderboardChangedEvent,
    LiveStreamQuery,
    MarketQuoteEvent,
    MarketStatusEvent,
    MeChangedEvent,
    QuoteEvent,
    ReadyEvent,
    StatusEvent,
)
from app.schemas.markets import ProposeMarketRequest
from app.schemas.profile import SaveProfileRequest
from app.schemas.trading import (
    GUARD_DOES_NOT_APPLY_MESSAGE,
    SELL_BY_SHARES_MESSAGE,
    SIZED_ONE_WAY_MESSAGE,
    QuoteQuery,
    QuoteResponse,
    TradeRequest,
)
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
        ("GET", "/api/stream"),
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
        ("POST", "/api/me/notices/{notice_id}/seen"),
        ("GET", "/api/me/vouchers"),
        ("GET", "/api/vouchers/products"),
        ("POST", "/api/vouchers/{voucher_id}/code"),
        ("GET", "/api/leaderboard"),
        ("GET", "/api/admin/code-batches"),
        ("GET", "/api/admin/code-batches/{batch_id}/codes"),
        ("POST", "/api/admin/alerts/{alert_id}/ack"),
        ("POST", "/api/admin/grants/monthly"),
        ("GET", "/api/admin/users"),
        ("POST", "/api/admin/users/{user_id}/suspend"),
        ("POST", "/api/admin/users/{user_id}/reinstate"),
        ("POST", "/api/admin/users/{user_id}/role"),
        ("POST", "/api/admin/users/{user_id}/adjustments"),
        ("GET", "/api/markets/{market_id}/chat"),
        ("POST", "/api/markets/{market_id}/chat"),
        ("POST", "/api/admin/markets/{market_id}/chat/{message_id}/remove"),
        ("GET", "/api/markets/{market_id}/broadcast"),
        ("POST", "/api/markets/{market_id}/broadcast/request"),
        ("POST", "/api/broadcasts/{broadcast_id}/start"),
        ("POST", "/api/broadcasts/{broadcast_id}/chunks"),
        ("POST", "/api/broadcasts/{broadcast_id}/end"),
        ("GET", "/api/broadcasts/live"),
        ("GET", "/api/broadcasts/{broadcast_id}/access"),
        ("GET", "/api/admin/broadcasts"),
        ("POST", "/api/admin/broadcasts/{broadcast_id}/approve"),
        ("POST", "/api/admin/broadcasts/{broadcast_id}/refuse"),
        ("POST", "/api/admin/markets/{market_id}/broadcast"),
    }
)

SUCCESS_STATUS: Final[dict[Operation, str]] = {
    ("POST", "/api/markets"): "201",
    ("POST", "/api/markets/{market_id}/trades"): "201",
    ("POST", "/api/markets/{market_id}/chat"): "201",
    ("POST", "/api/markets/{market_id}/broadcast/request"): "201",
    ("POST", "/api/vouchers"): "201",
    ("POST", "/api/admin/code-batches"): "201",
    ("POST", "/api/admin/users/{user_id}/adjustments"): "201",
    ("POST", "/api/admin/markets/{market_id}/broadcast"): "201",
}
"""Operations that do not answer 200: the eight that create something."""

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
    "member": 29,
    "staff": 1,
    "admin": 24,
}
"""How the 57 operations fall into the five access levels of A.2."""

VOUCHER_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "product_id",
        "product_name",
        "status",
        "refunded",
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
        "buy",
        "stake_presets",
        "buy_slider",
        "can_buy",
        "sell_presets",
        "sell",
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
        "share_presets",
        "share_step",
        "stake_presets_cr",
        "stake_step_cr",
        "min_trade_cr",
        "min_trade_shares",
        "price_display_decimals",
        "quote_debounce_ms",
        "live_poll_fallback_seconds",
        "live_heartbeat_seconds",
        "live_announce_spread_ms",
        "live_viewer_refresh_seconds",
        "tiers",
        "max_market_budget_cr",
        "max_market_lifetime_days",
        "max_resolution_delay_days",
        "max_proposals_per_day",
        "question_min_length",
        "question_max_length",
        "short_text_max_length",
        "long_text_max_length",
        "chat_message_max_length",
        "chat_backlog_size",
        "broadcast",
        "code_length",
        "code_min_value_cr",
        "code_max_value_cr",
        "code_default_value_cr",
        "max_code_issuance_per_month_cr",
        "code_batch_max_count",
        "code_label_max_length",
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
        "balance_cr",
        "positions_value_cr",
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
        "policy_accepted_at",
        "balance_cr",
        "can_get_voucher",
        "monthly_grant_cr",
        "next_monthly_grant_at",
        "notices",
    }
)

ADMIN_USER_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "email",
        "name",
        "username",
        "degree_level",
        "degree_programme",
        "role",
        "status",
        "bootstrap_admin",
        "balance_cr",
        "created_at",
    }
)
"""A user as the admin panel lists them."""


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
    assert len(served) == 57


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
    """Two need nobody, one needs a name, twenty-nine a profile, one a barista,
    twenty-four an admin: a route that moved to another level shows here."""
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
    assert sum(counted.values()) == 57


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


def test_a_profile_request_states_a_username_a_degree_and_whether_the_policy_is_accepted(
    schemas: dict[str, dict[str, Any]],
) -> None:
    request = schemas["SaveProfileRequest"]
    fields = request["properties"]
    assert set(request["required"]) == {"username", "degree_level", "degree_programme"}
    assert set(fields) == set(request["required"]) | {"policy_accepted"}
    # A plain boolean that may be left out: a save that edits a profile sends none, and
    # whether a save needs it is the service's to say.
    policy = fields["policy_accepted"]
    assert (policy["type"], policy["default"]) == ("boolean", False)
    assert "const" not in policy
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


def test_me_says_when_the_policy_was_accepted_and_when_the_next_grant_falls_due(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """Two instants. The frontend formats the second in the viewer's own timezone and
    works out no date of its own."""
    me = schemas["MeResponse"]["properties"]
    for instant in ("policy_accepted_at", "next_monthly_grant_at", "created_at"):
        assert (me[instant]["type"], me[instant]["format"]) == ("string", "date-time"), instant
    assert me["role"] == {"$ref": "#/components/schemas/UserRole"} | {
        "description": me["role"]["description"]
    }
    assert schemas["UserRole"]["enum"] == ["USER", "ADMIN", "BARISTA"]


def test_a_proposal_carries_the_acceptance_of_the_policy_and_only_true_is_one(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """`const: true`: the generated client cannot send a proposal that declines."""
    proposal = schemas["ProposeMarketRequest"]
    assert "policy_accepted" in proposal["required"]
    policy = proposal["properties"]["policy_accepted"]
    assert (policy["type"], policy["const"]) == ("boolean", True)


def test_the_admin_lists_users_by_a_text_a_role_and_a_status_page_by_page(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    listing = document["paths"]["/api/admin/users"]["get"]
    assert "requestBody" not in listing
    parameters = {parameter["name"]: parameter for parameter in listing["parameters"]}
    assert set(parameters) == {"q", "role", "status", "limit", "cursor"}
    assert all(parameter["in"] == "query" for parameter in parameters.values())
    assert not any(parameter["required"] for parameter in parameters.values())

    def kinds(name: str) -> list[dict[str, Any]]:
        """The alternatives of an optional parameter other than null."""
        return [
            option for option in parameters[name]["schema"]["anyOf"] if option != {"type": "null"}
        ]

    assert kinds("q") == [{"type": "string", "minLength": 1, "maxLength": 100}]
    assert kinds("role") == [{"$ref": "#/components/schemas/UserRole"}]
    assert kinds("status") == [{"$ref": "#/components/schemas/UserStatus"}]
    assert schemas["UserStatus"]["enum"] == ["ACTIVE", "SUSPENDED"]
    assert parameters["limit"]["schema"]["maximum"] == 200

    answered = listing["responses"]["200"]["content"]["application/json"]["schema"]
    assert answered == {"$ref": "#/components/schemas/AdminUserListResponse"}
    page = schemas["AdminUserListResponse"]["properties"]
    assert set(page) == {"items", "next_cursor"}
    assert page["items"]["items"] == {"$ref": "#/components/schemas/AdminUserItem"}

    user = schemas["AdminUserItem"]["properties"]
    assert set(user) == ADMIN_USER_FIELDS
    assert user["bootstrap_admin"]["type"] == "boolean"
    assert user["balance_cr"]["type"] == "string"
    assert user["status"] == {"$ref": "#/components/schemas/UserStatus"}


def test_the_three_actions_on_an_account_take_a_small_body_and_answer_with_the_user(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    bodies = {
        "suspend": ("SuspendUserRequest", {"reason"}),
        "reinstate": ("ReinstateUserRequest", {"reason"}),
        "role": ("SetUserRoleRequest", {"role"}),
    }
    for action, (request, fields) in bodies.items():
        operation = document["paths"][f"/api/admin/users/{{user_id}}/{action}"]["post"]
        (parameter,) = operation["parameters"]
        assert (parameter["name"], parameter["in"], parameter["required"]) == (
            "user_id",
            "path",
            True,
        )
        assert parameter["schema"] | {"type": "string", "format": "uuid"} == parameter["schema"]
        sent = operation["requestBody"]["content"]["application/json"]["schema"]
        assert sent == {"$ref": f"#/components/schemas/{request}"}, action
        assert set(schemas[request]["required"]) == set(schemas[request]["properties"]) == fields
        answered = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert answered == {"$ref": "#/components/schemas/AdminUserItem"}, action
    for request in ("SuspendUserRequest", "ReinstateUserRequest"):
        reason = schemas[request]["properties"]["reason"]
        assert (reason["type"], reason["minLength"], reason["maxLength"]) == ("string", 1, 500)
    assert schemas["SetUserRoleRequest"]["properties"]["role"]["$ref"].endswith("/UserRole")


def test_an_adjustment_is_a_signed_amount_with_a_reason_under_an_idempotency_key(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    adjust = document["paths"]["/api/admin/users/{user_id}/adjustments"]["post"]
    (header,) = [p for p in adjust["parameters"] if p["in"] == "header"]
    assert (header["name"], header["required"]) == ("Idempotency-Key", True)
    assert header["schema"] | {"type": "string", "format": "uuid"} == header["schema"]
    sent = adjust["requestBody"]["content"]["application/json"]["schema"]
    assert sent == {"$ref": "#/components/schemas/AdjustBalanceRequest"}
    answered = adjust["responses"]["201"]["content"]["application/json"]["schema"]
    assert answered == {"$ref": "#/components/schemas/BalanceAdjustmentResponse"}

    request = schemas["AdjustBalanceRequest"]
    assert set(request["required"]) == set(request["properties"]) == {"amount_cr", "reason"}
    amount = request["properties"]["amount_cr"]
    assert (amount["type"], amount["pattern"]) == ("string", "^-?[0-9]{1,9}(\\.[0-9]{1,6})?$")
    reason = request["properties"]["reason"]
    assert (reason["minLength"], reason["maxLength"]) == (1, 500)

    booked = schemas["BalanceAdjustmentResponse"]["properties"]
    assert set(booked) == {"user_id", "amount_cr", "balance_cr", "created_at"}
    assert booked["amount_cr"]["pattern"].startswith("^-?"), "signed: a debit is negative"
    assert not booked["balance_cr"]["pattern"].startswith("^-?")
    assert "ADMIN_ADJUSTMENT" in schemas["JournalKind"]["enum"]


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


def test_config_announces_the_bounds_the_batch_request_holds_a_batch_to(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """How many codes and how long a label: the form says so before a 422 does, and
    each figure is the one constant the request model reads."""
    config = schemas["PublicConfigResponse"]["properties"]
    for bound in ("code_batch_max_count", "code_label_max_length"):
        assert config[bound]["type"] == "integer", bound
        assert bound in schemas["PublicConfigResponse"]["required"], bound

    batch = schemas["CreateCodeBatchRequest"]["properties"]
    assert (batch["count"]["minimum"], batch["count"]["maximum"]) == (1, MAX_CODES_PER_BATCH)
    assert (batch["label"]["minLength"], batch["label"]["maxLength"]) == (1, LABEL_MAX_LENGTH)
    count, label = (CreateCodeBatchRequest.model_fields[name] for name in ("count", "label"))
    assert MAX_CODES_PER_BATCH in [getattr(rule, "le", None) for rule in count.metadata]
    assert LABEL_MAX_LENGTH in [getattr(rule, "max_length", None) for rule in label.metadata]


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


def test_the_viewers_side_carries_the_slider_of_a_buy_in_whole_credits(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """`{min, max, step}` as JSON integers, which is what a range input takes, or null
    where the viewer cannot buy. The step is also in `/config`, as an amount."""
    viewer = schemas["MarketViewer"]["properties"]
    assert viewer["buy_slider"]["anyOf"] == [
        {"$ref": "#/components/schemas/BuySlider"},
        {"type": "null"},
    ]
    assert "buy_slider" in schemas["MarketViewer"]["required"]

    slider = schemas["BuySlider"]
    assert set(slider["required"]) == set(slider["properties"]) == {"min", "max", "step"}
    for position in ("min", "max", "step"):
        assert slider["properties"][position]["type"] == "integer", position

    config = schemas["PublicConfigResponse"]["properties"]
    assert config["stake_step_cr"]["type"] == "string"
    assert config["stake_step_cr"]["pattern"] == config["min_trade_cr"]["pattern"]
    # The buttons stay beside it.
    assert viewer["stake_presets"]["items"] == {"$ref": "#/components/schemas/StakePreset"}


def test_the_viewers_side_says_what_can_be_bought_of_each_outcome_in_shares(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """`buy` has an entry per outcome: where the slider of a buy runs, in whole shares
    as JSON integers or null, and one shortcut per entry of `share_presets`. The
    shortcuts and the step are in `/config` too, as numbers of shares."""
    viewer = schemas["MarketViewer"]
    assert viewer["properties"]["buy"]["type"] == "array"
    assert viewer["properties"]["buy"]["items"] == {"$ref": "#/components/schemas/BuyEntry"}
    assert "buy" in viewer["required"]

    entry = schemas["BuyEntry"]
    assert set(entry["required"]) == set(entry["properties"]) == {"outcome", "slider", "presets"}
    assert entry["properties"]["outcome"] == {"$ref": "#/components/schemas/Outcome"}
    assert entry["properties"]["slider"]["anyOf"] == [
        {"$ref": "#/components/schemas/BuySlider"},
        {"type": "null"},
    ]
    assert entry["properties"]["presets"]["type"] == "array"
    assert entry["properties"]["presets"]["items"] == {"$ref": "#/components/schemas/BuyPreset"}

    preset = schemas["BuyPreset"]
    assert set(preset["required"]) == set(preset["properties"]) == {"shares", "allowed"}
    assert preset["properties"]["allowed"]["type"] == "boolean"
    shares = preset["properties"]["shares"]
    assert shares["type"] == "string"

    config = schemas["PublicConfigResponse"]
    for told in ("share_presets", "share_step"):
        assert told in config["required"], told
    assert config["properties"]["share_presets"]["type"] == "array"
    for a_number_of_shares in (
        config["properties"]["share_presets"]["items"],
        config["properties"]["share_step"],
    ):
        assert a_number_of_shares["type"] == "string"
        assert a_number_of_shares["pattern"] == shares["pattern"]


def test_a_sell_button_says_how_many_shares_it_sells_and_what_they_bring_in(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """The proceeds are the server's, at the price of the answer: the client prices
    nothing."""
    preset = schemas["SellPreset"]
    assert (
        set(preset["required"])
        == set(preset["properties"])
        == {"outcome", "portion", "shares", "proceeds_cr", "allowed"}
    )
    proceeds = preset["properties"]["proceeds_cr"]
    assert proceeds["type"] == "string"
    assert proceeds["pattern"] == schemas["QuoteResponse"]["properties"]["amount_cr"]["pattern"]
    assert "`GET /markets/{id}/quote`" in proceeds["description"]


def test_a_quote_says_what_its_shares_pay(schemas: dict[str, dict[str, Any]]) -> None:
    """`payout_cr` is the shares of the trade in credits, so the sheet shows what a buy
    can pay without multiplying anything."""
    quote = schemas["QuoteResponse"]
    assert "payout_cr" in quote["required"]
    payout = quote["properties"]["payout_cr"]
    assert payout["type"] == "string"
    assert payout["pattern"] == quote["properties"]["amount_cr"]["pattern"]
    assert "1 CR per share" in payout["description"]
    assert "price per share" in quote["properties"]["average_price"]["description"]


def test_config_tells_the_live_client_its_paces(schemas: dict[str, dict[str, Any]]) -> None:
    """How often a stream beats, over how long tabs spread their reads, how soon a
    holder's market is read again, and how often a tab without a stream reads."""
    config = schemas["PublicConfigResponse"]
    for pace in (
        "live_heartbeat_seconds",
        "live_announce_spread_ms",
        "live_viewer_refresh_seconds",
        "live_poll_fallback_seconds",
    ):
        assert config["properties"][pace]["type"] == "integer", pace
        assert pace in config["required"], pace


def test_a_proposal_asks_for_no_liquidity_and_a_market_tells_it_to_admins_only(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """Nothing a user sends or reads names a tier they could pick. The three liquidity
    figures of a market are always sent and are null to whoever is no admin."""
    proposal = schemas["ProposeMarketRequest"]
    assert (
        set(proposal["properties"])
        == set(proposal["required"])
        == {
            "question",
            "yes_definition",
            "no_definition",
            "resolution_criteria",
            "resolution_source",
            "close_at",
            "resolve_by",
            "policy_accepted",
        }
    )

    detail = schemas["MarketDetailResponse"]
    assert "requested_tier" not in detail["properties"]
    assert detail["properties"]["tier"]["anyOf"] == [
        {"$ref": "#/components/schemas/MarketTier"},
        {"type": "null"},
    ]
    for amount in ("b_cr", "max_loss_cr"):
        string, null = detail["properties"][amount]["anyOf"]
        assert (string["type"], null) == ("string", {"type": "null"}), amount
    for told_to_admins in ("tier", "b_cr", "max_loss_cr", "void_cost_cr", "proposer_email"):
        assert told_to_admins in detail["required"], told_to_admins
        assert "admins only" in detail["properties"][told_to_admins]["description"]

    liquidity = {"requested_tier", "tier", "b_cr", "max_loss_cr", "void_cost_cr", "budget_cr"}
    for told_to_everybody in ("ProposalItem", "MarketSummary", "MarketViewer", "PortfolioEntry"):
        assert liquidity.isdisjoint(schemas[told_to_everybody]["properties"]), told_to_everybody
    # The approval is where a tier is picked, by an admin, from what `/config` lists.
    assert "tier" in schemas["ApproveMarketRequest"]["required"]
    assert schemas["PublicConfigResponse"]["properties"]["tiers"]["items"] == {
        "$ref": "#/components/schemas/TierConfig"
    }


def test_a_quote_takes_the_size_of_a_trade_and_is_for_the_caller(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    """Side, outcome and one of amount and shares: no guard, no key and no body. Who it
    is for is the caller, so the answer names the errors their trade would get."""
    quote = document["paths"]["/api/markets/{market_id}/quote"]["get"]
    assert "requestBody" not in quote
    assert {(parameter["name"], parameter["in"]) for parameter in quote["parameters"]} == {
        ("market_id", "path"),
        ("side", "query"),
        ("outcome", "query"),
        ("amount_cr", "query"),
        ("shares", "query"),
    }
    assert quote["security"] == MEMBER
    answered = quote["responses"]["200"]["content"]["application/json"]["schema"]
    assert answered == {"$ref": "#/components/schemas/QuoteResponse"}
    described = schemas["QuoteResponse"]["description"]
    for code in (
        ErrorCode.FORBIDDEN,
        ErrorCode.MARKET_NOT_OPEN,
        ErrorCode.INSUFFICIENT_SHARES,
        ErrorCode.TRADE_TOO_SMALL,
        ErrorCode.LIMIT_EXCEEDED,
        ErrorCode.INSUFFICIENT_BALANCE,
    ):
        assert f"`{code.value}`" in described, code


def test_an_admin_opens_the_codes_of_a_batch_again_by_its_id(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    """A read that takes nothing but the batch, and answers in the shape the creation
    answered in, so the card sheet is built from either."""
    read = document["paths"]["/api/admin/code-batches/{batch_id}/codes"]["get"]
    assert "requestBody" not in read
    (parameter,) = read["parameters"]
    assert (parameter["name"], parameter["in"], parameter["required"]) == (
        "batch_id",
        "path",
        True,
    )
    assert parameter["schema"] | {"type": "string", "format": "uuid"} == parameter["schema"]
    assert read["security"] == ADMIN
    assert "audited" in read["summary"]
    answered = read["responses"]["200"]["content"]["application/json"]["schema"]
    assert answered == {"$ref": "#/components/schemas/CodeBatchCodesResponse"}

    def shape(schema: str) -> dict[str, Any]:
        """The fields of a schema without what is written about them."""
        return {
            name: {key: value for key, value in field.items() if key != "description"}
            for name, field in schemas[schema]["properties"].items()
        }

    assert (
        shape("CodeBatchCodesResponse")
        == shape("CodeBatchCreatedResponse")
        == {
            "batch": {"$ref": "#/components/schemas/CodeBatchItem"},
            "codes": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/CreatedCode"},
                "title": "Codes",
            },
        }
    )
    assert set(schemas["CreatedCode"]["properties"]) == {"code", "redeem_url"}
    # Creating a batch answers as it did, and the list of batches holds no code.
    create = document["paths"]["/api/admin/code-batches"]["post"]
    created = create["responses"]["201"]["content"]["application/json"]["schema"]
    assert created == {"$ref": "#/components/schemas/CodeBatchCreatedResponse"}
    assert "codes" not in schemas["CodeBatchListResponse"]["properties"]
    assert "code" not in schemas["CodeBatchItem"]["properties"]


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
        "EXPIRED",
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


def test_an_admin_reads_the_void_cost_on_the_market_itself_and_nobody_else_does(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """The figure of the risk page, on the page an admin voids a market from: the same
    signed amount, always sent, and null to whoever is no admin."""
    detail = schemas["MarketDetailResponse"]
    amount, null = detail["properties"]["void_cost_cr"]["anyOf"]
    assert null == {"type": "null"}
    assert amount["type"] == "string"
    assert amount["pattern"] == schemas["RiskMarketRow"]["properties"]["void_cost_cr"]["pattern"]
    assert "void_cost_cr" in detail["required"]


def test_the_risk_page_shows_the_months_adjustments_beside_the_months_codes(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """Signed: what admins took from balances can outweigh what they added."""
    risk = schemas["RiskResponse"]
    adjustments = risk["properties"]["adjustments_this_month_cr"]
    issued = risk["properties"]["code_issuance_this_month_cr"]
    assert "adjustments_this_month_cr" in risk["required"]
    assert adjustments["type"] == "string"
    assert adjustments["pattern"].startswith("^-?")
    assert not issued["pattern"].startswith("^-?")


def test_the_chart_says_where_its_right_edge_is(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    """`until` is an instant of the server, so a chart is drawn with no device clock."""
    history = document["paths"]["/api/markets/{market_id}/history"]["get"]
    answered = history["responses"]["200"]["content"]["application/json"]["schema"]
    assert answered == {"$ref": "#/components/schemas/HistoryResponse"}

    chart = schemas["HistoryResponse"]
    assert set(chart["required"]) == set(chart["properties"]) == {"points", "source", "until"}
    until = chart["properties"]["until"]
    assert (until["type"], until["format"]) == ("string", "date-time")
    assert set(schemas["HistoryPoint"]["properties"]) == {"t", "p_yes", "version"}


def test_a_proposal_in_the_list_says_how_its_market_ended(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """The same nullable outcome a market card carries."""
    proposal = schemas["ProposalItem"]
    assert "outcome" in proposal["required"]
    assert proposal["properties"]["outcome"]["anyOf"] == [
        {"$ref": "#/components/schemas/ResolutionOutcome"},
        {"type": "null"},
    ]
    assert (
        proposal["properties"]["outcome"]["anyOf"]
        == schemas["MarketSummary"]["properties"]["outcome"]["anyOf"]
        == schemas["MarketDetailResponse"]["properties"]["outcome"]["anyOf"]
    )
    assert schemas["ResolutionOutcome"]["enum"] == ["YES", "NO", "VOID"]


def test_a_batch_says_whether_a_revoke_would_revoke_anything(
    schemas: dict[str, dict[str, Any]],
) -> None:
    batch = schemas["CodeBatchItem"]
    assert "can_revoke" in batch["required"]
    assert batch["properties"]["can_revoke"]["type"] == "boolean"


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


LIMITS_NO_OPERATION_REFUSES_WITH: Final[frozenset[Limit]] = frozenset(
    {
        Limit.PROGRAMME_CHANGE_COOLDOWN_DAYS,
        Limit.DISCUSSION_MAX_WAITING_PER_USER,
        Limit.DISCUSSION_MAX_QUESTIONS_PER_MARKET,
    }
)
"""Members of `Limit` that no operation answers with, so `ErrorResponse` does not describe
them. The test below fails for a member the description names: whoever adds the refusal
describes it there and takes the name out of this set."""


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
        ErrorCode.CONFLICT,
        ErrorCode.INSUFFICIENT_BALANCE,
        ErrorCode.SERVICE_UNAVAILABLE,
    ):
        assert f"`{code.value}`" in description, code
    assert "`policy_accepted`" in description
    assert "`GET /markets/{id}/quote`" in description, "a quote is answered like its trade"
    as_one_line = " ".join(description.split())
    assert f"one per field, the first {MAX_ERRORS_LISTED} at most" in as_one_line
    assert "`json_invalid`" in description, "a body that is no JSON"
    # What a buy above the stake left would cost, on the trade and on its quote.
    assert as_one_line.count("`stake_left_cr` and `required_cr`") == 2
    assert "with the same message and details" in as_one_line
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
        "status",
        "end_reason",
        "media",
    ):
        assert f"`{key}`" in description, key
    # A limit is named exactly if an operation refuses with it.
    for limit in Limit:
        named = f"`{limit.value}`" in description
        assert named == (limit not in LIMITS_NO_OPERATION_REFUSES_WITH), limit
    # A live stream that runs into one of its two limits is told which, and no figure;
    # one that is in no state for the request says where it stands.
    assert "The two limits of a live stream carry `limit` and nothing beside it" in as_one_line
    assert "`status` and `end_reason` for one that has ended" in as_one_line
    assert "`postgres`, `redis`, `scylla` and `media`, each `up` or `down`" in as_one_line


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
        "repeat",
    }
    # Whether an expired voucher has its value back, on every shape that is a voucher.
    for voucher in ("VoucherItem", "VoucherIssuedResponse"):
        assert schemas[voucher]["properties"]["refunded"]["type"] == "boolean", voucher
        assert "refunded" in schemas[voucher]["required"], voucher
    # A second scan by the same barista is told apart from the first by this flag.
    assert schemas["VoucherRedeemResponse"]["properties"]["repeat"]["type"] == "boolean"
    assert "repeat" in schemas["VoucherRedeemResponse"]["required"]


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
        "product_name",
        "code",
        "expires_at",
    }
    assert (
        schemas["VoucherCodeResponse"]["properties"]["product_name"]["type"]
        == schemas["VoucherItem"]["properties"]["product_name"]["type"]
        == "string"
    )
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


def test_exactly_the_trade_route_and_the_adjustment_route_take_an_idempotency_key(
    document: dict[str, Any],
) -> None:
    """No other operation takes a header of its own."""
    keyed = {
        operation
        for operation, spec in each_operation(document)
        for parameter in spec.get("parameters", [])
        if parameter["in"] == "header"
    }
    assert keyed == {
        ("POST", "/api/markets/{market_id}/trades"),
        ("POST", "/api/admin/users/{user_id}/adjustments"),
    }


def test_lists_that_can_grow_take_a_limit_and_a_cursor(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    for path, response in (
        ("/api/me/trades", "TradeListResponse"),
        ("/api/me/ledger", "LedgerListResponse"),
        ("/api/admin/users", "AdminUserListResponse"),
    ):
        parameters = {p["name"]: p for p in document["paths"][path]["get"]["parameters"]}
        assert {"limit", "cursor"} <= set(parameters)
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


LONG_NUMBER_SECONDS: Final[float] = 2.0
"""How long rejecting a number of a million digits may take. It takes milliseconds;
converting such a number to an integer first takes half a minute."""


class Wallet(BaseModel):
    balance_cr: CreditAmount
    change_cr: SignedCreditAmount
    stake_cr: PositiveCreditAmount | None = None
    adjustment_cr: NonZeroCreditAmount | None = None


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


@pytest.mark.parametrize("sent", ["25", "-25", "0.000001", "-0.000001", "-999999999.999999"])
def test_an_adjustment_is_an_amount_above_or_below_zero(sent: str) -> None:
    wallet = Wallet.model_validate({"balance_cr": "1", "change_cr": "0", "adjustment_cr": sent})
    assert wallet.adjustment_cr == Decimal(sent)


@pytest.mark.parametrize(
    ("sent", "error"),
    [
        ("0", "decimal_zero"),
        ("-0", "decimal_zero"),
        ("0.000000", "decimal_zero"),
        ("1000000000", "decimal_too_large"),
        ("-1000000000", "decimal_too_large"),
        ("0.0000001", "decimal_places"),
        ("+5", "decimal_string"),
        ("5e3", "decimal_string"),
    ],
)
def test_an_adjustment_of_nothing_or_of_too_much_is_rejected(sent: str, error: str) -> None:
    with pytest.raises(ValidationError) as rejected:
        Wallet.model_validate({"balance_cr": "1", "change_cr": "0", "adjustment_cr": sent})
    assert [e["type"] for e in rejected.value.errors()] == [error]
    assert rejected.value.errors()[0]["loc"] == ("adjustment_cr",)


@pytest.mark.parametrize("field", ["balance_cr", "change_cr", "stake_cr", "adjustment_cr"])
@pytest.mark.parametrize(
    "digits", [13, 100, 5000, 999_999, 1_000_000, 1_500_000], ids=lambda digits: f"{digits}-digits"
)
def test_a_number_too_long_to_convert_is_too_large_like_any_other(field: str, digits: int) -> None:
    """Ordinary decimal arithmetic gives up on a number of about a million digits,
    and turning one into an integer takes seconds. The parser does neither: it is
    the same rejection as for thirteen digits, at once."""
    wallet = {"balance_cr": "1", "change_cr": "0"}
    started = time.monotonic()

    for sent in ("7" * digits, "7" * digits + ".5", "-" + "7" * digits):
        with pytest.raises(ValidationError) as rejected:
            Wallet.model_validate(wallet | {field: sent})
        assert [e["type"] for e in rejected.value.errors()] == ["decimal_too_large"]
        assert rejected.value.errors()[0]["loc"] == (field,)

    assert time.monotonic() - started < LONG_NUMBER_SECONDS


@pytest.mark.parametrize("field", ["balance_cr", "change_cr", "stake_cr", "adjustment_cr"])
@pytest.mark.parametrize(
    "sent",
    [
        "0." + "0" * 1_500_000 + "1",
        "1." + "0" * 1_500_000 + "1",
        "0." + "3" * 1_500_000,
        "1.0000000000000000000000000000001",
    ],
    ids=["next-to-nothing", "one-and-next-to-nothing", "a-million-and-a-half-places", "31-places"],
)
def test_a_fraction_too_long_to_convert_has_too_many_places_like_any_other(
    field: str, sent: str
) -> None:
    """To ordinary decimal arithmetic the first of these is zero and the last is one:
    each would pass for an amount of six places and be booked as something it is not.
    Each has a digit beyond the sixth place and is rejected for that."""
    started = time.monotonic()

    with pytest.raises(ValidationError) as rejected:
        Wallet.model_validate({"balance_cr": "1", "change_cr": "0"} | {field: sent})

    assert [e["type"] for e in rejected.value.errors()] == ["decimal_places"]
    assert rejected.value.errors()[0]["loc"] == (field,)
    assert time.monotonic() - started < LONG_NUMBER_SECONDS


def test_services_pass_decimals_and_json_gets_trimmed_strings() -> None:
    wallet = Wallet(balance_cr=to_cr(2_500_000), change_cr=to_cr(-1_000_000), stake_cr=None)
    assert wallet.balance_cr == Decimal("2.5")
    assert wallet.model_dump(mode="json") == {
        "balance_cr": "2.5",
        "change_cr": "-1",
        "stake_cr": None,
        "adjustment_cr": None,
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
        payout_cr=to_cr(quote.shares_micro),
        p_yes_before=0.0,
        p_no_before=1.0,
        p_yes_after=1.0,
        p_no_after=0.0,
        quote_version=0,
    )
    assert response.shares > query.amount_cr
    assert response.payout_cr == response.shares, "1 CR per share, and the answer holds it"


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
    ("body", "where", "sentence"),
    [
        ({"side": "BUY", "outcome": "YES"}, (), SIZED_ONE_WAY_MESSAGE),
        (
            {"side": "BUY", "outcome": "YES", "amount_cr": "1", "shares": "1"},
            (),
            SIZED_ONE_WAY_MESSAGE,
        ),
        (
            {"side": "SELL", "outcome": "YES", "amount_cr": "1"},
            ("amount_cr",),
            SELL_BY_SHARES_MESSAGE,
        ),
        (
            {"side": "BUY", "outcome": "YES", "amount_cr": "1", "max_cost_cr": "1"},
            ("max_cost_cr",),
            GUARD_DOES_NOT_APPLY_MESSAGE,
        ),
        (
            {"side": "BUY", "outcome": "YES", "shares": "1", "min_shares": "1"},
            ("min_shares",),
            GUARD_DOES_NOT_APPLY_MESSAGE,
        ),
        (
            {"side": "SELL", "outcome": "YES", "shares": "1", "max_cost_cr": "1"},
            ("max_cost_cr",),
            GUARD_DOES_NOT_APPLY_MESSAGE,
        ),
        (
            {"side": "BUY", "outcome": "YES", "shares": "1", "min_proceeds_cr": "1"},
            ("min_proceeds_cr",),
            GUARD_DOES_NOT_APPLY_MESSAGE,
        ),
    ],
    ids=[
        "no-size",
        "two-sizes",
        "a-sell-by-amount",
        "a-buy-by-amount-with-a-cost-guard",
        "a-buy-by-shares-with-a-shares-guard",
        "a-sell-with-a-cost-guard",
        "a-buy-with-a-proceeds-guard",
    ],
)
def test_every_other_combination_is_a_validation_error(
    body: dict[str, Any], where: tuple[str, ...], sentence: str
) -> None:
    """Each names the field that does not fit, where one field is it, and says so in
    a sentence: the guard that is not the one of the mode, the amount of a sell."""
    with pytest.raises(ValidationError) as rejected:
        TradeRequest.model_validate(body)

    assert [(error["loc"], error["type"], error["msg"]) for error in rejected.value.errors()] == [
        (where, "value_error", sentence)
    ]


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
    with pytest.raises(ValidationError, match=SELL_BY_SHARES_MESSAGE):
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
        "policy_accepted": True,
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
        {"requested_tier": "DEFAULT"},
        {"question": "Too short"},
        {"question": "x" * 201},
        {"yes_definition": "   "},
        {"close_at": "2026-10-16T12:00:00"},
        {"resolve_by": (TEST_NOW + timedelta(days=6)).isoformat()},
        {"policy_accepted": False},
        {"policy_accepted": 1},
        {"policy_accepted": "true"},
        {"policy_accepted": None},
    ],
    ids=[
        "names-a-tier",
        "short",
        "long",
        "blank",
        "naive-time",
        "resolves-before-close",
        "policy-declined",
        "policy-as-a-number",
        "policy-as-a-word",
        "policy-as-null",
    ],
)
def test_a_proposal_that_breaks_a_rule_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ProposeMarketRequest.model_validate(proposal(**overrides))


def test_a_proposal_without_the_policy_accepted_is_rejected_on_that_field() -> None:
    for declined in ({"policy_accepted": False}, {"policy_accepted": "true"}):
        with pytest.raises(ValidationError) as rejected:
            ProposeMarketRequest.model_validate(proposal(**declined))
        assert [error["loc"] for error in rejected.value.errors()] == [("policy_accepted",)]
    missing = proposal()
    del missing["policy_accepted"]
    with pytest.raises(ValidationError) as rejected:
        ProposeMarketRequest.model_validate(missing)
    assert [(error["loc"], error["type"]) for error in rejected.value.errors()] == [
        (("policy_accepted",), "missing")
    ]


def test_a_profile_takes_the_policy_as_a_strict_boolean_and_may_leave_it_out() -> None:
    profile = {"username": "neo", "degree_level": "OTHER", "degree_programme": "ETH staff"}

    assert SaveProfileRequest.model_validate(profile).policy_accepted is False
    for sent in (True, False):
        request = SaveProfileRequest.model_validate(profile | {"policy_accepted": sent})
        assert request.policy_accepted is sent
    for not_a_boolean in (1, 0, "true", "false", None):
        with pytest.raises(ValidationError) as rejected:
            SaveProfileRequest.model_validate(profile | {"policy_accepted": not_a_boolean})
        assert [(error["loc"], error["type"]) for error in rejected.value.errors()] == [
            (("policy_accepted",), "bool_type")
        ]


def test_an_adjustment_states_a_signed_amount_and_a_reason() -> None:
    credit = AdjustBalanceRequest.model_validate({"amount_cr": "25", "reason": "  Prize.  "})
    debit = AdjustBalanceRequest.model_validate({"amount_cr": "-0.5", "reason": "Typo."})
    assert (credit.amount_cr, credit.reason) == (Decimal(25), "Prize.")
    assert debit.amount_cr == Decimal("-0.5")
    for broken in (
        {"amount_cr": "0"},
        {"amount_cr": 25.0},
        {"amount_cr": "1000000000"},
        {"reason": "   "},
        {"reason": "x" * 501},
        {"note": "extra"},
    ):
        with pytest.raises(ValidationError):
            AdjustBalanceRequest.model_validate({"amount_cr": "25", "reason": "Prize."} | broken)
    for missing in ("amount_cr", "reason"):
        body = {"amount_cr": "25", "reason": "Prize."}
        del body[missing]
        with pytest.raises(ValidationError, match=missing):
            AdjustBalanceRequest.model_validate(body)


def test_the_user_list_takes_a_text_of_one_to_a_hundred_characters() -> None:
    assert AdminUserListQuery().q is None
    assert AdminUserListQuery.model_validate({"q": "  neo  "}).q == "neo"
    assert AdminUserListQuery.model_validate({"role": "BARISTA", "status": "SUSPENDED"})
    for broken in (
        {"q": ""},
        {"q": "   "},
        {"q": "x" * 101},
        {"role": "OWNER"},
        {"status": "GONE"},
    ):
        with pytest.raises(ValidationError):
            AdminUserListQuery.model_validate(broken)


def test_approval_takes_a_budget_for_custom_and_only_for_custom() -> None:
    assert ApproveMarketRequest.model_validate({"tier": "DEFAULT"}).tier is MarketTier.DEFAULT
    custom = ApproveMarketRequest.model_validate({"tier": "CUSTOM", "budget_cr": "50"})
    assert custom.budget_cr == 50
    for refused, sentence in (
        ({"tier": "CUSTOM"}, BUDGET_NEEDED_MESSAGE),
        ({"tier": "TIGHT", "budget_cr": "50"}, BUDGET_NOT_TAKEN_MESSAGE),
    ):
        with pytest.raises(ValidationError) as rejected:
            ApproveMarketRequest.model_validate(refused)
        # Both are about the budget, so both are reported on its field.
        assert [(error["loc"], error["msg"]) for error in rejected.value.errors()] == [
            (("budget_cr",), sentence)
        ]


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

LIVE_EVENT_NAMES: Final[tuple[str, ...]] = (
    "ready",
    "beat",
    "market.quote",
    "market.status",
    "leaderboard.changed",
    "me.changed",
    "chat.message",
    "chat.removed",
    "broadcast.changed",
)
"""The events the API pushes, in the order `LIVE_EVENTS` lists their models."""

BARE_EVENTS: Final[tuple[type[BaseModel], ...]] = (
    ReadyEvent,
    BeatEvent,
    LeaderboardChangedEvent,
    MeChangedEvent,
)
"""Events that say what happened by their name alone."""

TAB: Final[str] = "tab-0123"


def test_the_event_models_are_these_nine_and_each_is_named_by_its_type() -> None:
    assert list(LIVE_EVENTS) == [
        ReadyEvent,
        BeatEvent,
        MarketQuoteEvent,
        MarketStatusEvent,
        LeaderboardChangedEvent,
        MeChangedEvent,
        ChatMessageEvent,
        ChatRemovedEvent,
        BroadcastChangedEvent,
    ]
    named = [model.model_json_schema()["properties"]["type"]["const"] for model in LIVE_EVENTS]
    assert named == list(LIVE_EVENT_NAMES)
    for model in LIVE_EVENTS:
        schema = model.model_json_schema()
        assert set(schema["required"]) == set(schema["properties"]), model.__name__
        assert schema["properties"]["ts"]["format"] == "date-time", model.__name__
        assert model.model_fields["type"].is_required(), "a body always says its name"


def test_an_event_carries_what_its_name_promises_and_nothing_about_who_it_is_for() -> None:
    """`ready` is `{type, ts}`: a stream carries no cursor, so nothing says whether it
    resumed. No body names an audience."""
    for bare in BARE_EVENTS:
        assert set(bare.model_fields) == {"type", "ts"}, bare.__name__
    assert set(MarketQuoteEvent.model_fields) == {
        "type",
        "market_id",
        "version",
        "p_yes",
        "p_no",
        "volume_cr",
        "ts",
    }
    assert set(MarketStatusEvent.model_fields) == {
        "type",
        "market_id",
        "status",
        "trading_open",
        "ts",
    }
    # A chat message is for everybody inside its market, and its event carries it.
    assert set(ChatMessageEvent.model_fields) == {
        "type",
        "market_id",
        "broadcast_id",
        "message",
        "ts",
    }
    assert ChatMessageEvent.model_fields["message"].annotation is ChatMessageItem
    assert set(ChatRemovedEvent.model_fields) == {
        "type",
        "market_id",
        "broadcast_id",
        "message_id",
        "ts",
    }
    # What a live stream is at that moment depends on who asks: its event names the
    # market and nothing else.
    assert set(BroadcastChangedEvent.model_fields) == {"type", "market_id", "ts"}
    for model in LIVE_EVENTS:
        assert {"audience", "audience_programme"}.isdisjoint(model.model_fields)


def test_a_price_event_travels_with_its_amount_as_a_string_and_fits_an_announcement() -> None:
    market_id = uuid7()
    quote = MarketQuoteEvent(
        type="market.quote",
        market_id=market_id,
        version=8,
        p_yes=0.548,
        p_no=0.452,
        volume_cr=to_cr(1_250_500_000),
        ts=TEST_NOW,
    )

    assert json.loads(quote.model_dump_json()) == {
        "type": "market.quote",
        "market_id": str(market_id),
        "version": 8,
        "p_yes": 0.548,
        "p_no": 0.452,
        "volume_cr": "1250.5",
        "ts": json.loads(ReadyEvent(type="ready", ts=TEST_NOW).model_dump_json())["ts"],
    }
    # Every event model is a payload of `uow.announce`: its `type` is the event's name.
    announced = AnnouncedEvent(
        topics=(Topic(TopicKind.MARKETS), Topic(TopicKind.MARKET, market_id)),
        audience=Audience.everyone(),
        payload=quote,
    )
    assert announced.payload.type == "market.quote"
    with pytest.raises(ValidationError):
        ReadyEvent.model_validate({"type": "beat", "ts": TEST_NOW})


def test_a_stream_names_its_tab_and_any_number_of_topics_each_counted_once() -> None:
    market = uuid7()
    query = LiveStreamQuery.model_validate(
        {
            "tab": TAB,
            "topic": [
                "markets",
                f"market:{market}",
                "markets",
                f"market:{str(market).upper()}",
                "leaderboard",
                f"broadcast:{market}",
            ],
        }
    )

    assert query.topics == (
        Topic(TopicKind.MARKETS),
        Topic(TopicKind.MARKET, market),
        Topic(TopicKind.LEADERBOARD),
        Topic(TopicKind.BROADCAST, market),
    )
    # A tab's id may be a UUID, and a stream may follow nothing but its viewer's own.
    assert LiveStreamQuery.model_validate({"tab": str(uuid7())}).topics == ()
    most = [f"market:{uuid7()}" for _ in range(TOPICS_HARD_MAX)]
    assert len(LiveStreamQuery.model_validate({"tab": TAB, "topic": most}).topics) == len(most)
    assert set(LiveStreamQuery.model_fields) == {"tab", "topic"}, "no cursor"


@pytest.mark.parametrize(
    ("query", "loc", "kind"),
    [
        ({}, ("tab",), "missing"),
        ({"tab": "short"}, ("tab",), "string_pattern_mismatch"),
        ({"tab": "x" * 37}, ("tab",), "string_pattern_mismatch"),
        ({"tab": "tab 0123"}, ("tab",), "string_pattern_mismatch"),
        ({"tab": TAB, "topic": ["me"]}, ("topic", 0), "string_pattern_mismatch"),
        ({"tab": TAB, "topic": [f"me:{uuid7()}"]}, ("topic", 0), "string_pattern_mismatch"),
        ({"tab": TAB, "topic": ["market"]}, ("topic", 0), "string_pattern_mismatch"),
        ({"tab": TAB, "topic": [f"markets:{uuid7()}"]}, ("topic", 0), "string_pattern_mismatch"),
        ({"tab": TAB, "topic": ["markets", "market:1"]}, ("topic", 1), "string_pattern_mismatch"),
        (
            {"tab": TAB, "topic": ["x" * (TOPIC_MAX_LENGTH + 1)]},
            ("topic", 0),
            "string_too_long",
        ),
        (
            {"tab": TAB, "topic": ["markets"] * (TOPICS_HARD_MAX + 1)},
            ("topic",),
            "too_long",
        ),
        ({"tab": TAB, "after": "0a0a0a0a-12"}, ("after",), "extra_forbidden"),
    ],
    ids=[
        "no tab",
        "a short tab",
        "a long tab",
        "a tab with a blank",
        "me",
        "me with a key",
        "a market without its id",
        "markets with an id",
        "an id that is none",
        "a long topic",
        "more topics than a request takes",
        "a cursor",
    ],
)
def test_a_stream_query_without_a_tab_or_with_what_is_no_topic_is_refused(
    query: dict[str, Any], loc: tuple[str | int, ...], kind: str
) -> None:
    with pytest.raises(ValidationError) as refused:
        LiveStreamQuery.model_validate(query)

    (error,) = refused.value.errors()
    assert (error["loc"], error["type"]) == (loc, kind)


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
        degree_programme="BSc Computer Science",
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


def test_the_event_stream_is_documented_as_the_union_of_its_events_and_nothing_beside_it(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    """What the frontend types its events from: every event model, under the one media
    type of the answer, without the `type: string` of an answer that is no JSON."""
    stream = document["paths"]["/api/stream"]["get"]
    assert (stream["operationId"], stream["tags"]) == ("stream_live", ["live"])
    assert stream["summary"] == "Everything the app pushes (server-sent events)"

    answer = stream["responses"]["200"]
    assert list(answer["content"]) == ["text/event-stream"]
    events = answer["content"]["text/event-stream"]
    assert list(events) == ["schema"]
    assert set(events["schema"]) == {"anyOf", "title"}
    assert events["schema"]["anyOf"] == [
        {"$ref": f"#/components/schemas/{model.__name__}"} for model in LIVE_EVENTS
    ]
    named = [schemas[model.__name__]["properties"]["type"]["const"] for model in LIVE_EVENTS]
    assert named == list(LIVE_EVENT_NAMES)

    # A refusal is answered before the stream starts, as JSON in the one error shape.
    assert stream["responses"]["default"]["content"] == {
        "application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}
    }
    assert set(stream["responses"]) == {"200", "default"}


def test_a_stream_is_asked_for_with_its_tab_and_its_topics_in_the_query(
    document: dict[str, Any],
) -> None:
    stream = document["paths"]["/api/stream"]["get"]
    parameters = {parameter["name"]: parameter for parameter in stream["parameters"]}
    assert set(parameters) == {"tab", "topic"}
    assert all(parameter["in"] == "query" for parameter in parameters.values())
    assert (parameters["tab"]["required"], parameters["topic"]["required"]) == (True, False)
    assert parameters["tab"]["schema"]["pattern"] == TAB_PATTERN
    assert parameters["topic"]["schema"]["type"] == "array"
    assert "requestBody" not in stream


def test_the_tag_live_holds_the_event_stream_the_chart_and_the_stream_of_one_market(
    document: dict[str, Any],
) -> None:
    live = {spec["operationId"] for _, spec in each_operation(document) if spec["tags"] == ["live"]}
    assert live == {"stream_live", "get_market_history", "stream_market"}


# --- Live chat and live stream ---------------------------------------------------------

REF: Final[str] = "#/components/schemas/"

CHAT: Final[str] = "/api/markets/{market_id}/chat"
REMOVE_MESSAGE: Final[str] = "/api/admin/markets/{market_id}/chat/{message_id}/remove"
BROADCAST: Final[str] = "/api/markets/{market_id}/broadcast"
REQUEST_BROADCAST: Final[str] = "/api/markets/{market_id}/broadcast/request"
START: Final[str] = "/api/broadcasts/{broadcast_id}/start"
CHUNKS: Final[str] = "/api/broadcasts/{broadcast_id}/chunks"
END: Final[str] = "/api/broadcasts/{broadcast_id}/end"
LIVE_NOW: Final[str] = "/api/broadcasts/live"
ACCESS: Final[str] = "/api/broadcasts/{broadcast_id}/access"
ADMIN_BROADCASTS: Final[str] = "/api/admin/broadcasts"
APPROVE_BROADCAST: Final[str] = "/api/admin/broadcasts/{broadcast_id}/approve"
REFUSE_BROADCAST: Final[str] = "/api/admin/broadcasts/{broadcast_id}/refuse"
START_AS_ADMIN: Final[str] = "/api/admin/markets/{market_id}/broadcast"

BROADCAST_ITEM_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "market_id",
        "status",
        "on_air",
        "playlist_url",
        "requested_at",
        "started_at",
        "ended_at",
        "end_reason",
        "refusal_reason",
        "requested_by",
    }
)

BROADCAST_FLAGS: Final[frozenset[str]] = frozenset(
    {
        "is_broadcaster",
        "can_request",
        "can_start",
        "can_start_as_admin",
        "can_approve",
        "can_refuse",
        "can_end",
        "can_watch",
        "full",
    }
)
"""What a caller may do about the live stream of a market: nine answers of the server."""

BROADCAST_CONFIG_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "chunk_ms",
        "chunk_max_bytes",
        "upload_backlog_bytes",
        "capture_height",
        "capture_fps",
        "capture_max_width",
        "capture_max_height",
        "capture_video_bits_per_second",
        "capture_audio_bits_per_second",
        "capture_mime_types",
        "stall_seconds",
        "max_minutes",
        "upload_timeout_ms",
        "upload_retry_ms",
        "restart_window_ms",
        "wait_notice_ms",
        "player_retry_ms",
    }
)


def nullable(name: str) -> list[dict[str, str]]:
    """How the document writes a field that holds the schema `name` or null."""
    return [{"$ref": f"{REF}{name}"}, {"type": "null"}]


def answered_with(operation: dict[str, Any], status: str = "200") -> dict[str, Any]:
    schema: dict[str, Any] = operation["responses"][status]["content"]["application/json"]["schema"]
    return schema


def in_the_path(operation: dict[str, Any]) -> list[str]:
    return [parameter["name"] for parameter in operation["parameters"] if parameter["in"] == "path"]


def test_chat_and_live_stream_are_fourteen_operations_under_four_tags(
    document: dict[str, Any],
) -> None:
    by_tag: dict[str, set[str]] = {
        "chat": set(),
        "admin-chat": set(),
        "broadcasts": set(),
        "admin-broadcasts": set(),
    }
    for _, spec in each_operation(document):
        (tag,) = spec["tags"]
        if tag in by_tag:
            by_tag[tag].add(spec["operationId"])

    assert by_tag == {
        "chat": {"get_chat", "post_chat_message"},
        "admin-chat": {"remove_chat_message"},
        "broadcasts": {
            "get_market_broadcast",
            "request_broadcast",
            "start_broadcast",
            "upload_broadcast_chunk",
            "end_broadcast",
            "list_live_broadcasts",
            "admit_broadcast_viewer",
        },
        "admin-broadcasts": {
            "list_admin_broadcasts",
            "approve_broadcast",
            "refuse_broadcast",
            "start_broadcast_as_admin",
        },
    }
    paths = document["paths"]
    assert set(paths[CHAT]) == {"get", "post"}
    for path, method in (
        (BROADCAST, "get"),
        (REQUEST_BROADCAST, "post"),
        (START, "post"),
        (CHUNKS, "post"),
        (END, "post"),
        (LIVE_NOW, "get"),
        (ACCESS, "get"),
    ):
        assert set(paths[path]) == {method}, path
        assert paths[path][method]["security"] == MEMBER, path
    for path, method in (
        (REMOVE_MESSAGE, "post"),
        (ADMIN_BROADCASTS, "get"),
        (APPROVE_BROADCAST, "post"),
        (REFUSE_BROADCAST, "post"),
        (START_AS_ADMIN, "post"),
    ):
        assert set(paths[path]) == {method}, path
        assert paths[path][method]["security"] == ADMIN, path


def test_a_chat_is_read_in_one_answer_and_a_message_carries_the_authors_role(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    reading = document["paths"][CHAT]["get"]
    assert in_the_path(reading) == ["market_id"]
    assert [p for p in reading["parameters"] if p["in"] != "path"] == [], "no pages"
    assert answered_with(reading) == {"$ref": f"{REF}ChatResponse"}

    chat = schemas["ChatResponse"]
    assert set(chat["required"]) == set(chat["properties"])
    assert set(chat["properties"]) == {"open", "broadcast_id", "can_remove", "items"}
    assert (chat["properties"]["open"]["type"], chat["properties"]["can_remove"]["type"]) == (
        "boolean",
        "boolean",
    )
    assert chat["properties"]["broadcast_id"]["anyOf"] == [
        {"type": "string", "format": "uuid"},
        {"type": "null"},
    ]
    assert chat["properties"]["items"]["items"] == {"$ref": f"{REF}ChatMessageItem"}

    message = schemas["ChatMessageItem"]
    assert set(message["required"]) == set(message["properties"])
    assert set(message["properties"]) == {"id", "username", "role", "text", "from_vis"}
    assert message["properties"]["from_vis"]["type"] == "boolean"
    assert message["properties"]["role"]["$ref"] == f"{REF}UserRole"
    assert "format" not in message["properties"]["username"], "a username, not an id"
    # A message names its author by username and nothing else: no id, no address.
    assert not {"user_id", "email", "created_at"} & set(message["properties"])


def test_a_chat_message_is_one_text_as_long_as_the_database_takes(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    writing = document["paths"][CHAT]["post"]
    assert in_the_path(writing) == ["market_id"]
    assert writing["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": f"{REF}PostChatMessageRequest"
    }
    assert answered_with(writing, "201") == {"$ref": f"{REF}ChatMessageItem"}

    body = schemas["PostChatMessageRequest"]
    assert set(body["required"]) == set(body["properties"]) == {"text"}
    text = body["properties"]["text"]
    assert (text["type"], text["minLength"], text["maxLength"]) == (
        "string",
        1,
        CHAT_MESSAGE_STORED_MAX_LENGTH,
    )
    stored = check_constraint("chat_messages", "ck_chat_messages_text_length")
    assert f"BETWEEN 1 AND {CHAT_MESSAGE_STORED_MAX_LENGTH}" in str(stored.sqltext)
    assert CHAT_MESSAGE_STORED_MAX_LENGTH == 500
    assert PostChatMessageRequest.model_validate({"text": "  Goal!  "}).text == "Goal!"
    for refused in (
        {"text": "   "},
        {"text": "Goal!", "from_vis": True},
        {"text": "Goal!", "role": "ADMIN"},
        {},
    ):
        with pytest.raises(ValidationError):
            PostChatMessageRequest.model_validate(refused)

    config = schemas["PublicConfigResponse"]
    for told in ("chat_message_max_length", "chat_backlog_size"):
        assert config["properties"][told]["type"] == "integer", told
        assert told in config["required"], told


def test_an_admin_removes_a_chat_message_without_a_body_and_learns_when(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    removing = document["paths"][REMOVE_MESSAGE]["post"]
    assert in_the_path(removing) == ["market_id", "message_id"]
    assert "requestBody" not in removing
    assert answered_with(removing) == {"$ref": f"{REF}RemovedResponse"}

    removed = schemas["RemovedResponse"]
    assert set(removed["required"]) == set(removed["properties"]) == {"id", "removed_at"}
    assert removed["properties"]["id"]["format"] == "uuid"
    assert removed["properties"]["removed_at"]["format"] == "date-time"


def test_every_action_on_a_live_stream_answers_with_the_broadcast_and_the_callers_side(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    paths = document["paths"]
    for path, method, status, named in (
        (BROADCAST, "get", "200", ["market_id"]),
        (REQUEST_BROADCAST, "post", "201", ["market_id"]),
        (START, "post", "200", ["broadcast_id"]),
        (END, "post", "200", ["broadcast_id"]),
        (APPROVE_BROADCAST, "post", "200", ["broadcast_id"]),
        (REFUSE_BROADCAST, "post", "200", ["broadcast_id"]),
        (START_AS_ADMIN, "post", "201", ["market_id"]),
    ):
        operation = paths[path][method]
        assert in_the_path(operation) == named, path
        assert answered_with(operation, status) == {"$ref": f"{REF}MarketBroadcastResponse"}, path
    # Two of the seven take a body: the proposer's acceptance of the policy and an
    # admin's reason.
    with_a_body = {
        path
        for path in (
            BROADCAST,
            REQUEST_BROADCAST,
            START,
            END,
            APPROVE_BROADCAST,
            REFUSE_BROADCAST,
            START_AS_ADMIN,
        )
        for spec in paths[path].values()
        if "requestBody" in spec
    }
    assert with_a_body == {REQUEST_BROADCAST, REFUSE_BROADCAST}
    assert paths[REQUEST_BROADCAST]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ] == {"$ref": f"{REF}RequestBroadcastRequest"}
    assert paths[REFUSE_BROADCAST]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ] == {"$ref": f"{REF}RefuseBroadcastRequest"}

    answer = schemas["MarketBroadcastResponse"]
    assert set(answer["required"]) == set(answer["properties"]) == {"broadcast", "viewer"}
    assert answer["properties"]["broadcast"]["anyOf"] == nullable("BroadcastItem")
    assert answer["properties"]["viewer"]["$ref"] == f"{REF}BroadcastViewer"

    flags = schemas["BroadcastViewer"]
    assert set(flags["required"]) == set(flags["properties"]) == BROADCAST_FLAGS
    for flag in sorted(BROADCAST_FLAGS):
        assert flags["properties"][flag]["type"] == "boolean", flag


def test_a_broadcast_says_where_it_stands_and_nobody_is_told_how_many_watch(
    schemas: dict[str, dict[str, Any]],
) -> None:
    item = schemas["BroadcastItem"]
    assert set(item["required"]) == set(item["properties"]) == BROADCAST_ITEM_FIELDS
    fields = item["properties"]
    assert fields["status"]["$ref"] == f"{REF}BroadcastStatus"
    assert fields["on_air"]["type"] == "boolean"
    assert fields["playlist_url"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert "/hls/index.m3u8" in fields["playlist_url"]["description"]
    assert (fields["requested_at"]["type"], fields["requested_at"]["format"]) == (
        "string",
        "date-time",
    )
    for instant in ("started_at", "ended_at"):
        assert fields[instant]["anyOf"] == [
            {"type": "string", "format": "date-time"},
            {"type": "null"},
        ], instant
    assert fields["end_reason"]["anyOf"] == nullable("BroadcastEndReason")
    assert fields["refusal_reason"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert fields["requested_by"]["anyOf"] == nullable("BroadcastPerson")
    assert "admins only" in fields["requested_by"]["description"]
    assert set(schemas["BroadcastPerson"]["properties"]) == {"username", "email"}

    assert schemas["BroadcastStatus"]["enum"] == [
        "REQUESTED",
        "APPROVED",
        "REFUSED",
        "LIVE",
        "ENDED",
    ]
    assert [status.value for status in BroadcastStatus] == schemas["BroadcastStatus"]["enum"]
    assert schemas["BroadcastEndReason"]["enum"] == [
        "BROADCASTER",
        "ADMIN",
        "STALLED",
        "TIME_LIMIT",
    ]
    assert [why.value for why in BroadcastEndReason] == schemas["BroadcastEndReason"]["enum"]

    # No count of viewers, in no shape: a place is free or it is not.
    counting = sorted(
        f"{name}.{field}"
        for name, schema in schemas.items()
        for field in schema.get("properties", {})
        if "viewers" in field or field in ("watching", "seats", "places")
    )
    assert counting == []


def test_a_piece_of_a_recording_is_sent_as_bytes_with_its_part_and_its_place(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    """The one operation whose body is no JSON: the document says binary, and what is
    read of it is the route's to bound."""
    upload = document["paths"][CHUNKS]["post"]
    assert upload["operationId"] == "upload_broadcast_chunk"
    body = upload["requestBody"]
    assert body["required"] is True
    assert body["content"] == {
        "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
    }
    parameters = {parameter["name"]: parameter for parameter in upload["parameters"]}
    assert set(parameters) == {"broadcast_id", "part", "seq"}
    assert (parameters["part"]["in"], parameters["part"]["required"]) == ("query", True)
    assert (parameters["seq"]["in"], parameters["seq"]["required"]) == ("query", True)
    assert parameters["part"]["schema"]["pattern"] == PART_PATTERN == "^[A-Za-z0-9_-]{8,36}$"
    seq = parameters["seq"]["schema"]
    assert (seq["type"], seq["minimum"], seq["exclusiveMaximum"]) == ("integer", 0, SEQ_LIMIT)
    assert SEQ_LIMIT == 10**9
    assert answered_with(upload) == {"$ref": f"{REF}BroadcastChunkResponse"}

    taken = schemas["BroadcastChunkResponse"]
    assert set(taken["required"]) == set(taken["properties"]) == {"restart", "next_seq"}
    assert (taken["properties"]["restart"]["type"], taken["properties"]["next_seq"]["type"]) == (
        "boolean",
        "integer",
    )
    # It is the only operation with a body that is no JSON.
    binary = {
        operation
        for operation, spec in each_operation(document)
        if "requestBody" in spec and "application/json" not in spec["requestBody"]["content"]
    }
    assert binary == {("POST", CHUNKS)}

    assert BroadcastChunkQuery.model_validate({"part": str(uuid7()), "seq": "0"}).seq == 0
    for refused in (
        {"part": "short", "seq": 0},
        {"part": "x" * 37, "seq": 0},
        {"part": "part 0001", "seq": 0},
        {"part": "part-0001", "seq": -1},
        {"part": "part-0001", "seq": SEQ_LIMIT},
        {"part": "part-0001"},
        {"seq": 0},
        {"part": "part-0001", "seq": 0, "size": 1},
    ):
        with pytest.raises(ValidationError):
            BroadcastChunkQuery.model_validate(refused)


def test_the_two_bodies_of_a_live_stream_are_an_acceptance_and_a_reason(
    schemas: dict[str, dict[str, Any]],
) -> None:
    asking = schemas["RequestBroadcastRequest"]
    assert set(asking["required"]) == set(asking["properties"]) == {"policy_accepted"}
    policy = asking["properties"]["policy_accepted"]
    assert (policy["type"], policy["const"]) == ("boolean", True)
    # The acceptance a proposal states, held to the same rule.
    of_a_proposal = schemas["ProposeMarketRequest"]["properties"]["policy_accepted"]
    assert {key: value for key, value in policy.items() if key != "description"} == {
        key: value for key, value in of_a_proposal.items() if key != "description"
    }
    assert RequestBroadcastRequest.model_validate({"policy_accepted": True})
    for declined in (False, "true", 1, None):
        with pytest.raises(ValidationError):
            RequestBroadcastRequest.model_validate({"policy_accepted": declined})

    refusing = schemas["RefuseBroadcastRequest"]
    assert set(refusing["required"]) == set(refusing["properties"]) == {"reason"}
    reason = refusing["properties"]["reason"]
    assert (reason["type"], reason["minLength"], reason["maxLength"]) == ("string", 1, 500)
    assert (
        reason["maxLength"] == schemas["RejectMarketRequest"]["properties"]["reason"]["maxLength"]
    )
    assert RefuseBroadcastRequest.model_validate({"reason": "  Not now.  "}).reason == "Not now."


def test_the_lists_of_live_streams_show_each_with_the_question_of_its_market(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    live = document["paths"][LIVE_NOW]["get"]
    assert "parameters" not in live
    assert answered_with(live) == {"$ref": f"{REF}LiveBroadcastListResponse"}
    assert schemas["LiveBroadcastListResponse"]["properties"]["items"]["items"] == {
        "$ref": f"{REF}LiveBroadcastItem"
    }
    row = schemas["LiveBroadcastItem"]
    assert set(row["required"]) == set(row["properties"])
    assert set(row["properties"]) == {"broadcast_id", "market_id", "question", "started_at"}
    assert (row["properties"]["started_at"]["type"], row["properties"]["started_at"]["format"]) == (
        "string",
        "date-time",
    )

    queue = document["paths"][ADMIN_BROADCASTS]["get"]
    assert "parameters" not in queue
    assert answered_with(queue) == {"$ref": f"{REF}AdminBroadcastListResponse"}
    assert schemas["AdminBroadcastListResponse"]["properties"]["items"]["items"] == {
        "$ref": f"{REF}AdminBroadcastItem"
    }
    waiting = schemas["AdminBroadcastItem"]
    assert set(waiting["required"]) == set(waiting["properties"])
    assert set(waiting["properties"]) == BROADCAST_ITEM_FIELDS | {"question"}


def test_the_proxy_asks_for_each_viewer_whether_they_may_play(
    document: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> None:
    """One operation no screen calls. A playlist and a segment are served by the proxy
    and are no operations of this document."""
    asking = document["paths"][ACCESS]["get"]
    assert asking["operationId"] == "admit_broadcast_viewer"
    assert in_the_path(asking) == ["broadcast_id"]
    assert [p for p in asking["parameters"] if p["in"] != "path"] == []
    assert "requestBody" not in asking
    assert "proxy" in asking["summary"]
    assert answered_with(asking) == {"$ref": f"{REF}BroadcastAccessResponse"}
    admitted = schemas["BroadcastAccessResponse"]
    assert set(admitted["required"]) == set(admitted["properties"]) == {"broadcast_id"}
    assert not [path for path in document["paths"] if "/hls" in path or path.endswith(".m3u8")]


def test_config_tells_the_studio_and_the_player_what_they_go_by(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """Sizes, rates and paces come from the server: the studio and the player hold no
    figure of their own."""
    config = schemas["PublicConfigResponse"]
    assert "broadcast" in config["required"]
    assert config["properties"]["broadcast"]["$ref"] == f"{REF}BroadcastConfig"

    told = schemas["BroadcastConfig"]
    assert set(told["required"]) == set(told["properties"]) == BROADCAST_CONFIG_FIELDS
    for field in sorted(BROADCAST_CONFIG_FIELDS - {"capture_mime_types"}):
        assert told["properties"][field]["type"] == "integer", field
    kinds = told["properties"]["capture_mime_types"]
    assert (kinds["type"], kinds["items"]) == ("array", {"type": "string"})
    assert CAPTURE_MIME_TYPES == (
        "video/webm;codecs=vp8,opus",
        "video/webm",
        "video/mp4;codecs=avc1.42E01F,mp4a.40.2",
        "video/mp4",
    )
    # A studio records its camera: nothing here is about a screen.
    assert not [field for field in told["properties"] if "screen" in field or "display" in field]


def test_health_reports_the_media_box_beside_the_three_stores(
    schemas: dict[str, dict[str, Any]],
) -> None:
    health = schemas["HealthResponse"]
    assert set(health["required"]) == set(health["properties"])
    assert set(health["properties"]) == {"status", "postgres", "redis", "scylla", "media"}
    for store in ("postgres", "redis", "scylla", "media"):
        assert health["properties"][store]["$ref"] == f"{REF}DependencyStatus", store
    assert schemas["DependencyStatus"]["enum"] == ["up", "down"]
    assert schemas["HealthStatus"]["enum"] == ["ok", "degraded"]


def test_a_chat_message_and_its_removal_and_a_change_of_a_live_stream_are_pushed() -> None:
    market_id, broadcast_id, message_id = uuid7(), uuid7(), uuid7()
    now = json.loads(ReadyEvent(type="ready", ts=TEST_NOW).model_dump_json())["ts"]
    message = ChatMessageItem(
        id=message_id, username="ada", role=UserRole.ADMIN, text="Goal!", from_vis=True
    )
    written = ChatMessageEvent(
        type="chat.message",
        market_id=market_id,
        broadcast_id=broadcast_id,
        message=message,
        ts=TEST_NOW,
    )
    removed = ChatRemovedEvent(
        type="chat.removed",
        market_id=market_id,
        broadcast_id=broadcast_id,
        message_id=message_id,
        ts=TEST_NOW,
    )
    changed = BroadcastChangedEvent(type="broadcast.changed", market_id=market_id, ts=TEST_NOW)

    assert json.loads(written.model_dump_json()) == {
        "type": "chat.message",
        "market_id": str(market_id),
        "broadcast_id": str(broadcast_id),
        "message": {
            "id": str(message_id),
            "username": "ada",
            "role": "ADMIN",
            "text": "Goal!",
            "from_vis": True,
        },
        "ts": now,
    }
    assert json.loads(removed.model_dump_json()) == {
        "type": "chat.removed",
        "market_id": str(market_id),
        "broadcast_id": str(broadcast_id),
        "message_id": str(message_id),
        "ts": now,
    }
    assert json.loads(changed.model_dump_json()) == {
        "type": "broadcast.changed",
        "market_id": str(market_id),
        "ts": now,
    }
    # Each is a payload of `uow.announce`: the two of a chat on the topic of their
    # market, the one of a live stream on the topic of its broadcast.
    for payload, topic in (
        (written, Topic(TopicKind.MARKET, market_id)),
        (removed, Topic(TopicKind.MARKET, market_id)),
        (changed, Topic(TopicKind.BROADCAST, market_id)),
    ):
        announced = AnnouncedEvent(topics=(topic,), audience=Audience.everyone(), payload=payload)
        assert announced.payload.type == payload.type
    with pytest.raises(ValidationError):
        BroadcastChangedEvent.model_validate(
            {"type": "stream.changed", "market_id": market_id, "ts": TEST_NOW}
        )
