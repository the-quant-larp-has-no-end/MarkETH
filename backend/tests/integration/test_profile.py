"""`ProfileService.save`: how a person becomes a user, and changes their profile
(SPEC §3, §12 "Grants", Appendix A.3).

The first save writes the `users` row, the ledger account and the signup grant together
or not at all, and only for somebody who accepts the usage policy with it; every later
save edits the row and books nothing. Two advisory locks order what no row lock can:
two saves of one person, and two people who want one name.
The races are run against a real Postgres, where the second transaction really waits
for the first.

Most tests call the service the way the route does, with the identity the portal would
name. The last section goes through the route itself with the portal's two headers.
"""

import asyncio
from collections.abc import Sequence
from datetime import timedelta
from typing import Any, Final

import httpx
import pytest
from sqlalchemy import select

from app.container import Container
from app.core.config import Settings
from app.core.degrees import CATALOGUE
from app.core.enums import (
    SIGNUP_GRANT_PERIOD,
    SYSTEM_ACCOUNT_KINDS,
    AccountKind,
    DegreeLevel,
    JournalKind,
    UserRole,
    UserStatus,
)
from app.core.errors import (
    SUSPENDED_MESSAGE,
    ErrorCode,
    ForbiddenError,
    UsernameTakenError,
    ValidationFailedError,
)
from app.core.identity import USERNAME_STORED_MAX_LENGTH, PortalIdentity
from app.core.units import format_cr, to_cr
from app.models import Account, Grant, JournalEntry, Posting, User
from app.schemas.profile import ProfileSavedResponse, SaveProfileRequest
from app.services.ledger import LedgerRef, signup_grant_key
from app.services.profile import (
    POLICY_NOT_ACCEPTED_MESSAGE,
    RESERVED_USERNAME_MESSAGE,
    UNKNOWN_PROGRAMME_MESSAGE,
)
from tests.support.books import assert_books_balance, count_rows
from tests.support.clock import FrozenClock
from tests.support.factories import ActAs, Factory, PortalHeaders
from tests.support.settings import TEST_ADMIN_EMAILS, load_base_settings

pytestmark = pytest.mark.integration

type Degree = tuple[DegreeLevel, str]

BACHELOR: Final[Degree] = (CATALOGUE[0].level, CATALOGUE[0].programmes[0])
MASTER: Final[Degree] = (CATALOGUE[1].level, CATALOGUE[1].programmes[0])
DOCTORATE: Final[Degree] = (CATALOGUE[2].level, CATALOGUE[2].programmes[0])
"""Three pairs of the catalogue, each the first programme of its level."""

NEO: Final[PortalIdentity] = PortalIdentity(
    email="neo@ethz.ch", name="Thomas Anderson", role=UserRole.USER
)
TRINITY: Final[PortalIdentity] = PortalIdentity(
    email="trinity@ethz.ch", name="Trinity", role=UserRole.USER
)
"""Two people the portal signed in. Neither has a profile until a test saves one."""

IDENTITY: Final = load_base_settings().identity
MIN_LENGTH: Final[int] = IDENTITY.username_min_length
MAX_LENGTH: Final[int] = IDENTITY.username_max_length
RESERVED: Final[list[str]] = sorted(IDENTITY.reserved_usernames)
OUTSIDE_THE_LENGTHS: Final[list[int]] = sorted(
    length
    for length in {1, MIN_LENGTH - 1, MAX_LENGTH + 1, USERNAME_STORED_MAX_LENGTH}
    if 1 <= length <= USERNAME_STORED_MAX_LENGTH and not MIN_LENGTH <= length <= MAX_LENGTH
)
"""Lengths the request model lets through and the settings do not: the nearest on
either side, the shortest and the longest."""

NOTHING: Final[dict[str, int]] = {"users": 0, "accounts": 0, "grants": 0, "entries": 0}
ONE_PROFILE: Final[dict[str, int]] = {"users": 1, "accounts": 1, "grants": 1, "entries": 1}
"""What `written` counts before anybody saved a profile, and after exactly one person
did: their row, their account, their signup grant and the entry that booked it."""


def profile(
    username: str = "neo", degree: Degree = BACHELOR, *, policy_accepted: bool = True
) -> SaveProfileRequest:
    """What the set-up screen sends: a username, a degree and the usage policy,
    accepted unless a test says otherwise."""
    level, programme = degree
    return SaveProfileRequest(
        username=username,
        degree_level=level,
        degree_programme=programme,
        policy_accepted=policy_accepted,
    )


