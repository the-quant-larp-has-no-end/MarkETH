"""The rules of the market services that need no database (SPEC §4, §5, §6, A.5).

* `check_liquidity_settings`: the api and the relay do not start on tiers and a budget
  cap on which a market could not be opened or not be traded.
* `check_market_dates`: the four date rules a proposal and its approval are held to.
* `refuse_nul`: a text Postgres cannot store is refused before the database is asked.
* `ALLOWED_TRANSITIONS`: the state machine of SPEC §6.

Every limit a result depends on is read from the test settings or asked of
`app.pricing.lmsr`.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Final

import pytest
from hypothesis import example, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from app.container import Container
from app.core.config import Settings
from app.core.enums import STORED_MARKET_STATUSES, MarketStatus, MarketTier
from app.core.errors import ValidationFailedError
from app.core.units import MICRO
from app.pricing import lmsr
from app.schemas.admin.markets import (
    ApproveMarketRequest,
    CloseMarketRequest,
    HaltMarketRequest,
    RejectMarketRequest,
    ResolveMarketRequest,
    ResumeMarketRequest,
    VoidMarketRequest,
)
from app.schemas.common import RequestModel
from app.schemas.markets import ProposeMarketRequest
from app.services.market_lifecycle import ALLOWED_TRANSITIONS, check_liquidity_settings
from app.services.markets import UNSTORABLE_TEXT_MESSAGE, check_market_dates, refuse_nul
from app.services.public_config import FIXED_TIERS
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.settings import load_base_settings

BASE: Final[Settings] = load_base_settings()
PRECISION: Final[int] = BASE.pricing.decimal_precision
LIFETIME: Final[timedelta] = BASE.risk.max_market_lifetime
DELAY: Final[timedelta] = BASE.lifecycle.max_resolution_delay

TIER_VARIABLES: Final[dict[MarketTier, str]] = {
    MarketTier.TIGHT: "LMSR_TIER_TIGHT_B",
    MarketTier.DEFAULT: "LMSR_TIER_DEFAULT_B",
    MarketTier.MEDIUM: "LMSR_TIER_MEDIUM_B",
}
"""The variable of `.env` each fixed tier takes its b from."""

TIER_FIELDS: Final[dict[MarketTier, str]] = {
    MarketTier.TIGHT: "tier_tight_b_micro",
    MarketTier.DEFAULT: "tier_default_b_micro",
    MarketTier.MEDIUM: "tier_medium_b_micro",
}

LARGEST_B: Final[int] = max(BASE.pricing.tier_b_micro(tier) for tier in FIXED_TIERS)
"""The b of the widest fixed tier of the test settings, in µCR."""

SMALLEST_BUDGET_FOR_THE_TIERS: Final[int] = lmsr.max_loss_micro(LARGEST_B, precision=PRECISION)
"""The least MAX_MARKET_BUDGET every fixed tier of the test settings fits, in µCR."""

SECOND: Final[timedelta] = timedelta(seconds=1)
TICK: Final[timedelta] = timedelta(microseconds=1)
"""The smallest step an instant can take."""


def with_liquidity(
    *,
    max_market_budget_micro: int | None = None,
    decimal_precision: int | None = None,
    **tier_b_micro: int,
) -> Settings:
    """The test settings with another budget cap, other digits or another b for a tier
    (`tier_tight_b_micro`, `tier_default_b_micro`, `tier_medium_b_micro`)."""
    pricing: dict[str, Any] = dict(tier_b_micro)
    if decimal_precision is not None:
        pricing["decimal_precision"] = decimal_precision
    risk = (
        {}
        if max_market_budget_micro is None
        else {"max_market_budget_micro": max_market_budget_micro}
    )
    return BASE.model_copy(
        update={
            "pricing": BASE.pricing.model_copy(update=pricing),
            "risk": BASE.risk.model_copy(update=risk),
        }
    )


def is_quotable_budget(budget_micro: int, precision: int) -> bool:
    """Whether a CUSTOM market of this budget gets a b the quote functions take."""
    b_micro = lmsr.b_from_budget_micro(budget_micro, precision=precision)
    return lmsr.is_quotable(b_micro, precision=precision)


# --- check_liquidity_settings ----------------------------------------------------------


def test_the_settings_of_the_example_can_open_and_trade_markets() -> None:
    check_liquidity_settings(BASE)


def test_a_budget_cap_whose_b_the_digits_cannot_price_is_refused() -> None:
    """At 50 digits the line runs between 69,314 CR and 69,315 CR (see
    tests/unit/pricing/test_lmsr_bounds.py). A cap beyond it would let an admin open a
    market nobody can quote or trade."""
    covered = with_liquidity(max_market_budget_micro=69_314 * MICRO, decimal_precision=50)
    beyond = with_liquidity(max_market_budget_micro=69_315 * MICRO, decimal_precision=50)
    assert is_quotable_budget(69_314 * MICRO, 50)
    assert not is_quotable_budget(69_315 * MICRO, 50)

    check_liquidity_settings(covered)
    with pytest.raises(ValueError, match="MAX_MARKET_BUDGET = 69315 CR") as refused:
        check_liquidity_settings(beyond)
    assert "PRICING_DECIMAL_PRECISION = 50" in str(refused.value)


def test_more_digits_make_room_for_a_larger_budget_cap() -> None:
    beyond_at_50 = 69_315 * MICRO

    with pytest.raises(ValueError, match="MAX_MARKET_BUDGET"):
        check_liquidity_settings(
            with_liquidity(max_market_budget_micro=beyond_at_50, decimal_precision=50)
        )
    check_liquidity_settings(
        with_liquidity(max_market_budget_micro=beyond_at_50, decimal_precision=51)
    )


def test_a_budget_cap_of_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="MAX_MARKET_BUDGET must be above zero"):
        check_liquidity_settings(with_liquidity(max_market_budget_micro=0))


@pytest.mark.parametrize("tier", FIXED_TIERS)
def test_a_tier_whose_loss_bound_exceeds_the_budget_cap_is_refused(tier: MarketTier) -> None:
    """Approval gives a fixed tier the budget ceil(b · ln 2). Above MAX_MARKET_BUDGET
    it would refuse the tier every time."""
    b_micro = 3 * LARGEST_B
    loss_micro = lmsr.max_loss_micro(b_micro, precision=PRECISION)
    wide = {TIER_FIELDS[tier]: b_micro}

    check_liquidity_settings(with_liquidity(max_market_budget_micro=loss_micro, **wide))
    with pytest.raises(ValueError, match=TIER_VARIABLES[tier]) as refused:
        check_liquidity_settings(with_liquidity(max_market_budget_micro=loss_micro - 1, **wide))
    assert "MAX_MARKET_BUDGET" in str(refused.value)
    assert "could never be approved" in str(refused.value)


@pytest.mark.parametrize("tier", FIXED_TIERS)
def test_a_tier_whose_b_the_digits_cannot_price_is_refused(tier: MarketTier) -> None:
    unquotable = 10**11 + 1
    assert not lmsr.is_quotable(unquotable, precision=50)

    settings = with_liquidity(decimal_precision=50, **{TIER_FIELDS[tier]: unquotable})

    with pytest.raises(ValueError, match=f"{TIER_VARIABLES[tier]} = .* cannot be priced"):
        check_liquidity_settings(settings)


@pytest.mark.parametrize("tier", FIXED_TIERS)
def test_a_tier_without_liquidity_is_refused(tier: MarketTier) -> None:
    """`.env` may say 0, and the database refuses an approved market whose b is 0."""
    settings = with_liquidity(**{TIER_FIELDS[tier]: 0})

    with pytest.raises(ValueError, match=f"{TIER_VARIABLES[tier]} must be above zero"):
        check_liquidity_settings(settings)


def test_neither_the_api_nor_the_relay_is_built_on_settings_that_fail_the_check() -> None:
    """Both start from `Container.build`, which builds the lifecycle service."""
    broken = with_liquidity(max_market_budget_micro=SMALLEST_BUDGET_FOR_THE_TIERS - 1)

    with pytest.raises(ValueError, match="could never be approved"):
        Container.build(broken, clock=FrozenClock())


@hypothesis_settings(max_examples=300, deadline=None)
@given(
    max_budget_micro=st.one_of(
        st.integers(min_value=0, max_value=10**12),
        st.integers(min_value=-(10**6), max_value=10**6).map(
            lambda offset: max(0, SMALLEST_BUDGET_FOR_THE_TIERS + offset)
        ),
        st.integers(min_value=69_000 * MICRO, max_value=70_000 * MICRO),
    ),
    tier_b_micro=st.lists(
        st.one_of(
            st.integers(min_value=0, max_value=3), st.integers(min_value=1, max_value=10**12)
        ),
        min_size=len(FIXED_TIERS),
        max_size=len(FIXED_TIERS),
    ),
    precision=st.integers(min_value=50, max_value=53),
)
@example(max_budget_micro=BASE.risk.max_market_budget_micro, tier_b_micro=[1, 1, 1], precision=50)
def test_settings_pass_exactly_when_every_market_they_allow_can_be_opened_and_traded(
    max_budget_micro: int, tier_b_micro: list[int], precision: int
) -> None:
    """The check says yes exactly when the largest CUSTOM market is quotable and each
    fixed tier is quotable and fits the budget cap. When it says yes, no budget up to
    the cap gives a b the quote functions refuse: approval's own guard never fires."""
    settings = with_liquidity(
        max_market_budget_micro=max_budget_micro,
        decimal_precision=precision,
        **dict(zip(TIER_FIELDS.values(), tier_b_micro, strict=True)),
    )
    workable = (
        max_budget_micro > 0
        and is_quotable_budget(max_budget_micro, precision)
        and all(
            b_micro > 0
            and lmsr.is_quotable(b_micro, precision=precision)
            and lmsr.max_loss_micro(b_micro, precision=precision) <= max_budget_micro
            for b_micro in tier_b_micro
        )
    )

    if not workable:
        with pytest.raises(ValueError, match=r"MAX_MARKET_BUDGET|LMSR_TIER_"):
            check_liquidity_settings(settings)
        return

    check_liquidity_settings(settings)
    for budget_micro in {1, max_budget_micro // 3 + 1, max_budget_micro}:
        assert is_quotable_budget(budget_micro, precision)


# --- check_market_dates ----------------------------------------------------------------


def refused_field(now: datetime, close_at: datetime, resolve_by: datetime) -> str | None:
    """The field `check_market_dates` refuses these dates for; None when it takes them."""
    try:
        check_market_dates(BASE, now=now, close_at=close_at, resolve_by=resolve_by)
    except ValidationFailedError as refused:
        details = refused.details
    else:
        return None
    assert set(details) == {"field"}
    field: str = details["field"]
    return field


@pytest.mark.parametrize(
    ("closes_in", "resolves_after", "field"),
    [
        (TICK, timedelta(0), None),
        (LIFETIME, DELAY, None),
        (timedelta(0), timedelta(0), "close_at"),
        (-SECOND, DELAY, "close_at"),
        (LIFETIME + TICK, timedelta(0), "close_at"),
        (LIFETIME, -TICK, "resolve_by"),
        (TICK, DELAY + TICK, "resolve_by"),
        (-SECOND, DELAY + SECOND, "close_at"),
        (LIFETIME + SECOND, -SECOND, "close_at"),
    ],
    ids=[
        "closes-the-next-instant-and-resolves-then",
        "at-both-limits",
        "closes-now",
        "closes-in-the-past",
        "open-a-moment-too-long",
        "resolves-a-moment-before-the-close",
        "resolves-a-moment-too-late",
        "both-wrong-the-close-is-named",
        "too-long-and-resolved-early-the-close-is-named",
    ],
)
def test_each_date_rule_at_its_edge_names_the_field_it_is_about(
    closes_in: timedelta, resolves_after: timedelta, field: str | None
) -> None:
    close_at = TEST_NOW + closes_in

    assert refused_field(TEST_NOW, close_at, close_at + resolves_after) == field


@hypothesis_settings(max_examples=500, deadline=None)
@given(
    now=st.timedeltas(min_value=-timedelta(days=365), max_value=timedelta(days=4 * 365)).map(
        lambda since: TEST_NOW + since
    ),
    closes_in=st.timedeltas(min_value=-2 * LIFETIME, max_value=2 * LIFETIME),
    resolves_after=st.timedeltas(min_value=-2 * DELAY, max_value=2 * DELAY),
    offset_hours=st.integers(min_value=-12, max_value=14),
)
def test_dates_are_taken_exactly_when_the_four_rules_hold(
    now: datetime, closes_in: timedelta, resolves_after: timedelta, offset_hours: int
) -> None:
    """Trading closes after now and within MAX_MARKET_LIFETIME; the market is decided
    no earlier than that and within MAX_RESOLUTION_DELAY_DAYS of it. The close is
    judged first. An instant is the same instant in whatever offset it is written."""
    elsewhere = timezone(timedelta(hours=offset_hours))
    close_at = now + closes_in
    resolve_by = close_at + resolves_after

    if not timedelta(0) < closes_in <= LIFETIME:
        expected = "close_at"
    elif not timedelta(0) <= resolves_after <= DELAY:
        expected = "resolve_by"
    else:
        expected = None

    assert refused_field(now, close_at, resolve_by) == expected
    assert refused_field(now, close_at.astimezone(elsewhere), resolve_by) == expected
    assert refused_field(now.astimezone(elsewhere), close_at, resolve_by) == expected


def test_a_refused_date_is_answered_in_the_words_of_the_form_with_the_limit_it_ran_into() -> None:
    """A client shows the message under the field, so it has to say which rule it was."""

    def message(closes_in: timedelta, resolves_after: timedelta) -> str:
        close_at = TEST_NOW + closes_in
        with pytest.raises(ValidationFailedError) as refused:
            check_market_dates(
                BASE, now=TEST_NOW, close_at=close_at, resolve_by=close_at + resolves_after
            )
        assert refused.value.status_code == 422
        return refused.value.message

    lifetime_days = BASE.risk.max_market_lifetime_days
    delay_days = BASE.lifecycle.max_resolution_delay_days
    messages = [
        message(-SECOND, timedelta(0)),
        message(LIFETIME + SECOND, timedelta(0)),
        message(SECOND, -TICK),
        message(SECOND, DELAY + SECOND),
    ]

    assert len(set(messages)) == len(messages)
    assert "in the future" in messages[0]
    assert f"at most {lifetime_days} days" in messages[1]
    assert "before trading closes" in messages[2]
    assert f"within {delay_days} days" in messages[3]


def test_a_limit_of_one_day_is_written_as_one_day() -> None:
    one_day = BASE.model_copy(
        update={
            "risk": BASE.risk.model_copy(update={"max_market_lifetime_days": 1}),
            "lifecycle": BASE.lifecycle.model_copy(update={"max_resolution_delay_days": 1}),
        }
    )
    day = timedelta(days=1)

    with pytest.raises(ValidationFailedError, match="at most 1 day\\."):
        check_market_dates(
            one_day, now=TEST_NOW, close_at=TEST_NOW + 2 * day, resolve_by=TEST_NOW + 2 * day
        )
    with pytest.raises(ValidationFailedError, match="within 1 day after"):
        check_market_dates(
            one_day, now=TEST_NOW, close_at=TEST_NOW + day, resolve_by=TEST_NOW + 3 * day
        )


# --- refuse_nul ------------------------------------------------------------------------

NUL: Final[str] = "\x00"

PROPOSAL: Final[dict[str, Any]] = {
    "question": "Will the Polymensa serve Älplermagronen on Friday?",
    "yes_definition": "It is on the menu.",
    "no_definition": "It is not on the menu.",
    "resolution_criteria": "The published menu of that day.",
    "resolution_source": "ethz.ch/menu",
    "close_at": TEST_NOW + timedelta(days=7),
    "resolve_by": TEST_NOW + timedelta(days=9),
    "policy_accepted": True,
}

TEXTS_OF_A_PROPOSAL: Final[tuple[str, ...]] = (
    "question",
    "yes_definition",
    "no_definition",
    "resolution_criteria",
    "resolution_source",
)


def test_a_request_without_a_nul_is_left_alone() -> None:
    for request in (
        ProposeMarketRequest(**PROPOSAL),
        ApproveMarketRequest(tier=MarketTier.TIGHT),
        ApproveMarketRequest(tier=MarketTier.TIGHT, question="Will it snow in Zurich in May?"),
        ResumeMarketRequest(),
        CloseMarketRequest(note="Decided early: the menu is out."),
    ):
        refuse_nul(request)


@pytest.mark.parametrize("field", TEXTS_OF_A_PROPOSAL)
def test_a_nul_in_any_text_of_a_proposal_is_refused_and_the_field_is_named(field: str) -> None:
    request = ProposeMarketRequest(**(PROPOSAL | {field: f"Will it{NUL} rain tomorrow?"}))

    with pytest.raises(ValidationFailedError) as refused:
        refuse_nul(request)

    assert refused.value.status_code == 422
    assert refused.value.details == {"field": field}
    assert refused.value.message == UNSTORABLE_TEXT_MESSAGE


@pytest.mark.parametrize(
    ("request_with_a_nul", "field"),
    [
        (ApproveMarketRequest(tier=MarketTier.TIGHT, no_definition=f"No{NUL}pe."), "no_definition"),
        (RejectMarketRequest(reason=f"A named person{NUL}"), "reason"),
        (HaltMarketRequest(reason=f"{NUL}The source is in doubt."), "reason"),
        (ResumeMarketRequest(note=f"All {NUL} clear."), "note"),
        (CloseMarketRequest(note=f"Decided early{NUL}"), "note"),
        (ResolveMarketRequest(outcome="YES", evidence=f"The menu{NUL} shows it."), "evidence"),
        (VoidMarketRequest(reason=f"The source went{NUL} away."), "reason"),
    ],
    ids=["approve", "reject", "halt", "resume", "close", "resolve", "void"],
)
def test_a_nul_in_what_an_admin_writes_is_refused_and_the_field_is_named(
    request_with_a_nul: RequestModel, field: str
) -> None:
    with pytest.raises(ValidationFailedError) as refused:
        refuse_nul(request_with_a_nul)

    assert refused.value.details == {"field": field}


def test_the_first_text_with_a_nul_is_the_one_named() -> None:
    request = ProposeMarketRequest(
        **(PROPOSAL | {"no_definition": f"No{NUL}pe.", "resolution_source": f"ethz.ch{NUL}"})
    )

    with pytest.raises(ValidationFailedError) as refused:
        refuse_nul(request)

    assert refused.value.details == {"field": "no_definition"}


# --- ALLOWED_TRANSITIONS ---------------------------------------------------------------

SPEC_SECTION_6: Final[frozenset[tuple[MarketStatus, MarketStatus]]] = frozenset(
    {
        (MarketStatus.PROPOSED, MarketStatus.OPEN),
        (MarketStatus.PROPOSED, MarketStatus.REJECTED),
        (MarketStatus.OPEN, MarketStatus.HALTED),
        (MarketStatus.HALTED, MarketStatus.OPEN),
        (MarketStatus.OPEN, MarketStatus.CLOSED),
        (MarketStatus.HALTED, MarketStatus.CLOSED),
        (MarketStatus.CLOSED, MarketStatus.RESOLVED),
        (MarketStatus.CLOSED, MarketStatus.VOID),
    }
)
"""The arrows of the state diagram of SPEC §6."""


def test_the_state_machine_is_the_diagram_of_spec_section_6() -> None:
    arrows = {
        (status, target) for status, targets in ALLOWED_TRANSITIONS.items() for target in targets
    }

    assert set(ALLOWED_TRANSITIONS) == set(STORED_MARKET_STATUSES)
    assert arrows == SPEC_SECTION_6


def test_the_state_machine_knows_no_expired_market() -> None:
    """EXPIRED is what a proposal past its deadline is reported as. No row says it, so
    it is no state of the machine: nothing starts from it and nothing leads to it."""
    targets = {target for reachable in ALLOWED_TRANSITIONS.values() for target in reachable}

    assert MarketStatus.EXPIRED not in ALLOWED_TRANSITIONS
    assert MarketStatus.EXPIRED not in targets


@pytest.mark.parametrize(
    "status", [MarketStatus.REJECTED, MarketStatus.RESOLVED, MarketStatus.VOID]
)
def test_a_rejected_or_settled_market_goes_nowhere(status: MarketStatus) -> None:
    assert ALLOWED_TRANSITIONS[status] == frozenset()
