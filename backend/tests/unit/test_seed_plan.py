"""The plan of the demo seed, checked without a database (`scripts/seed.py`).

The seed makes its data by calling the services as the people of its plan, and a service
refuses what breaks a rule. Whether the plan keeps the rules can be read off the plan
itself, on the settings of `.example.env`: who may register, who may trade what, what
fits a limit, what a request model takes. `tests/integration/test_seed.py` runs it.
"""

import ast
import re
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Final
from uuid import uuid5

import pytest

from app.core.config import Settings
from app.core.degrees import is_programme
from app.core.enums import MarketStatus
from app.core.identity import USERNAME_PATTERN, portal_address
from app.schemas.admin.code_batches import CreateCodeBatchRequest
from app.schemas.admin.markets import (
    ApproveMarketRequest,
    CloseMarketRequest,
    HaltMarketRequest,
    RejectMarketRequest,
    ResolveMarketRequest,
    VoidMarketRequest,
)
from app.schemas.markets import ProposeMarketRequest
from app.schemas.profile import SaveProfileRequest
from app.services.email_policy import EmailPolicy
from app.services.public_config import FIXED_TIERS
from scripts import seed
from scripts.seed import (
    BATCH_LABEL,
    BATCH_SIZE,
    BATCH_VALID_FOR,
    DESK,
    MARKETS,
    SEED_NAMESPACE,
    STUDENTS,
    VOUCHERS,
    Buy,
    DemoMarket,
    Person,
    SellBack,
)
from tests.support.clock import TEST_NOW
from tests.support.settings import EXAMPLE_ENV_FILE

PEOPLE: Final[tuple[Person, ...]] = (*STUDENTS, DESK)

STORES_AND_WHAT_REACHES_THEM: Final[frozenset[str]] = frozenset(
    {
        "sqlalchemy",
        "asyncpg",
        "redis",
        "cassandra",
        "app.models",
        "app.repositories",
        "app.db",
        "app.integrations",
    }
)
"""What a module imports to read or write a store itself."""

BEHIND_THE_SERVICES: Final[frozenset[str]] = frozenset(
    {"uow_factory", "database", "session", "redis", "scylla", "ledger", "quote_history"}
)
"""Attributes of a `Container` through which a caller would get past the services."""

every_market = pytest.mark.parametrize("market", MARKETS, ids=lambda market: market.slug)


def address_of(person: Person, settings: Settings) -> str:
    return f"{person.mailbox}@{settings.identity.allowed_email_domains[0]}"


def buys_of(market: DemoMarket) -> list[Buy]:
    return [step for step in market.trades if isinstance(step, Buy)]


def preset_micro(buy: Buy, settings: Settings) -> int:
    """What the buy button of `buy` costs: the seed takes the last button when the
    settings have fewer."""
    presets = settings.pricing.stake_presets_micro
    return presets[min(buy.preset, len(presets) - 1)]


# --- People ----------------------------------------------------------------------------


@pytest.mark.parametrize("person", PEOPLE, ids=lambda person: person.username)
def test_every_demo_account_registers_with_a_profile_the_service_accepts(
    person: Person, base_settings: Settings
) -> None:
    identity = base_settings.identity
    request = SaveProfileRequest(
        username=person.username,
        degree_level=person.degree_level,
        degree_programme=person.degree_programme,
        policy_accepted=True,
    )

    assert re.fullmatch(USERNAME_PATTERN, request.username)
    assert request.username == request.username.lower()
    assert identity.username_min_length <= len(request.username) <= identity.username_max_length
    assert request.username not in identity.reserved_usernames
    assert is_programme(request.degree_level, request.degree_programme)
    assert person.name


def test_no_two_demo_accounts_share_an_address_or_a_username() -> None:
    assert len({person.mailbox for person in PEOPLE}) == len(PEOPLE)
    assert len({person.username for person in PEOPLE}) == len(PEOPLE)


@pytest.mark.parametrize("person", PEOPLE, ids=lambda person: person.username)
def test_every_demo_address_is_one_the_portal_could_send_and_no_bootstrap_admin(
    person: Person, base_settings: Settings
) -> None:
    """Neither among the bootstrap admins of the tests nor among those of
    `.example.env`: a demo account is nobody's real one."""
    address = address_of(person, base_settings)

    assert portal_address(address, base_settings.identity.allowed_email_domains) == address
    for settings in (base_settings, Settings.load(EXAMPLE_ENV_FILE)):
        roles = EmailPolicy(settings)
        assert roles.identify(address) == address
        assert not roles.is_bootstrap_admin(address)