def edit(username: str = "neo", degree: Degree = BACHELOR) -> SaveProfileRequest:
    """What the profile screen sends: a username and a degree, and no word about the
    usage policy."""
    level, programme = degree
    return SaveProfileRequest(username=username, degree_level=level, degree_programme=programme)


def body(username: str = "neo", degree: Degree = BACHELOR) -> dict[str, object]:
    """The JSON a browser sends for `profile(username, degree)`."""
    level, programme = degree
    return {
        "username": username,
        "degree_level": level.value,
        "degree_programme": programme,
        "policy_accepted": True,
    }


async def written(container: Container) -> dict[str, int]:
    """How many users, user accounts, grants and journal entries the database holds."""
    uow_factory = container.uow_factory
    return {
        "users": await count_rows(uow_factory, User),
        "accounts": await count_rows(uow_factory, Account) - len(SYSTEM_ACCOUNT_KINDS),
        "grants": await count_rows(uow_factory, Grant),
        "entries": await count_rows(uow_factory, JournalEntry),
    }


async def user_of(container: Container, email: str) -> User | None:
    async with container.uow_factory() as uow:
        return await uow.users.get_by_email(email)


async def stored(container: Container, email: str) -> User:
    """The row of somebody who has a profile."""
    user = await user_of(container, email)
    assert user is not None
    return user


async def balance_of(container: Container, user: User) -> int:
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(user.id)
        assert account is not None
        return account.balance_micro


def what_a_person_stated(user: User) -> tuple[str, DegreeLevel, str]:
    return user.username, user.degree_level, user.degree_programme


def sorted_out(
    outcomes: Sequence[ProfileSavedResponse | BaseException],
) -> tuple[list[ProfileSavedResponse], list[UsernameTakenError]]:
    """The saves of a race that went through, and those refused for the name. A save
    that ended any other way fails the test."""
    saved = [outcome for outcome in outcomes if isinstance(outcome, ProfileSavedResponse)]
    refused = [outcome for outcome in outcomes if isinstance(outcome, UsernameTakenError)]
    assert len(saved) + len(refused) == len(outcomes), outcomes
    return saved, refused


# --- The first save ------------------------------------------------------------------


async def test_the_first_save_creates_the_user_the_account_and_books_the_signup_grant_once(
    container: Container, settings: Settings, clock: FrozenClock
) -> None:
    signup_micro = settings.grants.signup_grant_micro
    assert signup_micro > 0
    assert await written(container) == NOTHING

    saved = await container.profile.save(NEO, profile("neo", MASTER))

    assert saved.signup_granted is True
    assert (saved.email, saved.name, saved.username) == ("neo@ethz.ch", "Thomas Anderson", "neo")
    assert (saved.degree_level, saved.degree_programme) == MASTER
    assert saved.role is UserRole.USER
    assert saved.created_at == clock.now()
    assert saved.balance_cr == to_cr(signup_micro)
    assert await written(container) == ONE_PROFILE

    async with container.uow_factory() as uow:
        user = await uow.users.get_by_email("neo@ethz.ch")
        assert user is not None
        account = await uow.accounts.get_user_account(user.id)
        assert account is not None
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
        grant = (await uow.session.scalars(select(Grant))).one()
        entry = (await uow.session.scalars(select(JournalEntry))).one()
        postings = (await uow.session.scalars(select(Posting).order_by(Posting.amount_micro))).all()
    assert (user.id, user.name, user.status) == (saved.id, "Thomas Anderson", UserStatus.ACTIVE)
    assert what_a_person_stated(user) == ("neo", *MASTER)
    assert user.role is UserRole.USER
    assert user.created_at == user.policy_accepted_at == clock.now()
    assert saved.policy_accepted_at == clock.now()
    assert (account.kind, account.balance_micro) == (AccountKind.USER, signup_micro)
    assert (grant.user_id, grant.period, grant.entry_id) == (user.id, SIGNUP_GRANT_PERIOD, entry.id)
    assert grant.created_at == clock.now()
    assert (entry.kind, entry.ref_type, entry.ref_id) == (
        JournalKind.SIGNUP_GRANT,
        LedgerRef.GRANT,
        grant.id,
    )
    assert (entry.idempotency_key, entry.created_at) == (signup_grant_key(user.id), clock.now())
    assert [(posting.account_id, posting.amount_micro) for posting in postings] == [
        (issuance.id, -signup_micro),
        (account.id, signup_micro),
    ]
    await assert_books_balance(container.uow_factory)


async def test_a_person_the_portal_names_without_a_name_gets_a_profile_with_an_empty_one(
    container: Container,
) -> None:
    nameless = PortalIdentity(email="neo@ethz.ch", name="", role=UserRole.USER)

    saved = await container.profile.save(nameless, profile())

    assert (saved.name, saved.signup_granted) == ("", True)
    assert (await stored(container, "neo@ethz.ch")).name == ""
    await assert_books_balance(container.uow_factory)


