"""Notices: what a user is told once (SPEC §6, Appendix A.5).

One thing writes a notice: the approval of a market somebody else proposed tells its
proposer, in the approval's own transaction. `GET /me` lists the notices its user has
not seen, oldest first; `POST /me/notices/{id}/seen` marks one, once.

The tests go through the lifecycle service and the routes. What is in the table is read
straight from it.
"""

import ast
import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from sqlalchemy import select

from app.container import Container
from app.core.enums import MarketStatus, MarketTier, NoticeKind, Outcome
from app.core.errors import ConflictError, LimitExceededError, NotFoundError
from app.core.identity import AuthenticatedUser
from app.core.units import MICRO, to_cr
from app.models import AuditLogEntry, Market, Notice, OutboxEvent, User
from app.schemas.admin.markets import (
    ApproveMarketRequest,
    CloseMarketRequest,
    HaltMarketRequest,
    RejectMarketRequest,
    ResolveMarketRequest,
    ResumeMarketRequest,
    VoidMarketRequest,
)
from app.schemas.markets import ProposeMarketRequest
from app.schemas.me import NoticeItem
from app.schemas.trading import TradeRequest
from app.services.notices import NoticeService
from tests.support.books import assert_books_balance, count_rows
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.database import BACKEND_ROOT
from tests.support.factories import ActAs, Factory, PortalHeaders

pytestmark = pytest.mark.integration

ME: Final[str] = "/api/me"
PROFILE: Final[str] = "/api/me/profile"

MINUTE: Final[timedelta] = timedelta(minutes=1)
HOUR: Final[timedelta] = timedelta(hours=1)

APPROVAL: Final[ApproveMarketRequest] = ApproveMarketRequest(tier=MarketTier.DEFAULT)

APP: Final[Path] = BACKEND_ROOT / "app"


def seen_path(notice_id: object) -> str:
    return f"/api/me/notices/{notice_id}/seen"


def as_sent(instant: datetime) -> str:
    """An instant as the API writes it."""
    return instant.isoformat().replace("+00:00", "Z")


async def notices(container: Container) -> Sequence[Notice]:
    """Every row of `notices`, oldest first."""
    async with container.uow_factory() as uow:
        statement = select(Notice).order_by(Notice.created_at, Notice.id)
        return (await uow.session.scalars(statement)).all()


async def notices_of(container: Container, user: User) -> Sequence[Notice]:
    return [notice for notice in await notices(container) if notice.user_id == user.id]


async def market_row(container: Container, market: Market) -> Market:
    async with container.uow_factory() as uow:
        row = await uow.markets.get(market.id)
    assert row is not None
    return row


@pytest.fixture
async def admin(factory: Factory) -> AuthenticatedUser:
    return factory.identity(await factory.admin(0))


@pytest.fixture
async def proposer(factory: Factory) -> User:
    return await factory.user(username="proposer")


async def approved(
    container: Container, factory: Factory, admin: AuthenticatedUser, proposer: User, **market: Any
) -> Market:
    """A proposal of `proposer` that `admin` approved just now."""
    proposal = await factory.market(status=MarketStatus.PROPOSED, created_by=proposer, **market)
    await container.market_lifecycle.approve(admin, proposal.id, APPROVAL)
    return proposal


# --- The approval writes the notice --------------------------------------------------


async def test_an_approval_tells_the_proposer_with_one_notice(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    admin: AuthenticatedUser,
    proposer: User,
) -> None:
    """Of the kind PROPOSAL_APPROVED, about that market, dated the instant of the
    approval, and not seen yet."""
    proposal = await factory.market(status=MarketStatus.PROPOSED, created_by=proposer)
    approved_at = clock.advance(HOUR)

    await container.market_lifecycle.approve(admin, proposal.id, APPROVAL)

    (notice,) = await notices(container)
    assert (notice.user_id, notice.kind, notice.market_id) == (
        proposer.id,
        NoticeKind.PROPOSAL_APPROVED,
        proposal.id,
    )
    assert (notice.created_at, notice.seen_at) == (approved_at, None)
    assert (await market_row(container, proposal)).opened_at == approved_at
    await assert_books_balance(container.uow_factory)


