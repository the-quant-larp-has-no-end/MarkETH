"""What the voucher and code routes take from a request and hand to their service
(SPEC §2, §10, Appendix A.2).

A route parses its input, calls one service method and returns what that answers. Each
test puts a recorder where the service is, so it shows the route alone: which product,
voucher or code reaches the service, on whose behalf, and how the answer leaves.
"""

from datetime import timedelta
from typing import Final
from uuid import UUID, uuid7

import httpx
import pytest

from app.container import Container
from app.core.enums import UserRole, UserStatus, VoucherStatus
from app.core.errors import AppError, CodeInvalidError, ConflictError, NotFoundError
from app.core.identity import AuthenticatedUser
from app.core.units import MICRO, to_cr
from app.schemas.barista import VoucherRedeemResponse
from app.schemas.codes import RedeemCodeResponse
from app.schemas.vouchers import (
    IssueVoucherRequest,
    VoucherCodeResponse,
    VoucherIssuedResponse,
    VoucherItem,
    VoucherListResponse,
    VoucherProductItem,
    VoucherProductListResponse,
)
from tests.support.clock import TEST_NOW
from tests.support.factories import ActAs

pytestmark = pytest.mark.integration

VOUCHER_ID: Final[UUID] = uuid7()
EXPIRES_AT: Final = TEST_NOW + timedelta(days=30)
EXPIRES_AT_JSON: Final[str] = "2026-11-08T12:00:00Z"


def someone(role: UserRole = UserRole.USER) -> AuthenticatedUser:
    name = role.value.lower()
    return AuthenticatedUser(
        id=uuid7(),
        email=f"{name}@ethz.ch",
        name=name.capitalize(),
        username=name,
        degree_programme="BSc Computer Science",
        role=role,
        status=UserStatus.ACTIVE,
    )


def voucher(**overrides: object) -> dict[str, object]:
    """The fields of a `VoucherItem`: a voucher for Mate that took 35 CR."""
    return {
        "id": VOUCHER_ID,
        "product_id": "mate",
        "product_name": "Mate",
        "status": VoucherStatus.ACTIVE,
        "refunded": False,
        "value_cr": to_cr(35 * MICRO),
        "created_at": TEST_NOW,
        "expires_at": EXPIRES_AT,
        "redeemed_at": None,
    } | overrides


VOUCHER_JSON: Final[dict[str, object]] = {
    "id": str(VOUCHER_ID),
    "product_id": "mate",
    "product_name": "Mate",
    "status": "ACTIVE",
    "refunded": False,
    "value_cr": "35",
    "created_at": "2026-10-09T12:00:00Z",
    "expires_at": EXPIRES_AT_JSON,
    "redeemed_at": None,
}


class RecordedVouchers:
    """Stands in for `VoucherService`: remembers each call and answers with a voucher
    for Mate, or raises what a test tells it to refuse with."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.refusal: AppError | None = None
        self.repeat = False
        """What `redeem` answers as `repeat`: whether the scan is one of a voucher this
        barista redeemed a moment ago."""

    async def products(self, user: AuthenticatedUser) -> VoucherProductListResponse:
        self.calls.append(("products", user))
        return VoucherProductListResponse(
            items=[
                VoucherProductItem(
                    id="mate", name="Mate", price_cr=to_cr(35 * MICRO), affordable=True
                ),
                VoucherProductItem(
                    id="iced-tea", name="Iced Tea", price_cr=to_cr(35 * MICRO), affordable=True
                ),
                VoucherProductItem(
                    id="m-ms", name="M&Ms", price_cr=to_cr(15_500_000), affordable=False
                ),
            ]
        )

    async def issue(
        self, user: AuthenticatedUser, request: IssueVoucherRequest
    ) -> VoucherIssuedResponse:
        self.calls.append(("issue", user, request))
        return VoucherIssuedResponse.model_validate(
            voucher(code="7KQ2-M9XA", balance_cr=to_cr(465 * MICRO))
        )

    async def replace_code(self, user: AuthenticatedUser, voucher_id: UUID) -> VoucherCodeResponse:
        self.calls.append(("replace_code", user, voucher_id))
        if self.refusal is not None:
            raise self.refusal
        return VoucherCodeResponse(
            voucher_id=voucher_id, product_name="Mate", code="W3RT-0HJ8", expires_at=EXPIRES_AT
        )

    async def list_for_user(self, user: AuthenticatedUser) -> VoucherListResponse:
        self.calls.append(("list_for_user", user))
        return VoucherListResponse(items=[VoucherItem.model_validate(voucher())])

    async def redeem(self, barista: AuthenticatedUser, code: str) -> VoucherRedeemResponse:
        self.calls.append(("redeem", barista, code))
        if self.refusal is not None:
            raise self.refusal
        return VoucherRedeemResponse(
            voucher_id=VOUCHER_ID,
            product_id="mate",
            product_name="Mate",
            value_cr=to_cr(35 * MICRO),
            redeemed_at=TEST_NOW,
            repeat=self.repeat,
        )


class RecordedCodes:
    """Stands in for `CodeService.redeem`: remembers every argument it was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def redeem(self, *arguments: object) -> RedeemCodeResponse:
        self.calls.append(arguments)
        return RedeemCodeResponse(value_cr=to_cr(35 * MICRO), balance_cr=to_cr(535 * MICRO))