def test_the_names_the_plan_calls_the_students_by_are_theirs() -> None:
    named = {
        seed.ANNA: "anna_demo",
        seed.BEN: "ben_demo",
        seed.CHIARA: "chiara_demo",
        seed.DARIO: "dario_demo",
        seed.ELIF: "elif_demo",
        seed.FINN: "finn_demo",
    }

    assert sorted(named) == list(range(len(STUDENTS)))
    for place, username in named.items():
        assert STUDENTS[place].username == username
    assert DESK not in STUDENTS


# --- Markets ---------------------------------------------------------------------------


def test_the_plan_has_a_market_for_every_state_a_screen_shows() -> None:
    """Open in each of the three tiers, and one each paused, closed, resolved, voided,
    proposed and rejected."""
    ends = Counter(market.ends for market in MARKETS)

    assert ends == {
        MarketStatus.OPEN: len(FIXED_TIERS),
        MarketStatus.HALTED: 1,
        MarketStatus.CLOSED: 1,
        MarketStatus.RESOLVED: 1,
        MarketStatus.VOID: 1,
        MarketStatus.PROPOSED: 1,
        MarketStatus.REJECTED: 1,
    }
    open_tiers = {market.tier for market in MARKETS if market.ends is MarketStatus.OPEN}
    assert open_tiers == set(FIXED_TIERS)


def test_no_two_demo_markets_share_a_question_or_a_slug() -> None:
    """The question is how a run finds a market again, the slug what the keys of its
    trades are made of."""
    assert len({market.question for market in MARKETS}) == len(MARKETS)
    assert len({market.slug for market in MARKETS}) == len(MARKETS)


@every_market
def test_the_wording_of_every_demo_market_is_a_proposal_the_api_takes(
    market: DemoMarket, base_settings: Settings
) -> None:
    close_at = TEST_NOW + timedelta(days=market.closes_in_days)
    proposal = ProposeMarketRequest(
        question=market.question,
        yes_definition=market.yes_definition,
        no_definition=market.no_definition,
        resolution_criteria=market.resolution_criteria,
        resolution_source=market.resolution_source,
        close_at=close_at,
        resolve_by=close_at + seed.RESOLVE_WITHIN,
        policy_accepted=True,
    )

    assert proposal.question == market.question
    assert 0 < market.closes_in_days <= base_settings.risk.max_market_lifetime_days
    assert base_settings.lifecycle.max_resolution_delay >= seed.RESOLVE_WITHIN
    assert 0 <= market.proposer < len(STUDENTS)


@every_market
def test_a_demo_market_names_a_tier_exactly_if_the_desk_opens_it(market: DemoMarket) -> None:
    """A proposal asks for no liquidity. The desk picks the tier when it approves, so
    the plan names one for a market that opens and none for one that never does."""
    opens = market.ends not in (MarketStatus.PROPOSED, MarketStatus.REJECTED)

    assert (market.tier is not None) == opens
    if market.tier is not None:
        assert market.tier in FIXED_TIERS
        assert ApproveMarketRequest(tier=market.tier).budget_cr is None


@every_market
def test_every_demo_market_says_what_its_last_step_needs_said(market: DemoMarket) -> None:
    """A reason, a note or the evidence, in the request model of that step, and an
    outcome exactly where a market resolves."""
    assert (market.outcome is not None) == (market.ends is MarketStatus.RESOLVED)
    match market.ends:
        case MarketStatus.OPEN | MarketStatus.PROPOSED:
            assert market.remark is None
        case MarketStatus.REJECTED:
            assert market.remark is not None
            RejectMarketRequest(reason=market.remark)
        case MarketStatus.HALTED:
            assert market.remark is not None
            HaltMarketRequest(reason=market.remark)
        case MarketStatus.CLOSED:
            assert market.remark is not None
            CloseMarketRequest(note=market.remark)
        case MarketStatus.RESOLVED:
            assert market.remark is not None
            assert market.outcome is not None
            ResolveMarketRequest(outcome=market.outcome, evidence=market.remark)
        case MarketStatus.VOID:
            assert market.remark is not None
            VoidMarketRequest(reason=market.remark)


@every_market
def test_only_a_market_that_opens_is_traded(market: DemoMarket) -> None:
    opens = market.ends not in (MarketStatus.PROPOSED, MarketStatus.REJECTED)

    assert bool(market.trades) == opens


# --- Trades ----------------------------------------------------------------------------


@every_market
def test_nobody_trades_a_market_they_proposed(market: DemoMarket) -> None:
    """A.4: the trading service would refuse the proposer."""
    assert market.proposer not in {step.student for step in market.trades}


