"""Every non-2xx response has one shape, and every guard holds (SPEC §10, §12, A.2).

`{code, message, details}` for whatever goes wrong: an unknown route, a body that does
not validate, a missing header, a foreign Origin, a missing session, a role that is not
enough, a service error, a bug, a database that is down. The guard tests walk the
OpenAPI document, so a route added later is covered without anyone remembering to add
it here.
"""

import logging
from collections.abc import AsyncIterator
from typing import Any, Final
from uuid import uuid7

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.container import Container
from app.core.enums import UserRole, UserStatus
from app.core.errors import QuoteStaleError, RateLimitedError
from app.core.identity import AuthenticatedUser
from app.core.units import to_cr
from app.schemas.markets import Holding
from tests.support.factories import ActAs

pytestmark = pytest.mark.integration

ERROR_KEYS: Final[set[str]] = {"code", "message", "details"}

PUBLIC_OPERATIONS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("GET", "/api/health"),
        ("GET", "/api/config"),
        ("POST", "/api/auth/request-link"),
        ("POST", "/api/auth/verify"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/markets"),
        ("GET", "/api/markets/{market_id}"),
        ("GET", "/api/markets/{market_id}/quote"),
        ("GET", "/api/markets/{market_id}/history"),
        ("GET", "/api/markets/{market_id}/stream"),
    }
)
"""What Appendix A.2 opens to everybody. Everything else needs a session."""

PATH_VALUES: Final[dict[str, str]] = {
    "{market_id}": str(uuid7()),
    "{batch_id}": str(uuid7()),
    "{alert_id}": "1",
    "{code}": "ABCD-EFGH",
}

TRADE_PATH: Final[str] = f"/api/markets/{uuid7()}/trades"
TRADE_BODY: Final[dict[str, str]] = {"side": "BUY", "outcome": "YES", "amount_cr": "0.25"}


def someone(role: UserRole, status: UserStatus = UserStatus.ACTIVE) -> AuthenticatedUser:
    name = role.value.lower()
    return AuthenticatedUser(
        id=uuid7(),
        email=f"{name}@student.ethz.ch",
        canonical_email=f"{name}@ethz.ch",
        role=role,
        status=status,
    )


def operations(app: FastAPI) -> list[tuple[str, str]]:
    """Every (METHOD, path template) the application serves under `/api`."""
    return sorted(
        (method.upper(), path) for path, item in app.openapi()["paths"].items() for method in item
    )


def protected(app: FastAPI, prefix: str = "/api/") -> list[tuple[str, str]]:
    return [
        (method, path)
        for method, path in operations(app)
        if (method, path) not in PUBLIC_OPERATIONS and path.startswith(prefix)
    ]


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
    client: httpx.AsyncClient, origin: str
) -> None:
    response = await client.post(
        "/api/auth/request-link", json={"email": "x@ethz.ch"}, headers={"Origin": origin}
    )
    assert_error(response, 403, "FORBIDDEN")


async def test_a_state_changing_request_without_an_origin_is_403(
    client: httpx.AsyncClient,
) -> None:
    del client.headers["Origin"]
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = await client.request(method, "/api/auth/logout")
        assert_error(response, 403, "FORBIDDEN")


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
        response = await client.post("/api/coffee-tickets", headers={"Origin": origin})
        assert_error(response, 401, "UNAUTHENTICATED")


# --- Validation ----------------------------------------------------------------