@pytest.fixture
def vouchers(container: Container, monkeypatch: pytest.MonkeyPatch) -> RecordedVouchers:
    recorded = RecordedVouchers()
    monkeypatch.setattr(container, "vouchers", recorded)
    return recorded


@pytest.fixture
def codes(container: Container, monkeypatch: pytest.MonkeyPatch) -> RecordedCodes:
    recorded = RecordedCodes()
    monkeypatch.setattr(container, "codes", recorded)
    return recorded


# --- Products ---------------------------------------------------------------------


async def test_the_products_are_listed_for_the_caller_in_the_order_the_service_gives(
    client: httpx.AsyncClient, as_user: ActAs, vouchers: RecordedVouchers
) -> None:
    user = someone()
    as_user(user)

    response = await client.get("/api/vouchers/products")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"id": "mate", "name": "Mate", "price_cr": "35", "affordable": True},
            {"id": "iced-tea", "name": "Iced Tea", "price_cr": "35", "affordable": True},
            {"id": "m-ms", "name": "M&Ms", "price_cr": "15.5", "affordable": False},
        ]
    }
    assert vouchers.calls == [("products", user)]


# --- Issuing ----------------------------------------------------------------------


async def test_a_voucher_is_issued_for_the_product_the_request_names(
    client: httpx.AsyncClient, as_user: ActAs, vouchers: RecordedVouchers
) -> None:
    user = someone()
    as_user(user)

    response = await client.post("/api/vouchers", json={"product_id": " mate "})

    assert response.status_code == 201
    assert response.json() == VOUCHER_JSON | {"code": "7KQ2-M9XA", "balance_cr": "465"}
    assert vouchers.calls == [("issue", user, IssueVoucherRequest(product_id="mate"))]