@every_market
def test_every_sale_gives_back_the_shares_of_an_earlier_buy_of_the_same_student(
    market: DemoMarket,
) -> None:
    sold: set[int] = set()
    for place, step in enumerate(market.trades):
        assert 0 <= step.student < len(STUDENTS)
        if isinstance(step, SellBack):
            bought = market.trades[step.bought_in]
            assert step.bought_in < place
            assert isinstance(bought, Buy)
            assert bought.student == step.student
            assert step.bought_in not in sold, "the same shares are not sold twice"
            sold.add(step.bought_in)


@every_market
def test_what_a_student_puts_into_one_market_stays_within_the_stake_limit(
    market: DemoMarket, base_settings: Settings
) -> None:
    """Counted without what a sale brings back, so the plan holds whatever the prices."""
    staked: Counter[int] = Counter()
    for buy in buys_of(market):
        assert 0 <= buy.preset < len(base_settings.pricing.stake_presets_micro)
        staked[buy.student] += preset_micro(buy, base_settings)

    assert max(staked.values(), default=0) <= base_settings.pricing.max_stake_per_user_market_micro


def test_the_signup_grant_pays_for_everything_a_student_buys(base_settings: Settings) -> None:
    """Every buy and the voucher, again without counting on a sale or a payout."""
    spent: Counter[int] = Counter()
    for market in MARKETS:
        for buy in buys_of(market):
            spent[buy.student] += preset_micro(buy, base_settings)
    products = base_settings.vouchers.products
    for place, voucher in enumerate(VOUCHERS):
        spent[voucher.student] += products[place % len(products)].price_micro

    assert max(spent.values()) <= base_settings.grants.signup_grant_micro


def test_no_student_is_asked_for_more_than_a_limit_allows(base_settings: Settings) -> None:
    """Half the trade requests a minute allows at most, so that a run that follows one
    that stopped can send them all again; and no more proposals than a day allows."""
    requests = Counter(step.student for market in MARKETS for step in market.trades)
    proposals = Counter(market.proposer for market in MARKETS)

    assert max(requests.values()) <= base_settings.risk.max_orders_per_minute // 2
    assert max(proposals.values()) <= base_settings.lifecycle.max_proposals_per_user_per_day


def test_every_demo_trade_has_a_key_of_its_own() -> None:
    keys = [
        uuid5(SEED_NAMESPACE, f"{market.slug}/{place}")
        for market in MARKETS
        for place in range(len(market.trades))
    ]

    assert len(set(keys)) == len(keys) > 0


# --- Vouchers and codes ------------------------------------------------------------------


def test_the_demo_vouchers_are_for_different_students_and_one_is_scanned() -> None:
    owners = [voucher.student for voucher in VOUCHERS]

    assert len(set(owners)) == len(owners)
    assert all(0 <= owner < len(STUDENTS) for owner in owners)
    assert [voucher.scanned for voucher in VOUCHERS].count(True) == 1


def test_the_demo_batch_is_one_the_api_takes_and_within_the_monthly_cap(
    base_settings: Settings,
) -> None:
    codes = base_settings.codes
    batch = CreateCodeBatchRequest(
        label=BATCH_LABEL,
        count=BATCH_SIZE,
        expires_at=TEST_NOW + BATCH_VALID_FOR,
        one_per_user=True,
    )

    assert batch.value_cr is None, "the seed leaves the value to CODE_DEFAULT_VALUE_CR"
    assert batch.count * codes.default_value_micro <= codes.max_issuance_per_month_micro


# --- Through the services only -----------------------------------------------------------


def seed_source() -> ast.Module:
    source = Path(seed.__file__)
    return ast.parse(source.read_text(encoding="utf-8"), filename=str(source))


def test_the_seed_imports_nothing_that_reads_or_writes_a_store() -> None:
    """No model, no repository, no driver: whatever it leaves behind, a service wrote."""
    imported: set[str] = set()
    for node in ast.walk(seed_source()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    reaching_a_store = {
        module
        for module in imported
        for forbidden in STORES_AND_WHAT_REACHES_THEM
        if module == forbidden or module.startswith(f"{forbidden}.")
    }
    assert reaching_a_store == set()


def test_the_seed_takes_nothing_from_the_container_but_services_the_clock_and_settings() -> None:
    """It never asks for a unit of work, a connection or the ledger. `stop` is what
    `main` ends the container with that it built."""
    taken = {
        node.attr
        for node in ast.walk(seed_source())
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "container"
    }

    assert taken == {
        "settings",
        "clock",
        "identity",
        "email_policy",
        "profile",
        "account",
        "markets",
        "market_lifecycle",
        "trading",
        "vouchers",
        "codes",
        "stop",
    }
    assert taken.isdisjoint(BEHIND_THE_SERVICES)
    named = {node.attr for node in ast.walk(seed_source()) if isinstance(node, ast.Attribute)}
    assert named.isdisjoint(BEHIND_THE_SERVICES)