async def test_a_body_that_does_not_validate_is_422_with_the_field_errors(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/auth/request-link", json={"e_mail": "x@ethz.ch"})
    details = assert_error(response, 422, "VALIDATION_ERROR")
    errors = {tuple(error["loc"]): error for error in details["errors"]}
    assert errors[("body", "email")]["type"] == "missing"
    assert errors[("body", "e_mail")]["type"] == "extra_forbidden"
    for error in errors.values():
        assert set(error) == {"loc", "msg", "type"}, "the rejected input is not echoed back"


async def test_a_body_that_is_not_json_is_422(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/verify", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    details = assert_error(response, 422, "VALIDATION_ERROR")
    assert details["errors"][0]["loc"][0] == "body"


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


async def test_a_quote_query_is_validated_like_a_body(client: httpx.AsyncClient) -> None:
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
    assert "sized by shares" in details["errors"][0]["msg"]

    unknown = await client.get(
        path, params={"side": "BUY", "outcome": "YES", "shares": "1", "leverage": "10"}
    )
    details = assert_error(unknown, 422, "VALIDATION_ERROR")
    assert details["errors"][0]["loc"] == ["query", "leverage"]


async def test_a_path_parameter_that_is_not_a_uuid_is_422(client: httpx.AsyncClient) -> None:
    details = assert_error(await client.get("/api/markets/not-a-uuid"), 422, "VALIDATION_ERROR")
    assert details["errors"][0]["loc"] == ["path", "market_id"]


async def test_a_trade_without_an_idempotency_key_is_422(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    as_user(someone(UserRole.USER))
    response = await client.post(TRADE_PATH, json=TRADE_BODY)
    details = assert_error(response, 422, "VALIDATION_ERROR")
    assert details["errors"] == [
        {"loc": ["header", "Idempotency-Key"], "msg": "Field required", "type": "missing"}
    ]


async def test_an_idempotency_key_must_be_a_uuid(client: httpx.AsyncClient, as_user: ActAs) -> None:
    as_user(someone(UserRole.USER))
    response = await client.post(TRADE_PATH, json=TRADE_BODY, headers={"Idempotency-Key": "tap-1"})
    details = assert_error(response, 422, "VALIDATION_ERROR")
    assert details["errors"][0]["loc"] == ["header", "Idempotency-Key"]
    assert details["errors"][0]["type"] == "uuid_parsing"


# --- Sessions and roles ----------------------------------------------------------


async def test_every_route_that_is_not_public_answers_401_without_a_session(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    routes = protected(app)
    assert len(routes) >= 24
    for method, path in routes:
        response = await client.request(method, concrete(path))
        assert_error(response, 401, "UNAUTHENTICATED")


async def test_the_document_marks_exactly_the_routes_that_answer_401(app: FastAPI) -> None:
    """`security` in openapi.json is read off the guards, so it matches what they do:
    a session is required exactly where no session means 401."""
    paths = app.openapi()["paths"]
    needs_session = sorted(
        (method.upper(), path)
        for path, item in paths.items()
        for method, operation in item.items()
        if operation.get("security") and {} not in operation["security"]
    )
    assert needs_session == protected(app)


async def test_a_missing_session_is_reported_before_a_bad_body(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/markets", json={"question": 1})
    assert_error(response, 401, "UNAUTHENTICATED")


@pytest.mark.parametrize("role", [UserRole.USER, UserRole.BARISTA])
async def test_admin_routes_answer_403_to_anyone_but_an_admin(
    client: httpx.AsyncClient, app: FastAPI, as_user: ActAs, role: UserRole
) -> None:
    as_user(someone(role))
    routes = protected(app, "/api/admin/")
    assert len(routes) == 13
    for method, path in routes:
        response = await client.request(method, concrete(path))
        assert_error(response, 403, "FORBIDDEN")


async def test_barista_routes_answer_403_to_a_normal_user(
    client: httpx.AsyncClient, app: FastAPI, as_user: ActAs
) -> None:
    as_user(someone(UserRole.USER))
    routes = protected(app, "/api/barista/")
    assert routes == [("POST", "/api/barista/coffee-tickets/{code}/redeem")]
    for method, path in routes:
        response = await client.request(method, concrete(path))
        assert_error(response, 403, "FORBIDDEN")


@pytest.mark.parametrize("role", [UserRole.BARISTA, UserRole.ADMIN])
async def test_barista_routes_let_baristas_and_admins_through(
    client: httpx.AsyncClient, as_user: ActAs, role: UserRole
) -> None:
    as_user(someone(role))
    response = await client.post("/api/barista/coffee-tickets/ABCD-EFGH/redeem")
    assert response.status_code not in (401, 403)


async def test_admin_routes_let_an_admin_through(client: httpx.AsyncClient, as_user: ActAs) -> None:
    as_user(someone(UserRole.ADMIN))
    response = await client.post(f"/api/admin/markets/{uuid7()}/approve", json={})
    details = assert_error(response, 422, "VALIDATION_ERROR")
    assert details["errors"][0]["loc"] == ["body", "tier"]


@pytest.mark.parametrize("role", list(UserRole))
async def test_a_suspended_user_is_refused_everywhere_a_session_is_needed(
    client: httpx.AsyncClient, as_user: ActAs, role: UserRole
) -> None:
    as_user(someone(role, UserStatus.SUSPENDED))
    for method, path in (
        ("GET", "/api/me"),
        ("POST", "/api/coffee-tickets"),
        ("GET", "/api/admin/risk"),
        ("POST", "/api/barista/coffee-tickets/ABCD-EFGH/redeem"),
    ):
        response = await client.request(method, path)
        assert_error(response, 403, "FORBIDDEN")
        assert "suspended" in response.json()["message"]


async def test_as_user_none_makes_the_request_anonymous_again(
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


async def test_a_service_that_is_still_a_skeleton_answers_501(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    async def skeleton() -> None:
        raise NotImplementedError

    app.add_api_route("/api/_test/skeleton", skeleton)
    assert_error(await client.get("/api/_test/skeleton"), 501, "INTERNAL_ERROR")


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


async def test_a_bug_that_is_an_os_error_stays_a_500(
    unhandled_client: httpx.AsyncClient, app: FastAPI
) -> None:
    async def missing_file() -> None:
        raise FileNotFoundError("/etc/marketh/secret.pem")

    app.add_api_route("/api/_test/file", missing_file)
    response = await unhandled_client.get("/api/_test/file")
    assert_error(response, 500, "INTERNAL_ERROR")
    assert "secret.pem" not in response.text
