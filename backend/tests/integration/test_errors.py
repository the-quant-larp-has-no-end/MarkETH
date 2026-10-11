"""Every non-2xx response has one shape, and every guard holds (SPEC §10, §12, A.2).

`{code, message, details}` for whatever goes wrong: an unknown route, a body that does
not validate, a missing header, a foreign Origin, a request the portal names nobody
for, a caller without a profile, a role that is not enough, a service error, a bug, a
database that is down, a lock that is not granted in time, a pool with no connection
free. The guard tests walk the OpenAPI document, so a route added later is covered
without anyone remembering to add it here.
"""

import logging
import time
from collections.abc import AsyncIterator
from typing import Any, Final
from uuid import uuid7

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.container import Container
from app.core.config import Settings
from app.core.enums import AccountKind, UserRole, UserStatus
from app.core.errors import (
    ForbiddenError,
    QuoteStaleError,
    RateLimitedError,
    UsernameTakenError,
    ValidationFailedError,
)
from app.core.identity import DISPLAY_NAME_MAX_LENGTH, AuthenticatedUser, Caller, PortalIdentity
from app.core.units import to_cr
from app.db import database as database_errors
from app.db.database import Database
from app.main import create_app
from app.schemas.common import MAX_ERRORS_LISTED
from app.schemas.markets import Holding
from app.services.broadcasts import NOT_AVAILABLE_MESSAGE
from app.services.codes import ADMIN_REDEEM_MESSAGE, OWN_BATCH_MESSAGE
from app.services.market_lifecycle import SELF_DEALING_MESSAGE
from app.services.profile import RESERVED_USERNAME_MESSAGE
from tests.support.clock import FrozenClock
from tests.support.factories import ActAs, Factory

pytestmark = pytest.mark.integration

type Operation = tuple[str, str]

ERROR_KEYS: Final[set[str]] = {"code", "message", "details"}

PUBLIC_OPERATIONS: Final[frozenset[Operation]] = frozenset(
    {
        ("GET", "/api/health"),
        ("GET", "/api/config"),
    }
)
"""What Appendix A.2 opens to everybody. Everything else needs the portal to name the caller."""

PROFILE_OPERATION: Final[Operation] = ("PUT", "/api/me/profile")
"""The one operation a named caller may use without a profile: it creates the profile."""

PATH_VALUES: Final[dict[str, str]] = {
    "{market_id}": str(uuid7()),
    "{batch_id}": str(uuid7()),
    "{voucher_id}": str(uuid7()),
    "{user_id}": str(uuid7()),
    "{alert_id}": "1",
    "{notice_id}": str(uuid7()),
    "{message_id}": str(uuid7()),
    "{broadcast_id}": str(uuid7()),
}
"""A value for every path parameter the API has. None of them is a code: a code travels
in a body."""

BROADCAST_ACCESS: Final[Operation] = ("GET", "/api/broadcasts/{broadcast_id}/access")
"""What the proxy asks for every viewer of a live stream. Its service answers 403 where
any other would answer 404: the proxy reads nothing else as a no."""

BARISTA_ROUTE: Final[str] = "/api/barista/vouchers/redeem"
SCANNED: Final[dict[str, str]] = {"code": "ABCD-EFGH"}

TRADE_PATH: Final[str] = f"/api/markets/{uuid7()}/trades"
TRADE_BODY: Final[dict[str, str]] = {"side": "BUY", "outcome": "YES", "amount_cr": "25"}

PROFILE_BODY: Final[dict[str, object]] = {
    "username": "newcomer",
    "degree_level": "BACHELOR",
    "degree_programme": "BSc Computer Science",
    "policy_accepted": True,
}

NEWCOMER: Final[PortalIdentity] = PortalIdentity(
    email="newcomer@ethz.ch", name="Nora Newcomer", role=UserRole.USER
)
"""Somebody the portal signed in who has not saved a profile yet."""


def someone(role: UserRole, status: UserStatus = UserStatus.ACTIVE) -> AuthenticatedUser:
    name = role.value.lower()
    return AuthenticatedUser(
        id=uuid7(),
        email=f"{name}@ethz.ch",
        name=name.capitalize(),
        username=name,
        degree_programme="BSc Computer Science",
        role=role,
        status=status,
    )


def operations(app: FastAPI) -> list[Operation]:
    """Every (METHOD, path template) the application serves under `/api`."""
    return sorted(
        (method.upper(), path) for path, item in app.openapi()["paths"].items() for method in item
    )


def protected(app: FastAPI, prefix: str = "/api/") -> list[Operation]:
    """Every operation that is not public."""
    return [
        (method, path)
        for method, path in operations(app)
        if (method, path) not in PUBLIC_OPERATIONS and path.startswith(prefix)
    ]


def member_operations(app: FastAPI) -> list[Operation]:
    """Every operation that needs a profile: all that are protected, except the one
    that creates it."""
    return [operation for operation in protected(app) if operation != PROFILE_OPERATION]


def concrete(path: str) -> str:
    for placeholder, value in PATH_VALUES.items():
        path = path.replace(placeholder, value)
    assert "{" not in path, f"no test value for a parameter of {path}"
    return path


def assert_error(response: httpx.Response, status_code: int, code: str) -> dict[str, Any]:
    """The response is the documented error; returns its `details`."""
    assert response.status_code == status_code, response.text
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["code"] == code
    assert isinstance(body["message"], str)
    assert body["message"]
    assert isinstance(body["details"], dict)
    details: dict[str, Any] = body["details"]
    return details


@pytest.fixture
async def unhandled_client(
    app: FastAPI, client: httpx.AsyncClient
) -> AsyncIterator[httpx.AsyncClient]:
    """Like `client`, but an exception the application does not handle becomes the
    response a real server would send instead of being raised into the test."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url=client.base_url, headers=client.headers
    ) as unhandled:
        yield unhandled


class RecordedIdentity:
    """Stands in for `IdentityService`: remembers what it was asked and answers with a
    caller who has no profile, under the id and the name it was given."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    async def authenticate(self, user_id: str, user_name: str) -> Caller:
        self.asked.append((user_id, user_name))
        identity = PortalIdentity(email=user_id, name=user_name, role=UserRole.USER)
        return Caller(identity=identity, user=None)


@pytest.fixture
def identity_service(container: Container, monkeypatch: pytest.MonkeyPatch) -> RecordedIdentity:
    """Puts a `RecordedIdentity` where the application looks for its identity service."""
    recorded = RecordedIdentity()
    monkeypatch.setattr(container, "identity", recorded)
    return recorded


# --- Requests that never reach a route -------------------------------------------


async def test_an_unknown_route_is_404_not_found(client: httpx.AsyncClient) -> None:
    assert_error(await client.get("/api/no-such-thing"), 404, "NOT_FOUND")
    assert_error(await client.get("/somewhere-else"), 404, "NOT_FOUND")
    assert_error(await client.post("/api/no-such-thing", json={}), 404, "NOT_FOUND")


async def test_a_wrong_method_is_405_with_the_same_shape(client: httpx.AsyncClient) -> None:
    response = await client.delete("/api/health")
    assert_error(response, 405, "NOT_FOUND")
    assert "GET" in response.headers["allow"]