async def test_an_admin_who_approves_their_own_proposal_gets_no_notice(
    container: Container, factory: Factory
) -> None:
    """The approver knows. The market opens like any other."""
    admin = await factory.admin(0)
    proposal = await factory.market(status=MarketStatus.PROPOSED, created_by=admin)

    await container.market_lifecycle.approve(factory.identity(admin), proposal.id, APPROVAL)

    assert (await market_row(container, proposal)).status is MarketStatus.OPEN
    assert await notices(container) == []
    await assert_books_balance(container.uow_factory)


async def test_another_admin_who_approves_an_admins_proposal_tells_that_admin(
    container: Container, factory: Factory
) -> None:
    """The rule is about who approves, not about what the proposer is."""
    first, second = await factory.admin(0), await factory.admin(1)
    proposal = await factory.market(status=MarketStatus.PROPOSED, created_by=first)

    await container.market_lifecycle.approve(factory.identity(second), proposal.id, APPROVAL)

    (notice,) = await notices(container)
    assert notice.user_id == first.id
    assert await notices_of(container, second) == []
    await assert_books_balance(container.uow_factory)


async def test_an_approval_that_is_refused_writes_no_notice(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    admin: AuthenticatedUser,
    proposer: User,
) -> None:
    """Over the budget a market may have, and past the deadline of the proposal."""
    too_large = ApproveMarketRequest(
        tier=MarketTier.CUSTOM,
        budget_cr=to_cr(container.settings.risk.max_market_budget_micro + 1),
    )
    proposal = await factory.market(status=MarketStatus.PROPOSED, created_by=proposer)

    with pytest.raises(LimitExceededError):
        await container.market_lifecycle.approve(admin, proposal.id, too_large)
    clock.set(proposal.close_at)
    with pytest.raises(ConflictError):
        await container.market_lifecycle.approve(admin, proposal.id, APPROVAL)

    assert (await market_row(container, proposal)).status is MarketStatus.PROPOSED
    assert await notices(container) == []