@pytest.mark.parametrize(
    ("body", "where", "rule"),
    [
        ({}, "product_id", "missing"),
        ({"product_id": ""}, "product_id", "string_too_short"),
        ({"product_id": "   "}, "product_id", "string_too_short"),
        ({"product_id": "p" * 65}, "product_id", "string_too_long"),
        ({"product_id": 7}, "product_id", "string_type"),
        ({"product_id": ["mate", "tea"]}, "product_id", "string_type"),
        ({"product_id": "mate", "price_cr": "1"}, "price_cr", "extra_forbidden"),
        ({"product_id": "mate", "quantity": 2}, "quantity", "extra_forbidden"),
        ({"product_id": "mate", "user_id": str(uuid7())}, "user_id", "extra_forbidden"),
    ],
    ids=[
        "no-product",
        "an-empty-id",
        "a-blank-id",
        "an-id-of-65-characters",
        "a-number",
        "two-products",
        "a-price-of-the-callers-own",
        "a-quantity",
        "somebody-elses-wallet",
    ],
)
async def test_a_request_for_a_voucher_names_one_product_and_nothing_else(
    client: httpx.AsyncClient,
    as_user: ActAs,
    vouchers: RecordedVouchers,
    body: dict[str, object],
    where: str,
    rule: str,
) -> None:
    """What a voucher takes and who pays for it is never the request's to say."""
    as_user(someone())

    response = await client.post("/api/vouchers", json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    errors = response.json()["details"]["errors"]
    assert [(error["loc"], error["type"]) for error in errors] == [(["body", where], rule)]
    assert vouchers.calls == []


async def test_the_wallet_lists_vouchers_with_their_product_and_without_a_code(
    client: httpx.AsyncClient, as_user: ActAs, vouchers: RecordedVouchers
) -> None:
    user = someone()
    as_user(user)

    response = await client.get("/api/me/vouchers")

    assert response.status_code == 200
    assert response.json() == {"items": [VOUCHER_JSON]}
    assert "code" not in response.text
    assert vouchers.calls == [("list_for_user", user)]


# --- A fresh code --------------------------------------------------------------------


async def test_a_fresh_code_is_asked_for_by_the_vouchers_id_alone(
    client: httpx.AsyncClient, as_user: ActAs, vouchers: RecordedVouchers
) -> None:
    user = someone()
    as_user(user)

    response = await client.post(f"/api/vouchers/{VOUCHER_ID}/code")

    assert response.status_code == 200
    assert response.json() == {
        "voucher_id": str(VOUCHER_ID),
        "product_name": "Mate",
        "code": "W3RT-0HJ8",
        "expires_at": EXPIRES_AT_JSON,
    }
    assert vouchers.calls == [("replace_code", user, VOUCHER_ID)]


async def test_a_fresh_code_is_for_the_caller_whoever_a_body_names(
    client: httpx.AsyncClient, as_user: ActAs, vouchers: RecordedVouchers
) -> None:
    """The route reads no body: whose voucher it is, the service decides from the caller."""
    user = someone()
    as_user(user)

    response = await client.post(
        f"/api/vouchers/{VOUCHER_ID}/code", json={"user_id": str(uuid7()), "code": "AAAA-AAAA"}
    )

    assert response.status_code == 200
    assert response.json()["code"] == "W3RT-0HJ8"
    assert vouchers.calls == [("replace_code", user, VOUCHER_ID)]


@pytest.mark.parametrize("not_an_id", ["7KQ2-M9XA", "1", "mate", "null"])
async def test_a_fresh_code_needs_the_id_of_a_voucher_not_its_code(
    client: httpx.AsyncClient, as_user: ActAs, vouchers: RecordedVouchers, not_an_id: str
) -> None:
    as_user(someone())

    response = await client.post(f"/api/vouchers/{not_an_id}/code")

    assert response.status_code == 422
    errors = response.json()["details"]["errors"]
    assert [error["loc"] for error in errors] == [["path", "voucher_id"]]
    assert vouchers.calls == []


async def test_a_voucher_that_is_unknown_or_somebody_elses_is_404(
    client: httpx.AsyncClient, as_user: ActAs, vouchers: RecordedVouchers
) -> None:
    """The service selects a voucher by its id and its owner together and answers both
    cases with one `NotFoundError`: whether a voucher of somebody else exists cannot be
    told from the answer."""
    user = someone()
    as_user(user)
    vouchers.refusal = NotFoundError()

    response = await client.post(f"/api/vouchers/{VOUCHER_ID}/code")

    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "message": "Not found.", "details": {}}
    assert "W3RT" not in response.text
    assert vouchers.calls == [("replace_code", user, VOUCHER_ID)]


async def test_an_own_voucher_that_is_used_up_or_expired_is_409_conflict(
    client: httpx.AsyncClient, as_user: ActAs, vouchers: RecordedVouchers
) -> None:
    """The caller's own voucher that is REDEEMED or EXPIRED, or past `expires_at`: it
    is theirs, so the answer says that its state is what stands in the way."""
    user = someone()
    as_user(user)
    vouchers.refusal = ConflictError()

    response = await client.post(f"/api/vouchers/{VOUCHER_ID}/code")

    assert response.status_code == 409
    assert response.json() == {
        "code": "CONFLICT",
        "message": "This is not possible in the current state.",
        "details": {},
    }
    assert vouchers.calls == [("replace_code", user, VOUCHER_ID)]


async def test_reading_a_voucher_code_is_not_offered(
    client: httpx.AsyncClient, as_user: ActAs, vouchers: RecordedVouchers
) -> None:
    """A code is made, never read back: the server has only its hash."""
    as_user(someone())

    for method in ("GET", "PUT", "DELETE"):
        response = await client.request(method, f"/api/vouchers/{VOUCHER_ID}/code")
        assert response.status_code == 405, method
    assert vouchers.calls == []


# --- Redeeming ----------------------------------------------------------------------


async def test_the_barista_is_told_which_product_a_scanned_voucher_is_for(
    client: httpx.AsyncClient, as_user: ActAs, vouchers: RecordedVouchers
) -> None:
    barista = someone(UserRole.BARISTA)
    as_user(barista)

    response = await client.post("/api/barista/vouchers/redeem", json={"code": " w3rt-0hj8 "})

    assert response.status_code == 200
    assert response.json() == {
        "voucher_id": str(VOUCHER_ID),
        "product_id": "mate",
        "product_name": "Mate",
        "value_cr": "35",
        "redeemed_at": "2026-10-09T12:00:00Z",
        "repeat": False,
    }
    assert vouchers.calls == [("redeem", barista, "w3rt-0hj8")]