@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost:8081", "null", ""])
async def test_a_state_changing_request_from_a_foreign_origin_is_403(
    client: httpx.AsyncClient, as_user: ActAs, origin: str
) -> None:
    """The portal names the visitor whichever page made the browser ask. Acting as a
    user here shows what refuses such a page: the Origin check, not the missing identity."""
    as_user(someone(UserRole.USER))
    response = await client.post(
        "/api/codes/redeem", json={"code": "ABCD-EFGH-JKMN"}, headers={"Origin": origin}
    )
    assert_error(response, 403, "FORBIDDEN")
    assert "did not come from" in response.json()["message"]


async def test_a_state_changing_request_without_an_origin_is_403(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    as_user(someone(UserRole.ADMIN))
    del client.headers["Origin"]
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = await client.request(method, "/api/me/profile")
        assert_error(response, 403, "FORBIDDEN")
        assert "did not come from" in response.json()["message"]


async def test_the_origin_check_comes_before_everything_else(client: httpx.AsyncClient) -> None:
    """Also before routing: a foreign page learns nothing about which routes exist."""
    response = await client.post("/api/no-such-thing", headers={"Origin": "https://evil.example"})
    assert_error(response, 403, "FORBIDDEN")


async def test_reading_needs_no_origin(client: httpx.AsyncClient) -> None:
    del client.headers["Origin"]
    assert (await client.get("/api/config")).status_code == 200
    assert (await client.head("/api/config")).status_code != 403
    assert_error(await client.get("/api/me"), 401, "UNAUTHENTICATED")


async def test_every_allowed_origin_passes_the_check(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    allowed = app.state.container.settings.app.allowed_origins
    assert len(allowed) >= 2
    for origin in (*allowed, allowed[0] + "/"):
        response = await client.post("/api/vouchers", headers={"Origin": origin})
        assert_error(response, 401, "UNAUTHENTICATED")


# --- Validation ----------------------------------------------------------------


async def test_a_body_that_does_not_validate_is_422_with_the_field_errors(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    as_user(someone(UserRole.USER))
    response = await client.post("/api/codes/redeem", json={"kode": "ABCD-EFGH-JKMN"})
    details = assert_error(response, 422, "VALIDATION_ERROR")
    errors = {tuple(error["loc"]): error for error in details["errors"]}
    assert errors[("body", "code")]["type"] == "missing"
    assert errors[("body", "kode")]["type"] == "extra_forbidden"
    for error in errors.values():
        assert set(error) == {"loc", "msg", "type"}, "the rejected input is not echoed back"
    # Each `msg` is a sentence for whoever filled the field in, not the framework's.
    assert errors[("body", "code")]["msg"] == "This is required."
    assert errors[("body", "kode")]["msg"] == "This field is not part of the request."


async def test_a_body_that_is_not_json_is_422_whoever_sent_it(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    """The framework parses the body before it asks any guard, so this one answer does
    not depend on who is calling. It says nothing about anybody and writes nothing."""
    for who in (None, NEWCOMER, someone(UserRole.USER)):
        as_user(who)
        response = await client.post(
            "/api/codes/redeem",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        details = assert_error(response, 422, "VALIDATION_ERROR")
        assert details["errors"][0]["loc"][0] == "body"
        assert details["errors"][0]["msg"] == "The request is not valid JSON."


UNDECODABLE_BODIES: Final[dict[str, bytes]] = {
    "a byte that is no UTF-8": b'{"code": "\xff"}',
    "a number too long to read": b"9" * 5000,
}
"""Bodies the framework cannot decode at all, so it never gets as far as calling one
of them invalid JSON."""


@pytest.mark.parametrize("body", UNDECODABLE_BODIES.values(), ids=UNDECODABLE_BODIES.keys())
async def test_a_body_that_cannot_be_decoded_is_422_like_one_that_is_no_json(
    client: httpx.AsyncClient, as_user: ActAs, body: bytes
) -> None:
    """The framework gives up on such a body with a status and a sentence of its own.
    The client is answered as for any body that is no JSON, whoever is calling."""
    for who in (None, NEWCOMER, someone(UserRole.USER)):
        as_user(who)
        response = await client.post(
            "/api/codes/redeem", content=body, headers={"Content-Type": "application/json"}
        )

        details = assert_error(response, 422, "VALIDATION_ERROR")
        assert response.json()["message"] == "The request is not valid."
        assert details == {
            "errors": [
                {"loc": ["body"], "msg": "The request is not valid JSON.", "type": "json_invalid"}
            ]
        }
        assert "parsing the body" not in response.text


async def test_a_request_refused_on_thousands_of_fields_is_told_the_first_fifty(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    """A body made of fields nobody asked for is not answered with a line for each:
    the answer lists the first `MAX_ERRORS_LISTED` and stays a few kilobytes."""
    as_user(someone(UserRole.USER))
    unknown = {f"field_{number}": number for number in range(5000)}

    response = await client.post("/api/codes/redeem", json=unknown)

    errors = assert_error(response, 422, "VALIDATION_ERROR")["errors"]
    assert len(errors) == MAX_ERRORS_LISTED == 50
    # What the form has to hear comes first: the field it left out.
    assert errors[0] == {"loc": ["body", "code"], "msg": "This is required.", "type": "missing"}
    assert [error["loc"] for error in errors[1:]] == [
        ["body", f"field_{number}"] for number in range(MAX_ERRORS_LISTED - 1)
    ]
    assert len(response.content) < 8_000

    # A request with fewer mistakes than that is told every one of them.
    fewer = {f"field_{number}": number for number in range(MAX_ERRORS_LISTED - 2)}
    told = assert_error(await client.post("/api/codes/redeem", json=fewer), 422, "VALIDATION_ERROR")
    assert len(told["errors"]) == MAX_ERRORS_LISTED - 1


async def test_a_json_float_is_not_an_amount(client: httpx.AsyncClient, as_user: ActAs) -> None:
    """A.2: credits cross the API as decimal strings; a float cannot hold 0.1."""
    as_user(someone(UserRole.USER))
    response = await client.post(
        TRADE_PATH,
        json={"side": "BUY", "outcome": "YES", "amount_cr": 0.25},
        headers={"Idempotency-Key": str(uuid7())},
    )
    details = assert_error(response, 422, "VALIDATION_ERROR")
    assert [error["loc"] for error in details["errors"]] == [["body", "amount_cr"]]
    assert details["errors"][0]["type"] == "decimal_string"


async def test_a_number_of_a_million_digits_is_422_like_any_amount_that_is_too_large(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    """Ordinary decimal arithmetic gives up on a number that long. It is the client's
    mistake, answered on the field like any amount with too many digits, not a
    failure of the server."""
    as_user(someone(UserRole.USER))
    a_million_digits = "9" * 1_000_000

    for sent in (a_million_digits, f"-{a_million_digits}", f"{a_million_digits}.5"):
        response = await client.post(
            TRADE_PATH,
            json={"side": "BUY", "outcome": "YES", "amount_cr": sent},
            headers={"Idempotency-Key": str(uuid7())},
        )

        details = assert_error(response, 422, "VALIDATION_ERROR")
        assert [error["loc"] for error in details["errors"]] == [["body", "amount_cr"]]
        assert details["errors"][0]["type"] == "decimal_too_large"
        assert a_million_digits not in response.text


async def test_a_quote_query_is_validated_like_a_body(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    as_user(someone(UserRole.USER))
    path = f"/api/markets/{uuid7()}/quote"
    seven_places = await client.get(
        path, params={"side": "BUY", "outcome": "YES", "amount_cr": "0.1234567"}
    )
    details = assert_error(seven_places, 422, "VALIDATION_ERROR")
    assert details["errors"][0]["loc"] == ["query", "amount_cr"]
    assert details["errors"][0]["type"] == "decimal_places"

    sell_by_amount = await client.get(
        path, params={"side": "SELL", "outcome": "YES", "amount_cr": "0.25"}
    )
    details = assert_error(sell_by_amount, 422, "VALIDATION_ERROR")
    assert details["errors"] == [
        {
            "loc": ["query", "amount_cr"],
            "msg": "Enter the number of shares to sell.",
            "type": "value_error",
        }
    ]

    unknown = await client.get(
        path, params={"side": "BUY", "outcome": "YES", "shares": "1", "leverage": "10"}
    )
    details = assert_error(unknown, 422, "VALIDATION_ERROR")
    assert details["errors"][0]["loc"] == ["query", "leverage"]


async def test_a_path_parameter_that_is_not_a_uuid_is_422(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    as_user(someone(UserRole.USER))
    details = assert_error(await client.get("/api/markets/not-a-uuid"), 422, "VALIDATION_ERROR")
    assert details["errors"][0]["loc"] == ["path", "market_id"]


async def test_a_trade_without_an_idempotency_key_is_422(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    as_user(someone(UserRole.USER))
    response = await client.post(TRADE_PATH, json=TRADE_BODY)
    details = assert_error(response, 422, "VALIDATION_ERROR")
    assert details["errors"] == [
        {"loc": ["header", "Idempotency-Key"], "msg": "This is required.", "type": "missing"}
    ]


async def test_an_idempotency_key_must_be_a_uuid(client: httpx.AsyncClient, as_user: ActAs) -> None:
    as_user(someone(UserRole.USER))
    response = await client.post(TRADE_PATH, json=TRADE_BODY, headers={"Idempotency-Key": "tap-1"})
    details = assert_error(response, 422, "VALIDATION_ERROR")
    assert details["errors"][0]["loc"] == ["header", "Idempotency-Key"]
    assert details["errors"][0]["type"] == "uuid_parsing"


@pytest.mark.parametrize(
    ("body", "field", "rule"),
    [
        (PROFILE_BODY | {"username": "two words"}, "username", "string_pattern_mismatch"),
        (PROFILE_BODY | {"username": "bindestrich-name"}, "username", "string_pattern_mismatch"),
        (PROFILE_BODY | {"username": "Newcomer"}, "username", "string_pattern_mismatch"),
        (PROFILE_BODY | {"username": "NEWCOMER"}, "username", "string_pattern_mismatch"),
        (PROFILE_BODY | {"username": "x" * 33}, "username", "string_too_long"),
        (PROFILE_BODY | {"username": "   "}, "username", "string_too_short"),
        (PROFILE_BODY | {"degree_level": "KINDERGARTEN"}, "degree_level", "enum"),
        (PROFILE_BODY | {"degree_programme": "x" * 101}, "degree_programme", "string_too_long"),
        (PROFILE_BODY | {"degree_programme": ""}, "degree_programme", "string_too_short"),
    ],
    ids=[
        "username-with-a-space",
        "username-with-a-dash",
        "username-with-a-capital-letter",
        "username-in-upper-case",
        "username-of-33-characters",
        "username-of-blanks",
        "level-that-does-not-exist",
        "programme-of-101-characters",
        "programme-that-is-empty",
    ],
)
async def test_a_profile_that_has_the_wrong_shape_is_422(
    client: httpx.AsyncClient, as_user: ActAs, body: dict[str, str], field: str, rule: str
) -> None:
    as_user(NEWCOMER)
    details = assert_error(await client.put("/api/me/profile", json=body), 422, "VALIDATION_ERROR")
    assert [(error["loc"], error["type"]) for error in details["errors"]] == [
        (["body", field], rule)
    ]


async def test_a_profile_takes_no_field_it_does_not_know(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    """Least of all one that says who the caller is, or what they may do."""
    as_user(NEWCOMER)
    for extra in ("role", "email", "name", "status"):
        response = await client.put("/api/me/profile", json=PROFILE_BODY | {extra: "ADMIN"})
        details = assert_error(response, 422, "VALIDATION_ERROR")
        assert [(error["loc"], error["type"]) for error in details["errors"]] == [
            (["body", extra], "extra_forbidden")
        ]


# --- Who is calling: the portal's headers -------------------------------------------


async def test_every_route_that_is_not_public_answers_401_when_the_portal_names_nobody(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    routes = protected(app)
    assert len(routes) == 55, "every operation but /health and /config"
    assert PROFILE_OPERATION in routes
    for method, path in routes:
        response = await client.request(method, concrete(path))
        assert_error(response, 401, "UNAUTHENTICATED")
        assert "portal" in response.json()["message"]


async def test_the_document_marks_exactly_the_routes_that_answer_401(app: FastAPI) -> None:
    """`security` in openapi.json is read off the guards, so it matches what they do:
    an identity is required exactly where a request that names nobody gets 401."""
    paths = app.openapi()["paths"]
    needs_identity = sorted(
        (method.upper(), path)
        for path, item in paths.items()
        for method, operation in item.items()
        if operation.get("security")
    )
    assert needs_identity == protected(app)


async def test_a_request_without_the_id_header_never_reaches_the_identity_service(
    client: httpx.AsyncClient, app: FastAPI, identity_service: RecordedIdentity
) -> None:
    for method, path in protected(app):
        assert_error(await client.request(method, concrete(path)), 401, "UNAUTHENTICATED")

    assert identity_service.asked == []


@pytest.mark.parametrize("blank", ["", " ", "\t", "   \t "], ids=["empty", "space", "tab", "mixed"])
async def test_a_blank_id_header_names_nobody(
    client: httpx.AsyncClient, settings: Settings, identity_service: RecordedIdentity, blank: str
) -> None:
    headers = {settings.identity.header_user_id: blank, settings.identity.header_user_name: "Eve"}

    assert_error(await client.get("/api/me", headers=headers), 401, "UNAUTHENTICATED")
    assert_error(
        await client.put("/api/me/profile", json=PROFILE_BODY, headers=headers),
        401,
        "UNAUTHENTICATED",
    )
    assert identity_service.asked == []


async def test_a_name_without_an_id_names_nobody(
    client: httpx.AsyncClient, settings: Settings, identity_service: RecordedIdentity
) -> None:
    headers = {settings.identity.header_user_name: "admin@ethz.ch"}

    assert_error(await client.get("/api/me", headers=headers), 401, "UNAUTHENTICATED")
    assert identity_service.asked == []


async def test_an_id_header_sent_twice_names_nobody(
    client: httpx.AsyncClient, settings: Settings, identity_service: RecordedIdentity
) -> None:
    """Whatever a client put in front of the portal's own line must not be the one read."""
    header = settings.identity.header_user_id
    for first, second in (
        ("admin@ethz.ch", "student@ethz.ch"),
        ("student@ethz.ch", "admin@ethz.ch"),
        ("student@ethz.ch", "student@ethz.ch"),
        ("", "admin@ethz.ch"),
    ):
        response = await client.get("/api/me", headers=[(header, first), (header, second)])
        assert_error(response, 401, "UNAUTHENTICATED")

    assert identity_service.asked == []


async def test_the_portal_headers_reach_the_identity_service_with_the_name_percent_decoded(
    client: httpx.AsyncClient, settings: Settings, identity_service: RecordedIdentity
) -> None:
    """The portal percent-encodes what is outside ASCII in the name: Zo%C3%AB is Zoë."""
    names = settings.identity
    headers = {
        names.header_user_id: "zoe@ethz.ch",
        names.header_user_name: "Zo%C3%AB%20M%C3%BCller",
    }

    response = await client.get("/api/me", headers=headers)

    zoe = "Zo" + chr(0xEB) + " M" + chr(0xFC) + "ller"
    assert identity_service.asked == [("zoe@ethz.ch", zoe)]
    details = assert_error(response, 403, "PROFILE_REQUIRED")
    assert details == {"name": zoe, "email": "zoe@ethz.ch"}


@pytest.mark.parametrize(
    ("sent", "name"),
    [
        ("Ada%00Admin", "AdaAdmin"),
        ("%20%20Ada%09%0A%20Admin%0D%0A", "Ada Admin"),
        ("Ada%E2%80%AE%20Admin%E2%80%8B", "Ada Admin"),
        ("a" * 300, "a" * DISPLAY_NAME_MAX_LENGTH),
        ("%00%20%0A", ""),
    ],
    ids=["nul", "mixed-whitespace", "format-characters", "300-characters", "nothing-to-keep"],
)
async def test_the_name_reaches_the_identity_service_as_the_display_name_a_profile_stores(
    client: httpx.AsyncClient,
    settings: Settings,
    identity_service: RecordedIdentity,
    sent: str,
    name: str,
) -> None:
    """`authenticate_request` reads the name header with `display_name`: no control or
    format character, whitespace collapsed, at most 200 characters."""
    names = settings.identity
    headers = {names.header_user_id: "x@ethz.ch", names.header_user_name: sent}

    response = await client.get("/api/me", headers=headers)

    assert identity_service.asked == [("x@ethz.ch", name)]
    assert assert_error(response, 403, "PROFILE_REQUIRED") == {"name": name, "email": "x@ethz.ch"}


async def test_the_id_header_reaches_the_identity_service_exactly_as_it_arrived(
    client: httpx.AsyncClient, settings: Settings, identity_service: RecordedIdentity
) -> None:
    """Nothing is decoded, trimmed, folded or picked apart on the way: what the id means
    is `EmailPolicy`'s to say, and it can only refuse what it gets to see."""
    header = settings.identity.header_user_id.encode()
    as_sent = [
        b"Zoe%40ETHZ.ch",
        b"a@ethz.ch, admin@ethz.ch",
        b"d\xbakw\xaak@ethz.ch",
        b"\xa0admin@ethz.ch",
    ]

    for value in as_sent:
        await client.get("/api/me", headers={header: value})

    assert [user_id for user_id, _ in identity_service.asked] == [
        value.decode("latin-1") for value in as_sent
    ]


async def test_a_request_without_a_name_header_has_an_empty_name(
    client: httpx.AsyncClient, settings: Settings, identity_service: RecordedIdentity
) -> None:
    response = await client.get("/api/me", headers={settings.identity.header_user_id: "x@ethz.ch"})

    assert identity_service.asked == [("x@ethz.ch", "")]
    assert assert_error(response, 403, "PROFILE_REQUIRED") == {"name": "", "email": "x@ethz.ch"}


async def test_a_name_header_sent_twice_is_no_name(
    client: httpx.AsyncClient, settings: Settings, identity_service: RecordedIdentity
) -> None:
    names = settings.identity
    headers = [
        (names.header_user_id, "x@ethz.ch"),
        (names.header_user_name, "Mallory"),
        (names.header_user_name, "Xavier"),
    ]

    await client.get("/api/me", headers=headers)

    assert identity_service.asked == [("x@ethz.ch", "")]


async def test_the_header_names_come_from_the_settings(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IDENTITY_HEADER_USER_ID and IDENTITY_HEADER_USER_NAME decide what is read."""
    renamed = container.settings.identity.model_copy(
        update={"header_user_id": "X-Portal-Subject", "header_user_name": "X-Portal-Display"}
    )
    monkeypatch.setattr(
        container, "settings", container.settings.model_copy(update={"identity": renamed})
    )
    recorded = RecordedIdentity()
    monkeypatch.setattr(container, "identity", recorded)
    origin = container.settings.app.public_base_url
    transport = httpx.ASGITransport(app=create_app(container))

    async with httpx.AsyncClient(transport=transport, base_url=origin) as renamed_client:
        under_the_example_names = await renamed_client.get(
            "/api/me", headers={"X-User-Id": "x@ethz.ch", "X-User-Name": "Xavier"}
        )
        await renamed_client.get(
            "/api/me", headers={"x-portal-subject": "x@ethz.ch", "X-PORTAL-DISPLAY": "Xavier"}
        )

    assert_error(under_the_example_names, 401, "UNAUTHENTICATED")
    assert recorded.asked == [("x@ethz.ch", "Xavier")]


async def test_the_identity_service_is_asked_once_per_request_however_many_guards_there_are(
    client: httpx.AsyncClient,
    container: Container,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An admin route sits behind three guards that all rest on `authenticate_request`.
    Who is calling costs one query, and upkeep hangs on it, so it must happen once."""
    admin = someone(UserRole.ADMIN)
    asked: list[str] = []

    async def authenticate(user_id: str, user_name: str) -> Caller:
        asked.append(user_id)
        identity = PortalIdentity(email=admin.email, name=admin.name, role=admin.role)
        return Caller(identity=identity, user=admin)

    monkeypatch.setattr(container.identity, "authenticate", authenticate)
    headers = {settings.identity.header_user_id: admin.email}

    revoke = f"/api/admin/code-batches/{uuid7()}/revoke"
    assert (await client.post(revoke, json={}, headers=headers)).status_code not in (401, 403)
    assert asked == [admin.email]

    scanned = await client.post(BARISTA_ROUTE, json=SCANNED, headers=headers)
    assert scanned.status_code not in (401, 403)
    assert asked == [admin.email, admin.email]


async def test_an_id_the_policy_refuses_is_403_with_the_allowed_domains(
    client: httpx.AsyncClient,
    container: Container,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the application's own `EmailPolicy`, as `IdentityService` asks it first."""

    async def authenticate(user_id: str, user_name: str) -> Caller:
        email = container.email_policy.identify(user_id)
        role = container.email_policy.effective_role(email, UserRole.USER)
        return Caller(identity=PortalIdentity(email=email, name=user_name, role=role), user=None)

    monkeypatch.setattr(container.identity, "authenticate", authenticate)
    header = settings.identity.header_user_id.encode()
    refused = [
        b"someone@uzh.ch",
        b"someone@student.ethz.ch",
        b"a@ethz.ch, admin@ethz.ch",
        b"not an address",
        b"admin@ethz.ch@ethz.ch",
        b"\xa0admin@ethz.ch",
        b"admin@ethz.ch\xa0",
        b"d\xbakw\xaak@ethz.ch",
        b"admin%40ethz.ch",
    ]

    for foreign in refused:
        for method, path in (("GET", "/api/me"), ("PUT", "/api/me/profile")):
            response = await client.request(method, path, headers={header: foreign})
            details = assert_error(response, 403, "FORBIDDEN")
            assert details == {"allowed_domains": ["ethz.ch"]}
            assert foreign.decode("latin-1") not in response.text

    # An id is read in lower case: capitals name the same account.
    allowed = await client.get("/api/me", headers={header: b"Someone@ETHZ.ch"})
    assert assert_error(allowed, 403, "PROFILE_REQUIRED")["email"] == "someone@ethz.ch"


# --- Profiles and roles --------------------------------------------------------------


async def test_every_member_route_answers_403_profile_required_to_an_identity_without_a_profile(
    client: httpx.AsyncClient, app: FastAPI, as_user: ActAs
) -> None:
    as_user(NEWCOMER)
    routes = member_operations(app)
    assert len(routes) == 54, "every protected operation but the one that saves a profile"
    for method, path in routes:
        response = await client.request(method, concrete(path))
        details = assert_error(response, 403, "PROFILE_REQUIRED")
        assert details == {"name": "Nora Newcomer", "email": "newcomer@ethz.ch"}


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.BARISTA])
async def test_a_role_opens_nothing_before_the_profile_exists(
    client: httpx.AsyncClient, app: FastAPI, as_user: ActAs, role: UserRole
) -> None:
    """A bootstrap admin sets up a profile like everybody else: the guards ask for
    the profile first, whatever role the identity carries."""
    as_user(PortalIdentity(email="chief@ethz.ch", name="", role=role))
    for method, path in member_operations(app):
        response = await client.request(method, concrete(path))
        assert assert_error(response, 403, "PROFILE_REQUIRED") == {
            "name": "",
            "email": "chief@ethz.ch",
        }


async def test_the_profile_route_lets_an_identity_without_a_profile_through(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    as_user(NEWCOMER)

    response = await client.put("/api/me/profile", json=PROFILE_BODY)

    assert response.status_code not in (401, 403), response.text


async def test_the_profile_route_lets_a_registered_user_through(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    """A profile can be changed by the route that created it."""
    as_user(someone(UserRole.USER))

    response = await client.put("/api/me/profile", json=PROFILE_BODY)

    assert response.status_code not in (401, 403), response.text


async def test_who_is_calling_is_settled_before_a_body_is_validated(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    bad_body = {"question": 1}

    assert_error(await client.post("/api/markets", json=bad_body), 401, "UNAUTHENTICATED")
    assert_error(await client.put("/api/me/profile", json=bad_body), 401, "UNAUTHENTICATED")

    as_user(NEWCOMER)
    assert_error(await client.post("/api/markets", json=bad_body), 403, "PROFILE_REQUIRED")
    assert_error(await client.put("/api/me/profile", json=bad_body), 422, "VALIDATION_ERROR")


async def test_the_guard_of_the_stream_runs_before_the_stream_subscribes(
    client: httpx.AsyncClient,
    container: Container,
    as_user: ActAs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The subscription is opened by a dependency of the route. A caller the guard turns
    away must not get as far as Redis."""

    class NoSubscriptions:
        def subscribe(self, market_id: object) -> object:
            raise AssertionError("a refused caller reached the subscription")

    monkeypatch.setattr(container, "live_quotes", NoSubscriptions())
    stream = f"/api/markets/{uuid7()}/stream"

    assert_error(await client.get(stream), 401, "UNAUTHENTICATED")
    as_user(NEWCOMER)
    assert_error(await client.get(stream), 403, "PROFILE_REQUIRED")
    as_user(someone(UserRole.USER, UserStatus.SUSPENDED))
    assert_error(await client.get(stream), 403, "FORBIDDEN")


async def test_the_guard_of_the_event_stream_runs_before_the_hub_opens_a_stream(
    client: httpx.AsyncClient,
    container: Container,
    as_user: ActAs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stream is opened by a dependency of the route. A caller the guard turns away
    must not get as far as the hub: such a caller ends nobody's stream and takes no place
    of `LIVE_MAX_STREAMS`."""

    class NoStreams:
        def open(self, *asked: object) -> object:
            raise AssertionError("a refused caller reached the hub")

    monkeypatch.setattr(container, "live", NoStreams())
    stream = "/api/stream?tab=tab-0123&topic=markets"

    assert_error(await client.get(stream), 401, "UNAUTHENTICATED")
    as_user(NEWCOMER)
    assert_error(await client.get(stream), 403, "PROFILE_REQUIRED")
    as_user(someone(UserRole.USER, UserStatus.SUSPENDED))
    assert_error(await client.get(stream), 403, "FORBIDDEN")


async def test_every_route_that_asks_for_no_role_lets_a_registered_user_through(
    client: httpx.AsyncClient, app: FastAPI, factory: Factory, as_user: ActAs
) -> None:
    """Twenty-nine operations are open to every registered user. No guard may turn a
    plain user away from one of them; what the service then answers is not the point
    here. One service answers 403 itself: a live stream nobody may play is refused in
    its own words, which no guard says."""
    as_user(await factory.user())
    routes = [
        (method, path)
        for method, path in member_operations(app)
        if not path.startswith(("/api/admin/", "/api/barista/"))
    ]
    assert len(routes) == 29
    assert ("GET", "/api/leaderboard") in routes
    assert ("GET", "/api/stream") in routes
    assert ("POST", "/api/me/notices/{notice_id}/seen") in routes
    assert ("POST", "/api/broadcasts/{broadcast_id}/chunks") in routes
    assert BROADCAST_ACCESS in routes
    for method, path in routes:
        response = await client.request(method, concrete(path))
        if (method, path) == BROADCAST_ACCESS:
            assert assert_error(response, 403, "FORBIDDEN") == {}
            assert response.json()["message"] == NOT_AVAILABLE_MESSAGE
            continue
        assert response.status_code not in (401, 403), (method, path, response.text)


@pytest.mark.parametrize("role", [UserRole.USER, UserRole.BARISTA])
async def test_admin_routes_answer_403_to_anyone_but_an_admin(
    client: httpx.AsyncClient, app: FastAPI, as_user: ActAs, role: UserRole
) -> None:
    as_user(someone(role))
    routes = protected(app, "/api/admin/")
    assert len(routes) == 24
    for method, path in routes:
        response = await client.request(method, concrete(path))
        assert_error(response, 403, "FORBIDDEN")


async def test_barista_routes_answer_403_to_a_normal_user(
    client: httpx.AsyncClient, app: FastAPI, as_user: ActAs
) -> None:
    as_user(someone(UserRole.USER))
    routes = protected(app, "/api/barista/")
    assert routes == [("POST", BARISTA_ROUTE)]
    for method, path in routes:
        assert_error(await client.request(method, concrete(path)), 403, "FORBIDDEN")
        assert_error(await client.request(method, path, json=SCANNED), 403, "FORBIDDEN")


@pytest.mark.parametrize("role", [UserRole.BARISTA, UserRole.ADMIN])
async def test_barista_routes_let_baristas_and_admins_through(
    client: httpx.AsyncClient, as_user: ActAs, role: UserRole
) -> None:
    as_user(someone(role))
    response = await client.post(BARISTA_ROUTE, json=SCANNED)
    assert response.status_code not in (401, 403)


async def test_admin_routes_let_an_admin_through(client: httpx.AsyncClient, as_user: ActAs) -> None:
    as_user(someone(UserRole.ADMIN))
    response = await client.post(f"/api/admin/markets/{uuid7()}/approve", json={})
    details = assert_error(response, 422, "VALIDATION_ERROR")
    assert details["errors"][0]["loc"] == ["body", "tier"]


@pytest.mark.parametrize("role", list(UserRole))
async def test_a_suspended_user_is_refused_everywhere_an_identity_is_needed(
    client: httpx.AsyncClient, app: FastAPI, as_user: ActAs, role: UserRole
) -> None:
    """The profile route included: a suspended account cannot rename itself either."""
    as_user(someone(role, UserStatus.SUSPENDED))
    routes = protected(app)
    assert PROFILE_OPERATION in routes
    for method, path in routes:
        response = await client.request(method, concrete(path))
        assert_error(response, 403, "FORBIDDEN")
        assert response.json()["message"] == "This account is suspended."


async def test_as_user_none_makes_the_request_come_from_nobody_again(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    as_user(someone(UserRole.ADMIN))
    assert (await client.get("/api/admin/code-batches")).status_code not in (401, 403)
    as_user(None)
    assert_error(await client.get("/api/admin/code-batches"), 401, "UNAUTHENTICATED")


# --- Errors raised behind a route ------------------------------------------------


async def test_an_app_error_keeps_its_status_details_and_headers(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    async def limited() -> None:
        raise RateLimitedError(retry_after_seconds=42)

    app.add_api_route("/api/_test/limited", limited)
    response = await client.get("/api/_test/limited")
    details = assert_error(response, 429, "RATE_LIMITED")
    assert details == {"retry_after_seconds": 42}
    assert response.headers["retry-after"] == "42"
    assert response.json()["message"] == RateLimitedError.default_message


async def test_a_taken_username_is_409_and_names_the_field(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    """What the profile form gets back when somebody else holds the name it asked for."""

    async def taken() -> None:
        raise UsernameTakenError

    app.add_api_route("/api/_test/taken", taken)
    details = assert_error(await client.get("/api/_test/taken"), 409, "USERNAME_TAKEN")
    assert details == {"field": "username"}


async def test_an_admin_who_traded_a_market_is_told_that_another_admin_has_to_settle_it(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    """What resolving and voiding answer such an admin (A.4): 403 with a sentence the
    admin screen shows as it is, and nothing in `details`."""

    async def traded() -> None:
        raise ForbiddenError(SELF_DEALING_MESSAGE)

    app.add_api_route("/api/_test/traded", traded)
    response = await client.get("/api/_test/traded")

    assert assert_error(response, 403, "FORBIDDEN") == {}
    assert response.json()["message"] == (
        "You traded this market, so another admin has to settle it."
    )


async def test_the_creator_of_a_batch_is_told_why_its_codes_are_not_theirs_to_redeem(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    """What redeeming answers whoever created the batch of the code (A.6): 403 with a
    sentence of its own, and nothing in `details`."""

    async def own_batch() -> None:
        raise ForbiddenError(OWN_BATCH_MESSAGE)

    app.add_api_route("/api/_test/own-batch", own_batch)
    response = await client.get("/api/_test/own-batch")

    assert assert_error(response, 403, "FORBIDDEN") == {}
    assert response.json()["message"] == "You created this batch, so you cannot redeem its codes."


async def test_an_admin_is_told_that_admins_do_not_redeem_codes(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    """What redeeming answers an admin, whoever created the batch (A.6): 403 with a
    sentence of its own, and nothing in `details`."""

    async def by_an_admin() -> None:
        raise ForbiddenError(ADMIN_REDEEM_MESSAGE)

    app.add_api_route("/api/_test/admin-redeems", by_an_admin)
    response = await client.get("/api/_test/admin-redeems")

    assert assert_error(response, 403, "FORBIDDEN") == {}
    assert response.json()["message"] == "Admins hand out codes and cannot redeem them."


async def test_a_reserved_username_is_422_and_names_the_field(
    client: httpx.AsyncClient, app: FastAPI, base_settings: Settings
) -> None:
    """What the profile form gets back for a name of RESERVED_USERNAMES."""

    async def reserved() -> None:
        raise ValidationFailedError(RESERVED_USERNAME_MESSAGE, details={"field": "username"})

    app.add_api_route("/api/_test/reserved", reserved)
    response = await client.get("/api/_test/reserved")

    assert assert_error(response, 422, "VALIDATION_ERROR") == {"field": "username"}
    assert response.json()["message"] == "This username is reserved. Pick another one."
    assert "admin" in base_settings.identity.reserved_usernames


async def test_schema_objects_in_details_are_serialised_like_responses(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    """`QUOTE_STALE` carries a fresh quote in `details.quote`: amounts stay strings."""
    holding = Holding(outcome="YES", shares=to_cr(1_910_000), value_cr=to_cr(1_000_000))

    async def stale() -> None:
        raise QuoteStaleError(
            details={"quote": holding, "as_dict": holding.model_dump(mode="json")}
        )

    app.add_api_route("/api/_test/stale", stale)
    details = assert_error(await client.get("/api/_test/stale"), 409, "QUOTE_STALE")
    assert details["quote"] == {"outcome": "YES", "shares": "1.91", "value_cr": "1"}
    assert details["as_dict"] == details["quote"]


async def test_an_unexpected_error_is_500_logged_and_tells_the_client_nothing(
    unhandled_client: httpx.AsyncClient, app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken() -> None:
        raise RuntimeError("password=hunter2 at 10.0.0.7")

    app.add_api_route("/api/_test/broken", broken)
    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        response = await unhandled_client.get("/api/_test/broken")

    details = assert_error(response, 500, "INTERNAL_ERROR")
    assert details == {}
    assert "hunter2" not in response.text
    assert "RuntimeError" not in response.text

    (record,) = [r for r in caplog.records if r.name == "app.api.errors"]
    assert "GET /api/_test/broken" in record.getMessage()
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], RuntimeError)


# --- Postgres down (SPEC §9: "everything returns 503") -----------------------------


def add_route_that_reads_the_database(app: FastAPI, path: str = "/api/_test/books") -> str:
    """The smallest thing every service does: open a unit of work and run one query."""
    container: Container = app.state.container

    async def read() -> dict[str, int]:
        async with container.uow_factory() as uow:
            return {"total_micro": await uow.accounts.total_balance()}

    app.add_api_route(path, read)
    return path


async def test_the_test_route_answers_while_postgres_is_up(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    response = await client.get(add_route_that_reads_the_database(app))
    assert (response.status_code, response.json()) == (200, {"total_micro": 0})


@pytest.mark.postgres_down
async def test_an_unreachable_postgres_is_503_not_500(
    client: httpx.AsyncClient, app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """No service translates a lost connection, so the API layer has to, once."""
    path = add_route_that_reads_the_database(app)

    with caplog.at_level(logging.WARNING, logger="app.api.errors"):
        response = await client.get(path)

    details = assert_error(response, 503, "SERVICE_UNAVAILABLE")
    assert details == {}
    (record,) = [r for r in caplog.records if r.name == "app.api.errors"]
    assert record.levelno == logging.WARNING
    assert f"GET {path}" in record.getMessage()


async def test_a_connection_postgres_dropped_mid_request_is_503(
    client: httpx.AsyncClient, app: FastAPI, owner_engine: AsyncEngine
) -> None:
    """What a restart of Postgres looks like to a request that was already running."""
    container: Container = app.state.container

    async def read_after_losing_the_connection() -> dict[str, int]:
        async with container.uow_factory() as uow:
            backend = await uow.session.scalar(text("SELECT pg_backend_pid()"))
            async with owner_engine.connect() as admin:
                await admin.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": backend})
            return {"total_micro": await uow.accounts.total_balance()}

    app.add_api_route("/api/_test/dropped", read_after_losing_the_connection)
    assert_error(await client.get("/api/_test/dropped"), 503, "SERVICE_UNAVAILABLE")

    # The pool replaces the dead connection: the next request is served again.
    response = await client.get(add_route_that_reads_the_database(app))
    assert response.status_code == 200


async def test_an_error_a_reachable_postgres_reports_stays_a_500(
    unhandled_client: httpx.AsyncClient, app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """A violated constraint that a service let through is a bug, not an outage."""
    container: Container = app.state.container

    async def break_a_constraint() -> None:
        async with container.uow_factory() as uow:
            await uow.session.execute(
                text("INSERT INTO accounts (id, kind) VALUES (gen_random_uuid(), 'VIS_ISSUANCE')")
            )

    app.add_api_route("/api/_test/constraint", break_a_constraint)
    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        response = await unhandled_client.get("/api/_test/constraint")

    assert_error(response, 500, "INTERNAL_ERROR")
    assert "uq_accounts_system_kind" not in response.text
    (record,) = [r for r in caplog.records if r.name == "app.api.errors"]
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], IntegrityError)


async def test_a_failed_statement_is_logged_without_the_values_it_was_sent_with(
    unhandled_client: httpx.AsyncClient, app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """The lookup of a caller is a statement with their address in it. Where such a
    statement fails, the log gets its text and the error of Postgres, and not the
    address: the engine hides the values of every statement."""
    container: Container = app.state.container
    address = "zoe.zuercher@ethz.ch"

    async def look_somebody_up_in_a_table_that_is_not_there() -> None:
        async with container.uow_factory() as uow:
            await uow.session.execute(
                text("SELECT id FROM no_such_table WHERE email = :address"), {"address": address}
            )

    app.add_api_route("/api/_test/lookup", look_somebody_up_in_a_table_that_is_not_there)
    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        response = await unhandled_client.get("/api/_test/lookup")

    assert_error(response, 500, "INTERNAL_ERROR")
    (record,) = [r for r in caplog.records if r.name == "app.api.errors"]
    assert record.exc_info is not None
    failed = record.exc_info[1]
    assert isinstance(failed, ProgrammingError)
    logged = logging.Formatter().format(record)
    assert "no_such_table" in logged, "what failed is still told"
    assert "hide_parameters" in logged
    for text_of_it in (logged, str(failed), repr(failed), response.text):
        assert address not in text_of_it
        assert "zoe" not in text_of_it


async def test_a_bug_that_is_an_os_error_stays_a_500(
    unhandled_client: httpx.AsyncClient, app: FastAPI
) -> None:
    async def missing_file() -> None:
        raise FileNotFoundError("/etc/marketh/secret.pem")

    app.add_api_route("/api/_test/file", missing_file)
    response = await unhandled_client.get("/api/_test/file")
    assert_error(response, 500, "INTERNAL_ERROR")
    assert "secret.pem" not in response.text


# --- A lock that is not granted in time (SPEC §8, §9) --------------------------------

IMPATIENT_MS: Final[int] = 200
"""A POSTGRES_LOCK_TIMEOUT_MS short enough to sit through in a test."""

LOCK_TIMEOUT_IN_MS: Final[str] = "SELECT setting::int FROM pg_settings WHERE name = 'lock_timeout'"


@pytest.fixture
async def impatient_app(settings: Settings, clock: FrozenClock) -> AsyncIterator[FastAPI]:
    """The application on a connection pool of its own, built with a lock timeout of
    `IMPATIENT_MS`. Its database is the one every other fixture uses."""
    postgres = settings.postgres.model_copy(update={"lock_timeout_ms": IMPATIENT_MS})
    container = Container.build(settings.model_copy(update={"postgres": postgres}), clock=clock)
    yield create_app(container)
    await container.stop()


async def test_every_connection_of_the_application_carries_the_lock_timeout(
    database: Database, settings: Settings, owner_engine: AsyncEngine
) -> None:
    """It is set as a connection opens, so no statement of the api or the relay can wait
    for a lock without end. The owner role's connections build and empty the schema;
    they are not the application's and keep Postgres' own setting, no timeout."""
    configured = settings.postgres.lock_timeout_ms

    async with database.engine.connect() as first, database.engine.connect() as second:
        for connection in (first, second):
            assert await connection.scalar(text(LOCK_TIMEOUT_IN_MS)) == configured
    async with owner_engine.connect() as owner:
        assert await owner.scalar(text(LOCK_TIMEOUT_IN_MS)) == 0


async def test_a_lock_that_is_not_granted_in_time_is_503_and_the_request_can_be_sent_again(
    impatient_app: FastAPI,
    settings: Settings,
    owner_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A request queued behind a lock must not outlive the client that waits for it. It
    fails once POSTGRES_LOCK_TIMEOUT_MS have passed, having changed nothing, and 503 is
    the answer after which a client sends the same request again."""
    container: Container = impatient_app.state.container

    async def lock_the_issuance_account() -> dict[str, int]:
        async with container.uow_factory() as uow:
            account = await uow.accounts.get_system_account(
                AccountKind.VIS_ISSUANCE, for_update=True
            )
            return {"balance_micro": account.balance_micro}

    impatient_app.add_api_route("/api/_test/locked", lock_the_issuance_account)
    transport = httpx.ASGITransport(app=impatient_app)
    origin = settings.app.public_base_url

    async with httpx.AsyncClient(transport=transport, base_url=origin) as impatient:
        async with owner_engine.connect() as holder:
            await holder.execute(
                text("SELECT id FROM accounts WHERE kind = 'VIS_ISSUANCE' FOR UPDATE")
            )
            started = time.monotonic()
            with caplog.at_level(logging.WARNING, logger="app.api.errors"):
                refused = await impatient.get("/api/_test/locked")
            waited_seconds = time.monotonic() - started
        # The holder is gone and its lock with it.
        served = await impatient.get("/api/_test/locked")

    assert_error(refused, 503, "SERVICE_UNAVAILABLE")
    assert waited_seconds >= IMPATIENT_MS / 2000, "it has to wait for the lock first"
    (record,) = [r for r in caplog.records if r.name == "app.api.errors"]
    assert "GET /api/_test/locked" in record.getMessage()
    assert "lock timeout" in record.getMessage()
    assert (served.status_code, served.json()) == (200, {"balance_micro": 0})


# --- A pool with no connection free (SPEC §9) ----------------------------------------

POOL_TIMEOUT_SECONDS: Final[int] = 1
"""The shortest POSTGRES_POOL_TIMEOUT_SECONDS there is."""


@pytest.fixture
async def crowded_app(settings: Settings, clock: FrozenClock) -> AsyncIterator[FastAPI]:
    """The application on a pool of one connection and no overflow, which gives up on a
    checkout after `POOL_TIMEOUT_SECONDS`. Its database is the one every other fixture
    uses."""
    postgres = settings.postgres.model_copy(
        update={"pool_size": 1, "max_overflow": 0, "pool_timeout_seconds": POOL_TIMEOUT_SECONDS}
    )
    container = Container.build(settings.model_copy(update={"postgres": postgres}), clock=clock)
    yield create_app(container)
    await container.stop()


async def test_a_pool_with_no_connection_free_is_503_once_the_pool_timeout_has_passed(
    crowded_app: FastAPI, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """Every connection of the process is in use. A request waits for one for
    POSTGRES_POOL_TIMEOUT_SECONDS and is then answered 503, the answer after which a
    client sends the same request again; it has reached Postgres with nothing."""
    container: Container = crowded_app.state.container
    path = add_route_that_reads_the_database(crowded_app)
    transport = httpx.ASGITransport(app=crowded_app)
    origin = settings.app.public_base_url

    async with httpx.AsyncClient(transport=transport, base_url=origin) as crowded:
        async with container.database.engine.connect() as the_one_connection:
            await the_one_connection.execute(text("SELECT 1"))
            started = time.monotonic()
            with caplog.at_level(logging.WARNING, logger="app.api.errors"):
                refused = await crowded.get(path)
            waited_seconds = time.monotonic() - started
        # The connection is back in the pool.
        served = await crowded.get(path)

    assert assert_error(refused, 503, "SERVICE_UNAVAILABLE") == {}
    assert waited_seconds >= POOL_TIMEOUT_SECONDS * 0.9, "it has to wait for the pool first"
    assert waited_seconds < POOL_TIMEOUT_SECONDS + 5, "and not for SQLAlchemy's own 30 s"
    (record,) = [r for r in caplog.records if r.name == "app.api.errors"]
    assert record.levelno == logging.WARNING
    assert f"GET {path}" in record.getMessage()
    assert (served.status_code, served.json()) == (200, {"total_micro": 0})


async def test_health_reports_postgres_down_once_it_waited_for_the_pool_in_vain(
    crowded_app: FastAPI, settings: Settings
) -> None:
    """The probe of Postgres takes a connection of the pool like any request. With none
    free it waits POSTGRES_POOL_TIMEOUT_SECONDS at most, and the answer says that
    Postgres did not answer: 503 with the four statuses."""
    container: Container = crowded_app.state.container
    transport = httpx.ASGITransport(app=crowded_app)

    async with httpx.AsyncClient(
        transport=transport, base_url=settings.app.public_base_url
    ) as crowded:
        async with container.database.engine.connect() as the_one_connection:
            await the_one_connection.execute(text("SELECT 1"))
            started = time.monotonic()
            while_crowded = await crowded.get("/api/health")
            waited_seconds = time.monotonic() - started
        once_free = await crowded.get("/api/health")

    details = assert_error(while_crowded, 503, "SERVICE_UNAVAILABLE")
    assert details["postgres"] == "down"
    assert POOL_TIMEOUT_SECONDS * 0.9 <= waited_seconds < POOL_TIMEOUT_SECONDS + 5
    assert (once_free.status_code, once_free.json()["postgres"]) == (200, "up")


async def test_a_checkout_that_timed_out_is_transient_as_the_pool_reports_it(
    crowded_app: FastAPI,
) -> None:
    """What `UserUpkeepService` and the monthly run rest on: a pool that had no
    connection free is told apart from a lost connection, like a lock timeout."""
    container: Container = crowded_app.state.container

    async with container.database.engine.connect() as the_one_connection:
        await the_one_connection.execute(text("SELECT 1"))
        with pytest.raises(PoolTimeoutError) as timed_out:
            async with container.uow_factory() as uow:
                await uow.accounts.total_balance()

    assert database_errors.is_transient(timed_out.value)
    assert database_errors.is_unavailable(timed_out.value)
    # The pool is whole: once the connection is back, the next call is served.
    async with container.uow_factory() as uow:
        assert await uow.accounts.total_balance() == 0


async def test_a_lock_timeout_and_a_lost_connection_are_told_apart_as_the_driver_reports_them(
    impatient_app: FastAPI, owner_engine: AsyncEngine
) -> None:
    """What `UserUpkeepService` rests on (A.3): it skips its turn on the first and
    lets the second leave as a 503. Both are errors `is_unavailable` recognises; only
    `is_transient` says which one it is."""
    container: Container = impatient_app.state.container

    async def lock_the_issuance_account() -> None:
        async with container.uow_factory() as uow:
            await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE, for_update=True)

    async def lose_the_connection() -> None:
        async with container.uow_factory() as uow:
            backend = await uow.session.scalar(text("SELECT pg_backend_pid()"))
            async with owner_engine.connect() as admin:
                await admin.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": backend})
            await uow.accounts.total_balance()

    async with owner_engine.connect() as holder:
        await holder.execute(text("SELECT id FROM accounts WHERE kind = 'VIS_ISSUANCE' FOR UPDATE"))
        with pytest.raises(DBAPIError) as timed_out:
            await lock_the_issuance_account()
    with pytest.raises(DBAPIError) as lost:
        await lose_the_connection()

    assert database_errors.is_transient(timed_out.value)
    assert database_errors.is_unavailable(timed_out.value)
    assert not database_errors.is_transient(lost.value)
    assert database_errors.is_unavailable(lost.value)
    # After a lock timeout the connection is whole: the same pool serves the next call.
    await lock_the_issuance_account()