async def test_the_notice_and_the_approval_commit_together_or_not_at_all(
    container: Container,
    factory: Factory,
    admin: AuthenticatedUser,
    proposer: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The notice is written inside the approval's transaction: when something fails
    after it was written, the notice is gone with everything else the approval did, and
    when the notice itself cannot be written, the market does not open."""
    proposal = await factory.market(status=MarketStatus.PROPOSED, created_by=proposer)
    written: list[int] = []
    proposal_approved = NoticeService.proposal_approved

    async def write_then_fail(self: NoticeService, *args: Any) -> None:
        await proposal_approved(self, *args)
        uow = args[0]
        written.append(len((await uow.session.scalars(select(Notice))).all()))
        raise RuntimeError("after the notice")

    async def fail(self: NoticeService, *args: Any) -> None:
        raise RuntimeError("no notice")

    for failing in (write_then_fail, fail):
        monkeypatch.setattr(NoticeService, "proposal_approved", failing)
        with pytest.raises(RuntimeError):
            await container.market_lifecycle.approve(admin, proposal.id, APPROVAL)

        row = await market_row(container, proposal)
        assert (row.status, row.b_micro, row.opened_at) == (MarketStatus.PROPOSED, 0, None)
        assert await notices(container) == []
        assert await count_rows(container.uow_factory, AuditLogEntry) == 0
        assert await count_rows(container.uow_factory, OutboxEvent) == 0

    assert written == [1], "the first failure came after the notice was in the transaction"
    monkeypatch.setattr(NoticeService, "proposal_approved", proposal_approved)
    await container.market_lifecycle.approve(admin, proposal.id, APPROVAL)
    assert len(await notices(container)) == 1
    await assert_books_balance(container.uow_factory)


# --- Nothing else writes one ---------------------------------------------------------


async def test_no_other_action_on_a_market_writes_a_notice(
    container: Container,
    factory: Factory,
    admin: AuthenticatedUser,
    proposer: User,
) -> None:
    """Proposing, rejecting, halting, resuming, closing, resolving and voiding, and a
    trade in between: the table holds the notice of the one approval throughout."""
    lifecycle = container.market_lifecycle
    trader = await factory.user(balance_micro=100 * MICRO)
    close_at = TEST_NOW + timedelta(days=3)
    proposal = ProposeMarketRequest(
        question="Will the ASVZ open a second climbing wall this semester?",
        yes_definition="It is open to members.",
        no_definition="It is not open to members.",
        resolution_criteria="The ASVZ website decides.",
        resolution_source="asvz.ch",
        close_at=close_at,
        resolve_by=close_at + timedelta(days=1),
        policy_accepted=True,
    )

    proposed = await container.markets.propose(factory.identity(proposer), proposal)
    turned_down = await container.markets.propose(factory.identity(proposer), proposal)
    assert await notices(container) == []
    await lifecycle.reject(admin, turned_down.id, RejectMarketRequest(reason="Asked twice."))
    assert await notices(container) == []

    await lifecycle.approve(admin, proposed.id, APPROVAL)
    (told,) = await notices(container)

    await container.trading.trade(
        factory.identity(trader),
        proposed.id,
        uuid7(),
        TradeRequest.model_validate({"side": "BUY", "outcome": "YES", "amount_cr": "10"}),
    )
    await lifecycle.halt(admin, proposed.id, HaltMarketRequest(reason="The source is in doubt."))
    await lifecycle.resume(admin, proposed.id, ResumeMarketRequest(note="It is back."))
    await lifecycle.close(admin, proposed.id, CloseMarketRequest(note="Decided early."))
    await lifecycle.resolve(
        admin, proposed.id, ResolveMarketRequest(outcome=Outcome.YES, evidence="It opened.")
    )
    voided = await approved(container, factory, admin, await factory.user())
    await lifecycle.close(admin, voided.id, CloseMarketRequest(note="It will not be decided."))
    await lifecycle.void(admin, voided.id, VoidMarketRequest(reason="The wall was moved."))

    assert [notice.id for notice in await notices_of(container, proposer)] == [told.id]
    assert len(await notices(container)) == 2, "one per approval, and nothing beside them"
    assert await notices_of(container, trader) == []
    await assert_books_balance(container.uow_factory)


def test_one_place_builds_a_notice_and_one_place_asks_for_it() -> None:
    """In the source of the application: only `NoticeService.proposal_approved` builds
    a `Notice` row, and only `MarketLifecycleService.approve` calls it."""
    builds: list[str] = []
    asks: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = path.relative_to(APP).as_posix()
            if isinstance(node.func, ast.Name) and node.func.id == "Notice":
                builds.append(name)
            if isinstance(node.func, ast.Attribute) and node.func.attr == "proposal_approved":
                asks.append(name)

    assert builds == ["services/notices.py"]
    assert asks == ["services/market_lifecycle.py"]


# --- GET /me lists the unseen ones ----------------------------------------------------


async def test_me_lists_the_unseen_notices_oldest_first_with_the_question_of_each_market(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    client: httpx.AsyncClient,
    as_user: ActAs,
    admin: AuthenticatedUser,
    proposer: User,
) -> None:
    """Three approvals an hour apart, the last two at one instant: in the order they
    happened, each with the five fields of the contract and nothing else."""
    first = await approved(container, factory, admin, proposer, question="Will it snow first?")
    later = clock.advance(HOUR)
    second = await approved(container, factory, admin, proposer, question="Will it snow next?")
    third = await approved(container, factory, admin, proposer, question="Will it snow last?")
    rows = await notices(container)
    as_user(proposer)

    response = await client.get(ME)

    assert response.status_code == 200
    assert [row.market_id for row in rows] == [first.id, second.id, third.id]
    assert rows[1].created_at == rows[2].created_at == later
    assert response.json()["notices"] == [
        {
            "id": str(row.id),
            "kind": "PROPOSAL_APPROVED",
            "market_id": str(market.id),
            "question": market.question,
            "created_at": as_sent(row.created_at),
        }
        for row, market in zip(rows, (first, second, third), strict=True)
    ]


async def test_a_notice_carries_the_question_as_the_approval_left_it(
    container: Container,
    factory: Factory,
    client: httpx.AsyncClient,
    as_user: ActAs,
    admin: AuthenticatedUser,
    proposer: User,
) -> None:
    """An approval may edit the wording. The notice is about the market that opened."""
    proposal = await factory.market(
        status=MarketStatus.PROPOSED, created_by=proposer, question="will it rain on friday"
    )
    edited = ApproveMarketRequest(
        tier=MarketTier.DEFAULT, question="Will it rain in Zurich on Friday?"
    )
    await container.market_lifecycle.approve(admin, proposal.id, edited)
    as_user(proposer)

    (notice,) = (await client.get(ME)).json()["notices"]

    assert notice["question"] == "Will it rain in Zurich on Friday?"
    assert notice["market_id"] == str(proposal.id)


async def test_me_lists_nobody_elses_notices_and_none_for_somebody_who_has_none(
    container: Container,
    factory: Factory,
    client: httpx.AsyncClient,
    as_user: ActAs,
    admin: AuthenticatedUser,
    proposer: User,
) -> None:
    other = await factory.user(username="other")
    mine = await approved(container, factory, admin, proposer)
    theirs = await approved(container, factory, admin, other)
    bystander = await factory.user(username="bystander")

    answers: dict[str, list[str]] = {}
    for user in (proposer, other, bystander):
        as_user(user)
        told = (await client.get(ME)).json()["notices"]
        answers[user.username] = [notice["market_id"] for notice in told]

    assert answers == {"proposer": [str(mine.id)], "other": [str(theirs.id)], "bystander": []}


async def test_the_answer_of_a_saved_profile_carries_the_notices_too(
    container: Container,
    factory: Factory,
    client: httpx.AsyncClient,
    portal_headers: PortalHeaders,
    admin: AuthenticatedUser,
    proposer: User,
) -> None:
    """`ProfileSavedResponse` is `MeResponse` and more: through the real headers."""
    opened = await approved(container, factory, admin, proposer)
    profile = {
        "username": proposer.username,
        "degree_level": proposer.degree_level.value,
        "degree_programme": proposer.degree_programme,
    }

    saved = await client.put(PROFILE, json=profile, headers=portal_headers(proposer))
    me = await client.get(ME, headers=portal_headers(proposer))

    assert saved.status_code == 200, saved.text
    assert [notice["market_id"] for notice in saved.json()["notices"]] == [str(opened.id)]
    assert saved.json()["notices"] == me.json()["notices"]


# --- POST /me/notices/{id}/seen ------------------------------------------------------


async def test_marking_a_notice_seen_answers_the_notice_and_takes_it_off_the_list(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    client: httpx.AsyncClient,
    as_user: ActAs,
    admin: AuthenticatedUser,
    proposer: User,
) -> None:
    """200 with the `NoticeItem`, as an acknowledged alert answers its alert. The other
    notice of the same user stays on the list."""
    marked = await approved(container, factory, admin, proposer, question="Will it hail?")
    kept = await approved(container, factory, admin, proposer, question="Will it thunder?")
    first, second = await notices(container)
    as_user(proposer)
    listed = (await client.get(ME)).json()["notices"]
    seen_at = clock.advance(MINUTE)

    response = await client.post(seen_path(first.id))

    assert response.status_code == 200, response.text
    assert response.json() == listed[0]
    assert NoticeItem.model_validate(response.json()).market_id == marked.id
    assert set(response.json()) == {"id", "kind", "market_id", "question", "created_at"}
    after = await notices(container)
    assert [(row.id, row.seen_at) for row in after] == [(first.id, seen_at), (second.id, None)]
    assert (await client.get(ME)).json()["notices"] == [listed[1]]
    assert listed[1]["market_id"] == str(kept.id)


async def test_a_second_call_answers_the_same_and_changes_nothing(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    client: httpx.AsyncClient,
    as_user: ActAs,
    admin: AuthenticatedUser,
    proposer: User,
) -> None:
    """`seen_at` is set once: it keeps the instant of the first call."""
    await approved(container, factory, admin, proposer)
    (notice,) = await notices(container)
    as_user(proposer)
    first_seen_at = clock.advance(MINUTE)

    first = await client.post(seen_path(notice.id))
    clock.advance(HOUR)
    second = await client.post(seen_path(notice.id))
    third = await client.post(seen_path(notice.id))

    assert (first.status_code, second.status_code, third.status_code) == (200, 200, 200)
    assert first.json() == second.json() == third.json()
    (stored,) = await notices(container)
    assert stored.seen_at == first_seen_at
    assert (await client.get(ME)).json()["notices"] == []


async def test_two_calls_at_once_mark_the_notice_once(
    container: Container,
    factory: Factory,
    client: httpx.AsyncClient,
    as_user: ActAs,
    admin: AuthenticatedUser,
    proposer: User,
) -> None:
    await approved(container, factory, admin, proposer)
    (notice,) = await notices(container)
    as_user(proposer)

    answers = await asyncio.gather(*(client.post(seen_path(notice.id)) for _ in range(8)))

    assert {answer.status_code for answer in answers} == {200}
    assert len({answer.text for answer in answers}) == 1
    (stored,) = await notices(container)
    assert stored.seen_at == container.clock.now()


async def test_an_unknown_notice_and_somebody_elses_are_both_404(
    container: Container,
    factory: Factory,
    client: httpx.AsyncClient,
    as_user: ActAs,
    admin: AuthenticatedUser,
    proposer: User,
) -> None:
    """The two are told apart by nothing, and somebody else's notice stays unseen: an
    admin cannot mark it either."""
    await approved(container, factory, admin, proposer)
    (notice,) = await notices(container)
    stranger = await factory.user(username="stranger")
    not_found = {"code": "NOT_FOUND", "message": NotFoundError.default_message, "details": {}}

    as_user(stranger)
    unknown = await client.post(seen_path(uuid7()))
    foreign = await client.post(seen_path(notice.id))
    as_user(await factory.admin(1))
    by_an_admin = await client.post(seen_path(notice.id))

    for response in (unknown, foreign, by_an_admin):
        assert (response.status_code, response.json()) == (404, not_found)
    (stored,) = await notices(container)
    assert stored.seen_at is None
    as_user(proposer)
    assert len((await client.get(ME)).json()["notices"]) == 1


@pytest.mark.parametrize("notice_id", ["7", "first", "0199c1d4-7a55-7f0e-9a53"])
async def test_a_notice_id_that_is_no_id_is_refused_in_a_sentence(
    factory: Factory, client: httpx.AsyncClient, as_user: ActAs, notice_id: str
) -> None:
    as_user(await factory.user())

    response = await client.post(seen_path(notice_id))

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    (error,) = response.json()["details"]["errors"]
    assert (error["loc"], error["msg"]) == (["path", "notice_id"], "This is not a valid id.")


async def test_the_service_refuses_what_the_route_refuses(
    container: Container, factory: Factory, admin: AuthenticatedUser, proposer: User
) -> None:
    """For whoever calls it: the user is part of the question the table is asked."""
    await approved(container, factory, admin, proposer)
    (notice,) = await notices(container)
    stranger = factory.identity(await factory.user())

    with pytest.raises(NotFoundError):
        await container.notices.mark_seen(stranger, notice.id)
    with pytest.raises(NotFoundError):
        await container.notices.mark_seen(factory.identity(proposer), UUID(int=0))

    told = await container.notices.mark_seen(factory.identity(proposer), notice.id)
    assert (told.id, told.kind) == (notice.id, NoticeKind.PROPOSAL_APPROVED)