async def test_a_repeated_scan_leaves_as_the_same_answer_marked_as_a_repeat(
    client: httpx.AsyncClient, as_user: ActAs, vouchers: RecordedVouchers
) -> None:
    """200 like the first scan: the screen tells the two apart by `repeat` alone."""
    barista = someone(UserRole.BARISTA)
    as_user(barista)
    vouchers.repeat = True

    response = await client.post("/api/barista/vouchers/redeem", json={"code": "W3RT-0HJ8"})

    assert response.status_code == 200
    assert response.json() == {
        "voucher_id": str(VOUCHER_ID),
        "product_id": "mate",
        "product_name": "Mate",
        "value_cr": "35",
        "redeemed_at": "2026-10-09T12:00:00Z",
        "repeat": True,
    }
    assert vouchers.calls == [("redeem", barista, "W3RT-0HJ8")]


@pytest.mark.parametrize(
    ("body", "where", "rule"),
    [
        ({}, "code", "missing"),
        ({"code": ""}, "code", "string_too_short"),
        ({"code": "   "}, "code", "string_too_short"),
        ({"code": "W" * 65}, "code", "string_too_long"),
        ({"code": 12345678}, "code", "string_type"),
        ({"code": ["W3RT-0HJ8", "7KQ2-M9XA"]}, "code", "string_type"),
        ({"code": "W3RT-0HJ8", "voucher_id": str(uuid7())}, "voucher_id", "extra_forbidden"),
        ({"code": "W3RT-0HJ8", "user_id": str(uuid7())}, "user_id", "extra_forbidden"),
    ],
    ids=[
        "no-code",
        "an-empty-code",
        "a-blank-code",
        "a-code-of-65-characters",
        "a-number",
        "two-codes",
        "a-voucher-by-its-id",
        "somebody-elses-wallet",
    ],
)
async def test_a_scan_sends_one_code_in_the_body_and_nothing_else(
    client: httpx.AsyncClient,
    as_user: ActAs,
    vouchers: RecordedVouchers,
    body: dict[str, object],
    where: str,
    rule: str,
) -> None:
    as_user(someone(UserRole.BARISTA))

    response = await client.post("/api/barista/vouchers/redeem", json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    errors = response.json()["details"]["errors"]
    assert [(error["loc"], error["type"]) for error in errors] == [(["body", where], rule)]
    assert vouchers.calls == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/barista/vouchers/W3RT-0HJ8/redeem"),
        ("POST", "/api/barista/vouchers/redeem/W3RT-0HJ8"),
        ("GET", "/api/barista/vouchers/W3RT-0HJ8"),
    ],
)
async def test_no_route_takes_the_code_of_a_voucher_in_its_path(
    client: httpx.AsyncClient,
    as_user: ActAs,
    vouchers: RecordedVouchers,
    method: str,
    path: str,
) -> None:
    """A path is written into logs on the way; the code goes in the body."""
    as_user(someone(UserRole.BARISTA))

    response = await client.request(method, path)

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert vouchers.calls == []


async def test_a_code_in_the_query_is_not_read(
    client: httpx.AsyncClient, as_user: ActAs, vouchers: RecordedVouchers
) -> None:
    as_user(someone(UserRole.BARISTA))

    response = await client.post("/api/barista/vouchers/redeem?code=W3RT-0HJ8")

    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["details"]["errors"]] == [["body"]]
    assert vouchers.calls == []


async def test_a_scan_the_service_refuses_leaves_as_the_one_generic_answer(
    client: httpx.AsyncClient, as_user: ActAs, vouchers: RecordedVouchers
) -> None:
    """Unknown, used, replaced or expired: the barista screen gets one answer, and it
    does not repeat the code."""
    as_user(someone(UserRole.BARISTA))
    vouchers.refusal = CodeInvalidError()

    response = await client.post("/api/barista/vouchers/redeem", json={"code": "W3RT-0HJ8"})

    assert response.status_code == 400
    assert response.json() == {
        "code": "CODE_INVALID",
        "message": "This code is not valid.",
        "details": {},
    }


async def test_a_code_is_redeemed_for_the_caller_and_the_service_gets_nothing_else(
    client: httpx.AsyncClient, as_user: ActAs, codes: RecordedCodes
) -> None:
    """The user and the code, as typed. Where a request came from is not passed on: the
    one limit on redeeming is per user."""
    user = someone()
    as_user(user)

    response = await client.post(
        "/api/codes/redeem",
        json={"code": " abcd-efgh-jkmn "},
        headers={"X-Real-IP": "203.0.113.7", "X-Forwarded-For": "203.0.113.7, 10.0.0.1"},
    )

    assert response.status_code == 200
    assert response.json() == {"value_cr": "35", "balance_cr": "535"}
    assert codes.calls == [(user, "abcd-efgh-jkmn")]