async def test_a_first_save_that_fails_leaves_nothing_behind(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No user without an account and no account without its grant: the three are one
    transaction, and the person can simply save again."""

    async def closed(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the ledger is closed")

    with monkeypatch.context() as patched:
        patched.setattr(container.ledger, "post", closed)
        with pytest.raises(RuntimeError, match="the ledger is closed"):
            await container.profile.save(NEO, profile())
    assert await written(container) == NOTHING

    saved = await container.profile.save(NEO, profile())

    assert saved.signup_granted is True
    assert await written(container) == ONE_PROFILE
    await assert_books_balance(container.uow_factory)


# --- The usage policy ----------------------------------------------------------------


@pytest.mark.parametrize(
    "request_without_it",
    [profile(policy_accepted=False), edit()],
    ids=["declined", "left-out"],
)
async def test_a_profile_is_not_created_without_the_usage_policy_accepted(
    container: Container, request_without_it: SaveProfileRequest
) -> None:
    """422 on the field, and nothing is written: no user, no account, no grant."""
    with pytest.raises(ValidationFailedError) as refused:
        await container.profile.save(NEO, request_without_it)

    assert (refused.value.status_code, refused.value.code) == (422, ErrorCode.VALIDATION_ERROR)
    assert refused.value.details == {"field": "policy_accepted"}
    assert refused.value.message == POLICY_NOT_ACCEPTED_MESSAGE
    assert await written(container) == NOTHING

    saved = await container.profile.save(NEO, profile())

    assert saved.signup_granted is True
    assert await written(container) == ONE_PROFILE
    await assert_books_balance(container.uow_factory)


async def test_the_instant_the_policy_was_accepted_is_the_instant_the_profile_was_created(
    container: Container, clock: FrozenClock
) -> None:
    """Kept with the row and reported by every answer about the user. A later save,
    which says nothing about the policy, leaves it as it is."""
    created = await container.profile.save(NEO, profile())
    accepted_at = clock.now()
    clock.advance(timedelta(days=3))

    edited = await container.profile.save(NEO, edit("the_one", MASTER))
    declined = await container.profile.save(NEO, profile("neo", policy_accepted=False))

    assert created.policy_accepted_at == edited.policy_accepted_at == accepted_at
    assert declined.policy_accepted_at == accepted_at, "an edit does not look at the field"
    user = await stored(container, NEO.email)
    assert (user.policy_accepted_at, user.created_at) == (accepted_at, accepted_at)
    assert await written(container) == ONE_PROFILE
    await assert_books_balance(container.uow_factory)


async def test_the_policy_is_asked_for_before_the_username_is_looked_up(
    container: Container, factory: Factory
) -> None:
    """A newcomer who asks for a taken name without accepting the policy hears of the
    policy: a mistake in the request comes before a clash with somebody else."""
    await factory.user(username="the_one")
    before = await written(container)

    with pytest.raises(ValidationFailedError) as refused:
        await container.profile.save(NEO, profile("the_one", policy_accepted=False))

    assert refused.value.details == {"field": "policy_accepted"}
    with pytest.raises(UsernameTakenError):
        await container.profile.save(NEO, profile("the_one"))
    assert await written(container) == before


async def test_one_of_two_first_saves_without_the_policy_does_not_stop_the_other(
    container: Container,
) -> None:
    """A double tap of which only one carries the acceptance: whichever order they run
    in, the profile is created once and the other save either is refused or edits it."""
    outcomes = await asyncio.gather(
        container.profile.save(NEO, profile("neo")),
        container.profile.save(NEO, profile("neo", policy_accepted=False)),
        return_exceptions=True,
    )

    saved = [outcome for outcome in outcomes if isinstance(outcome, ProfileSavedResponse)]
    refused = [outcome for outcome in outcomes if isinstance(outcome, ValidationFailedError)]
    assert len(saved) + len(refused) == 2, outcomes
    assert isinstance(outcomes[0], ProfileSavedResponse), "the one that accepted goes through"
    assert [one.signup_granted for one in saved].count(True) == 1
    assert all(error.details == {"field": "policy_accepted"} for error in refused)
    assert await written(container) == ONE_PROFILE
    await assert_books_balance(container.uow_factory)


# --- Every later save ------------------------------------------------------------------


async def test_a_second_save_edits_the_profile_and_grants_nothing(
    container: Container, settings: Settings, clock: FrozenClock
) -> None:
    created = await container.profile.save(NEO, profile("neo", BACHELOR))
    signed_up_at = clock.now()
    clock.advance(timedelta(hours=3))
    renamed_by_the_portal = PortalIdentity(email=NEO.email, name="Neo", role=UserRole.USER)

    edited = await container.profile.save(renamed_by_the_portal, edit("the_one", MASTER))

    assert edited.signup_granted is False
    assert (edited.id, edited.email, edited.created_at) == (created.id, NEO.email, signed_up_at)
    assert (edited.username, edited.name) == ("the_one", "Neo")
    assert (edited.degree_level, edited.degree_programme) == MASTER
    assert edited.balance_cr == created.balance_cr == to_cr(settings.grants.signup_grant_micro)
    user = await stored(container, NEO.email)
    assert (user.id, user.name, user.created_at) == (created.id, "Neo", signed_up_at)
    assert what_a_person_stated(user) == ("the_one", *MASTER)
    assert await written(container) == ONE_PROFILE
    await assert_books_balance(container.uow_factory)


async def test_a_profile_can_be_changed_as_often_as_its_owner_likes(
    container: Container, settings: Settings
) -> None:
    await container.profile.save(NEO, profile("neo", BACHELOR))

    for username, degree in (("neo_2", MASTER), ("neo_3", DOCTORATE), ("neo", BACHELOR)):
        saved = await container.profile.save(NEO, edit(username, degree))
        assert (saved.username, saved.degree_level, saved.degree_programme) == (username, *degree)
        assert saved.signup_granted is False

    assert await written(container) == ONE_PROFILE
    assert await balance_of(container, await stored(container, NEO.email)) == (
        settings.grants.signup_grant_micro
    )
    await assert_books_balance(container.uow_factory)


async def test_saving_the_name_one_already_holds_is_no_clash(
    container: Container, factory: Factory
) -> None:
    await factory.user(email=NEO.email, username="neo")

    saved = await container.profile.save(NEO, profile("neo", MASTER))

    assert (saved.username, saved.degree_level, saved.degree_programme) == ("neo", *MASTER)
    assert what_a_person_stated(await stored(container, NEO.email)) == ("neo", *MASTER)


async def test_a_save_without_a_name_from_the_portal_keeps_the_name_the_profile_has(
    container: Container, factory: Factory
) -> None:
    await factory.user(email=NEO.email, username="neo", name="Thomas Anderson")
    nameless = PortalIdentity(email=NEO.email, name="", role=UserRole.USER)

    saved = await container.profile.save(nameless, profile("neo", MASTER))

    assert saved.name == "Thomas Anderson"
    assert (await stored(container, NEO.email)).name == "Thomas Anderson"


async def test_a_suspended_profile_cannot_be_changed(
    container: Container, factory: Factory
) -> None:
    """The guard of the route refuses a suspended caller first. The service says the
    same to one who was suspended after the guard had looked."""
    await factory.user(email=NEO.email, username="neo", status=UserStatus.SUSPENDED)
    before = await stored(container, NEO.email)

    with pytest.raises(ForbiddenError) as refused:
        await container.profile.save(NEO, profile("the_one", MASTER))

    assert (refused.value.status_code, refused.value.message) == (403, SUSPENDED_MESSAGE)
    after = await stored(container, NEO.email)
    assert what_a_person_stated(after) == what_a_person_stated(before) == ("neo", *BACHELOR)
    assert (after.name, after.status) == (before.name, UserStatus.SUSPENDED)


# --- One person, two saves at once ---------------------------------------------------


@pytest.mark.parametrize(
    "usernames",
    [
        ["neo", "neo"],
        ["neo", "the_one"],
        [f"neo_{tab}" for tab in range(8)],
    ],
    ids=["a-double-tap", "two-tabs", "eight-tabs"],
)
async def test_concurrent_first_saves_of_one_person_give_one_user_and_one_grant(
    container: Container, settings: Settings, usernames: list[str]
) -> None:
    """The second waits for the first on `profile:{email}`, finds the row it committed
    and edits it."""
    signup_cr = to_cr(settings.grants.signup_grant_micro)

    saves = await asyncio.gather(
        *(container.profile.save(NEO, profile(username)) for username in usernames)
    )

    assert sorted(saved.signup_granted for saved in saves) == [False] * (len(saves) - 1) + [True]
    assert len({saved.id for saved in saves}) == 1
    assert {saved.balance_cr for saved in saves} == {signup_cr}
    assert await written(container) == ONE_PROFILE
    user = await stored(container, NEO.email)
    assert user.username in usernames
    assert to_cr(await balance_of(container, user)) == signup_cr
    await assert_books_balance(container.uow_factory)


# --- A name somebody else holds --------------------------------------------------------


async def test_a_name_somebody_else_holds_is_409_username_taken_and_creates_nothing(
    container: Container, factory: Factory
) -> None:
    holder = await factory.user(username="the_one")
    before = await written(container)

    with pytest.raises(UsernameTakenError) as taken:
        await container.profile.save(NEO, profile("the_one"))

    assert (taken.value.status_code, taken.value.code) == (409, ErrorCode.USERNAME_TAKEN)
    assert taken.value.details == {"field": "username"}
    assert await user_of(container, NEO.email) is None
    assert await written(container) == before
    assert (await stored(container, holder.email)).username == "the_one"


async def test_renaming_to_a_name_somebody_else_holds_is_409_and_changes_nothing(
    container: Container, factory: Factory
) -> None:
    """The degree of the same request is not saved either: a save is all or nothing."""
    await factory.user(username="the_one")
    await factory.user(email=NEO.email, username="neo", name="Thomas Anderson")
    renamed_by_the_portal = PortalIdentity(email=NEO.email, name="Neo", role=UserRole.USER)

    with pytest.raises(UsernameTakenError) as taken:
        await container.profile.save(renamed_by_the_portal, profile("the_one", MASTER))

    assert taken.value.details == {"field": "username"}
    unchanged = await stored(container, NEO.email)
    assert what_a_person_stated(unchanged) == ("neo", *BACHELOR)
    assert unchanged.name == "Thomas Anderson"


async def test_a_name_is_free_again_as_soon_as_its_holder_took_another(
    container: Container,
) -> None:
    await container.profile.save(NEO, profile("the_one"))
    with pytest.raises(UsernameTakenError):
        await container.profile.save(TRINITY, profile("the_one"))

    await container.profile.save(NEO, profile("neo"))
    saved = await container.profile.save(TRINITY, profile("the_one"))

    assert (saved.username, saved.signup_granted) == ("the_one", True)
    assert (await stored(container, NEO.email)).username == "neo"
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("people", [2, 6])
async def test_people_racing_for_one_name_end_with_one_holder_and_everyone_else_409(
    container: Container, people: int
) -> None:
    """They wait for each other on `username:{name}`: the first to commit holds the
    name, and everybody after reads that."""
    racers = [
        PortalIdentity(email=f"racer{number}@ethz.ch", name="", role=UserRole.USER)
        for number in range(people)
    ]

    outcomes = await asyncio.gather(
        *(container.profile.save(racer, profile("the_one")) for racer in racers),
        return_exceptions=True,
    )

    winners, refused = sorted_out(outcomes)
    assert (len(winners), len(refused)) == (1, people - 1)
    assert all(error.details == {"field": "username"} for error in refused)
    assert winners[0].signup_granted is True
    assert await written(container) == ONE_PROFILE
    async with container.uow_factory() as uow:
        holder = await uow.users.get_by_username("the_one")
    assert holder is not None
    assert holder.email == winners[0].email
    await assert_books_balance(container.uow_factory)


async def test_a_newcomer_and_a_user_who_renames_cannot_both_get_one_name(
    container: Container, factory: Factory
) -> None:
    """A first save against an edit: whichever commits first holds the name, and the
    other is left as it was."""
    await factory.user(email=TRINITY.email, username="trinity")

    outcomes = await asyncio.gather(
        container.profile.save(NEO, profile("the_one")),
        container.profile.save(TRINITY, profile("the_one", MASTER)),
        return_exceptions=True,
    )

    winners, refused = sorted_out(outcomes)
    assert (len(winners), len(refused)) == (1, 1)
    async with container.uow_factory() as uow:
        holder = await uow.users.get_by_username("the_one")
    assert holder is not None
    assert holder.email == winners[0].email
    trinity = await stored(container, TRINITY.email)
    if holder.email == NEO.email:
        assert what_a_person_stated(trinity) == ("trinity", *BACHELOR)
    else:
        assert what_a_person_stated(trinity) == ("the_one", *MASTER)
        assert await user_of(container, NEO.email) is None
    await assert_books_balance(container.uow_factory)


# --- What a save is refused for, on its field ------------------------------------------


def refused_on(error: pytest.ExceptionInfo[ValidationFailedError], field: str) -> str:
    """The error is the 422 that names `field`; returns its message."""
    assert (error.value.status_code, error.value.code) == (422, ErrorCode.VALIDATION_ERROR)
    assert error.value.details == {"field": field}
    return error.value.message


@pytest.mark.parametrize("username", RESERVED)
async def test_a_reserved_name_is_422_on_the_username(container: Container, username: str) -> None:
    with pytest.raises(ValidationFailedError) as refused:
        await container.profile.save(NEO, profile(username))

    assert refused_on(refused, "username") == RESERVED_USERNAME_MESSAGE
    assert await written(container) == NOTHING


@pytest.mark.parametrize(
    "who",
    [
        PortalIdentity(email=TEST_ADMIN_EMAILS[0], name="Ada Admin", role=UserRole.ADMIN),
        PortalIdentity(email="bea@ethz.ch", name="Bea Barista", role=UserRole.BARISTA),
    ],
    ids=["an-admin", "a-barista"],
)
async def test_nobody_gets_a_reserved_name_whatever_their_role(
    container: Container, who: PortalIdentity
) -> None:
    for username in ("admin", "barista", "vis"):
        with pytest.raises(ValidationFailedError) as refused:
            await container.profile.save(who, profile(username))
        assert refused_on(refused, "username") == RESERVED_USERNAME_MESSAGE
    assert await written(container) == NOTHING


async def test_a_profile_cannot_be_renamed_to_a_reserved_name(
    container: Container, factory: Factory
) -> None:
    await factory.user(email=NEO.email, username="neo")

    with pytest.raises(ValidationFailedError) as refused:
        await container.profile.save(NEO, profile("official", MASTER))

    assert refused_on(refused, "username") == RESERVED_USERNAME_MESSAGE
    assert what_a_person_stated(await stored(container, NEO.email)) == ("neo", *BACHELOR)


@pytest.mark.parametrize("length", OUTSIDE_THE_LENGTHS)
async def test_a_username_of_a_length_outside_the_settings_is_422_on_the_username(
    container: Container, length: int
) -> None:
    with pytest.raises(ValidationFailedError) as refused:
        await container.profile.save(NEO, profile("x" * length))

    message = refused_on(refused, "username")
    assert str(MIN_LENGTH) in message
    assert str(MAX_LENGTH) in message
    assert await written(container) == NOTHING


@pytest.mark.parametrize("length", sorted({MIN_LENGTH, MAX_LENGTH}))
async def test_a_username_of_the_shortest_and_of_the_longest_length_is_taken(
    container: Container, length: int
) -> None:
    saved = await container.profile.save(NEO, profile("x" * length))

    assert (saved.username, saved.signup_granted) == ("x" * length, True)
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "degree",
    [
        (MASTER[0], "MSc Underwater Basket Weaving"),
        (BACHELOR[0], MASTER[1]),
        (DegreeLevel.OTHER, BACHELOR[1]),
        (MASTER[0], MASTER[1].lower()),
    ],
    ids=[
        "an-unknown-programme",
        "a-master-programme-for-a-bachelor",
        "a-bachelor-programme-for-other",
        "a-programme-in-another-spelling",
    ],
)
async def test_a_degree_the_catalogue_does_not_list_as_a_pair_is_422_on_the_programme(
    container: Container, degree: Degree
) -> None:
    with pytest.raises(ValidationFailedError) as refused:
        await container.profile.save(NEO, profile("neo", degree))

    assert refused_on(refused, "degree_programme") == UNKNOWN_PROGRAMME_MESSAGE
    assert await written(container) == NOTHING


async def test_a_degree_that_is_refused_changes_nothing_of_an_existing_profile(
    container: Container, factory: Factory
) -> None:
    await factory.user(email=NEO.email, username="neo")

    with pytest.raises(ValidationFailedError) as refused:
        await container.profile.save(NEO, profile("the_one", (BACHELOR[0], MASTER[1])))

    refused_on(refused, "degree_programme")
    assert what_a_person_stated(await stored(container, NEO.email)) == ("neo", *BACHELOR)


async def test_a_profile_can_state_a_programme_of_every_level(container: Container) -> None:
    """The last programme of each level: the levels differ in nothing."""
    for entry in CATALOGUE:
        degree = (entry.level, entry.programmes[-1])
        saved = await container.profile.save(NEO, profile("neo", degree))
        assert (saved.degree_level, saved.degree_programme) == degree
    assert await written(container) == ONE_PROFILE
    await assert_books_balance(container.uow_factory)


async def test_the_username_is_checked_before_the_degree(container: Container) -> None:
    """The rules are asked in one order, so a form with two mistakes hears of the
    same one every time."""
    no_such_degree = (BACHELOR[0], MASTER[1])

    for username in ("x" * (MAX_LENGTH + 1), RESERVED[0]):
        with pytest.raises(ValidationFailedError) as refused:
            await container.profile.save(NEO, profile(username, no_such_degree))
        refused_on(refused, "username")


@pytest.mark.postgres_down
async def test_what_a_save_is_refused_for_is_decided_before_any_transaction(
    container: Container,
) -> None:
    """With Postgres unreachable the three rules still answer: none of them asks it."""
    assert not await container.database.ping()

    for request, field in (
        (profile("x" * (MAX_LENGTH + 1)), "username"),
        (profile(RESERVED[0]), "username"),
        (profile("neo", (BACHELOR[0], MASTER[1])), "degree_programme"),
    ):
        with pytest.raises(ValidationFailedError) as refused:
            await container.profile.save(NEO, request)
        refused_on(refused, field)


# --- The role in the answer and in the row -----------------------------------------------


@pytest.mark.parametrize("role", list(UserRole))
async def test_the_answer_carries_the_role_of_the_identity_and_the_row_starts_as_user(
    container: Container, factory: Factory, role: UserRole
) -> None:
    """The service answers with the role the request was let in with and writes USER
    into every new row, whoever creates it: a bootstrap admin is one by its address.
    `GET /me` then reports what the row and the address amount to."""
    named = PortalIdentity(email=NEO.email, name=NEO.name, role=role)

    created = await container.profile.save(named, profile())
    edited = await container.profile.save(named, edit("the_one"))

    assert created.role is edited.role is role
    user = await stored(container, NEO.email)
    assert user.role is UserRole.USER
    assert (await container.account.me(factory.identity(user))).role is UserRole.USER
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("given", [UserRole.BARISTA, UserRole.ADMIN])
async def test_saving_a_profile_leaves_the_role_an_admin_gave(
    container: Container, factory: Factory, given: UserRole
) -> None:
    """Nobody changes their own role by saving their profile."""
    await factory.user(email=NEO.email, username="neo", role=given)
    as_they_are = PortalIdentity(email=NEO.email, name=NEO.name, role=given)

    saved = await container.profile.save(as_they_are, edit("the_one", MASTER))

    assert saved.role is given
    user = await stored(container, NEO.email)
    assert (user.role, user.username) == (given, "the_one")


# --- Through the route, as the portal names the caller ---------------------------------


ME_FIELDS: Final[set[str]] = {
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


def error_of(response: httpx.Response, status_code: int, code: str) -> dict[str, Any]:
    """The response is the documented error; returns its `details`."""
    assert response.status_code == status_code, response.text
    answer = response.json()
    assert set(answer) == {"code", "message", "details"}
    assert answer["code"] == code
    details: dict[str, Any] = answer["details"]
    return details


async def test_a_newcomer_sets_up_a_profile_through_the_portal_headers(
    client: httpx.AsyncClient,
    container: Container,
    settings: Settings,
    portal_headers: PortalHeaders,
) -> None:
    """The whole way: the two headers, `IdentityService`, the guard, the service."""
    zoe = PortalIdentity(email="zoe@ethz.ch", name="Zoë Müller", role=UserRole.USER)
    headers = portal_headers(zoe)
    signup_cr = format_cr(settings.grants.signup_grant_micro)
    needs_a_profile = await client.get("/api/me", headers=headers)
    assert error_of(needs_a_profile, 403, "PROFILE_REQUIRED") == {
        "name": "Zoë Müller",
        "email": "zoe@ethz.ch",
    }

    created = await client.put("/api/me/profile", json=body("zoe", MASTER), headers=headers)

    assert created.status_code == 200, created.text
    answer = created.json()
    assert set(answer) == ME_FIELDS | {"signup_granted"}
    assert answer["signup_granted"] is True
    assert (answer["email"], answer["name"], answer["username"]) == (
        "zoe@ethz.ch",
        "Zoë Müller",
        "zoe",
    )
    assert (answer["degree_level"], answer["degree_programme"]) == (MASTER[0].value, MASTER[1])
    assert (answer["role"], answer["balance_cr"]) == ("USER", signup_cr)
    assert answer["policy_accepted_at"] == answer["created_at"]
    assert await written(container) == ONE_PROFILE

    me = await client.get("/api/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json() == {field: answer[field] for field in ME_FIELDS}

    # The profile screen sends no word about the policy.
    changed = {
        key: value for key, value in body("zoe_m", BACHELOR).items() if key != "policy_accepted"
    }
    edited = await client.put("/api/me/profile", json=changed, headers=headers)
    assert edited.status_code == 200, edited.text
    assert (edited.json()["signup_granted"], edited.json()["username"]) == (False, "zoe_m")
    assert (edited.json()["id"], edited.json()["balance_cr"]) == (answer["id"], signup_cr)
    assert await written(container) == ONE_PROFILE
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    ("email", "role"),
    [
        (TEST_ADMIN_EMAILS[0], "ADMIN"),
        (TEST_ADMIN_EMAILS[1], "ADMIN"),
        ("someone@ethz.ch", "USER"),
    ],
)
async def test_a_bootstrap_admin_is_an_admin_from_its_first_save_and_everybody_else_a_user(
    client: httpx.AsyncClient,
    container: Container,
    portal_headers: PortalHeaders,
    email: str,
    role: str,
) -> None:
    """Nothing in the request says admin. The address does, to the application, and
    the row it writes says USER for both."""
    named = PortalIdentity(email=email, name="", role=UserRole.USER)

    created = await client.put("/api/me/profile", json=body("ada"), headers=portal_headers(named))

    assert created.status_code == 200, created.text
    assert (created.json()["role"], created.json()["signup_granted"]) == (role, True)
    me = await client.get("/api/me", headers=portal_headers(named))
    assert (me.status_code, me.json()["role"]) == (200, role)
    assert (await stored(container, email)).role is UserRole.USER
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "sent",
    [
        {key: value for key, value in body().items() if key != "policy_accepted"},
        body() | {"policy_accepted": False},
    ],
    ids=["left-out", "false"],
)
async def test_the_route_answers_a_first_save_without_the_policy_with_422_on_the_field(
    client: httpx.AsyncClient,
    container: Container,
    portal_headers: PortalHeaders,
    sent: dict[str, object],
) -> None:
    response = await client.put("/api/me/profile", json=sent, headers=portal_headers(NEO))

    assert error_of(response, 422, "VALIDATION_ERROR") == {"field": "policy_accepted"}
    assert response.json()["message"] == POLICY_NOT_ACCEPTED_MESSAGE
    assert await written(container) == NOTHING
    still = await client.get("/api/me", headers=portal_headers(NEO))
    assert error_of(still, 403, "PROFILE_REQUIRED")["email"] == NEO.email


@pytest.mark.parametrize("not_a_boolean", [1, 0, "true", "yes", None], ids=repr)
async def test_the_policy_field_takes_a_boolean_and_nothing_that_merely_counts_as_one(
    client: httpx.AsyncClient, container: Container, as_user: ActAs, not_a_boolean: object
) -> None:
    as_user(NEO)

    response = await client.put("/api/me/profile", json=body() | {"policy_accepted": not_a_boolean})

    details = error_of(response, 422, "VALIDATION_ERROR")
    assert [(error["loc"], error["type"]) for error in details["errors"]] == [
        (["body", "policy_accepted"], "bool_type")
    ]
    assert await written(container) == NOTHING


async def test_the_route_answers_a_taken_name_with_409_and_the_field(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    portal_headers: PortalHeaders,
) -> None:
    await factory.user(username="the_one")
    before = await written(container)

    response = await client.put(
        "/api/me/profile", json=body("the_one"), headers=portal_headers(NEO)
    )

    assert error_of(response, 409, "USERNAME_TAKEN") == {"field": "username"}
    assert response.json()["message"] == "This username is taken."
    assert await written(container) == before


@pytest.mark.parametrize(
    ("sent", "field", "message"),
    [
        (body("official"), "username", RESERVED_USERNAME_MESSAGE),
        (
            body("x" * (MAX_LENGTH + 1)),
            "username",
            f"A username has {MIN_LENGTH} to {MAX_LENGTH} characters.",
        ),
        (body("neo", (BACHELOR[0], MASTER[1])), "degree_programme", UNKNOWN_PROGRAMME_MESSAGE),
    ],
    ids=["a-reserved-name", "a-name-too-long", "a-programme-of-another-level"],
)
async def test_the_route_answers_what_the_service_refuses_with_422_on_the_field(
    client: httpx.AsyncClient,
    container: Container,
    as_user: ActAs,
    sent: dict[str, object],
    field: str,
    message: str,
) -> None:
    as_user(NEO)

    response = await client.put("/api/me/profile", json=sent)

    assert error_of(response, 422, "VALIDATION_ERROR") == {"field": field}
    assert response.json()["message"] == message
    assert await written(container) == NOTHING


async def test_a_username_is_lower_case_and_a_capital_letter_never_reaches_the_service(
    client: httpx.AsyncClient, container: Container, as_user: ActAs
) -> None:
    """The request model refuses it, so a name has one spelling by the time the
    service locks it."""
    as_user(NEO)

    response = await client.put("/api/me/profile", json=body("Neo"))

    details = error_of(response, 422, "VALIDATION_ERROR")
    assert [error["loc"] for error in details["errors"]] == [["body", "username"]]
    assert await written(container) == NOTHING
