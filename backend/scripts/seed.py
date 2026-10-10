"""Load demo data into the running stack (`make seed`).

    docker compose exec api python -m scripts.seed

Demo data is made the way real data is made: by the services, as the person who would do
it. Every row it leaves behind is therefore one the application could have written: a
signup grant with its journal entry, a market opened with its caps checked and its audit
row, trades with their outbox events, a settlement that pays through the ledger. Nothing
here opens a transaction or writes a row of its own, so a demo never shows a state the
application cannot reach.

What it leaves behind:

* Six demo students and a demo desk, each with a profile, an account and the signup
  grant (`ProfileService.save`).
* Ten markets, one in every state a screen has to show: open in each of the three
  tiers, one paused, one closed, one resolved, one voided, one still proposed, one
  proposal that expires and one rejected. The students propose them, the desk picks
  the tier each is opened with, the students trade them with the buy buttons of the
  trade sheet (`TRADE_STAKE_PRESETS_CR`), and one of them sells again before the
  resolution. Each approval tells the proposer with a notice, as every approval does.
* The proposal that expires is proposed like the others, with a deadline that is
  `EXPIRES_AFTER` away, and nobody decides on it. The run that makes it leaves it
  PROPOSED; from its deadline on the application reports it EXPIRED, and a later run
  finds it so.
* Three vouchers, one of them scanned at the desk.
* One batch of codes. The run that makes it prints its codes. An admin opens the ones
  nobody has redeemed again in the admin panel.

Who acts. The students are plain users. An address of `ADMIN_EMAILS` is never
registered: those belong to real people, who sign in through the portal. The admin
actions a demo needs (approve, reject, halt, close, resolve, void, the code batch, the
scan of a voucher) are done as the demo desk, one more registered demo account, to
which this script itself gives the admin role for its own calls. That role is written
nowhere: the row the seed makes for the desk says USER like those of the students, so
nobody who signs in as it gets an admin screen. The desk trades nothing, so it may
settle every market (A.4).

Refused when `APP_ENV=production`, and when one of the demo addresses is a bootstrap
admin or has a suspended account: the script acts for nobody the API would not let
act. A role somebody gave a demo account in the admin panel is left as it is.

Safe to run again. A second run finds what the first one made and makes nothing more; a
run that follows one that stopped half way goes on where that one stopped. What makes
each thing happen once:

* a person: the profile that `IdentityService` finds for the address;
* a market: its question among the proposals of its proposer, and from there its
  status, which says which step is next;
* a trade: its Idempotency-Key, which is the same in every run, so a trade sent again
  is answered with the stored one (SPEC §8);
* a voucher: a voucher for that product in its owner's wallet;
* the batch: its label among the batches.

Asking who a demo address is does what any request of that person does (A.3): if a
monthly grant or the refund of an expired voucher is owed to the account by then, the
run books it. That is the application's doing, not demo data.

Whatever a service refuses is not caught: the run stops at the first error and shows it.

This module runs inside the api container. It must not import from `tests/`, which is
not part of the image.
"""

import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid5

from app.container import Container
from app.core.config import get_settings
from app.core.enums import (
    DegreeLevel,
    MarketStatus,
    MarketTier,
    Outcome,
    TradeSide,
    UserRole,
    VoucherStatus,
)
from app.core.identity import AuthenticatedUser, Caller
from app.core.logging import configure_logging
from app.core.units import format_cr, to_cr, to_micro
from app.schemas.admin.code_batches import CreateCodeBatchRequest
from app.schemas.admin.markets import (
    ApproveMarketRequest,
    CloseMarketRequest,
    HaltMarketRequest,
    RejectMarketRequest,
    ResolveMarketRequest,
    VoidMarketRequest,
)
from app.schemas.markets import MarketDetailResponse, ProposeMarketRequest
from app.schemas.profile import SaveProfileRequest
from app.schemas.trading import TradeRequest, TradeResponse
from app.schemas.vouchers import IssueVoucherRequest

SEED_NAMESPACE: Final[UUID] = UUID("3e5c8316-fb40-4c3a-876b-927a3030923a")
"""What the Idempotency-Key of a demo trade is derived from, with the trade's place in
the plan: the same key in every run."""

RESOLVE_WITHIN: Final[timedelta] = timedelta(days=7)
"""How long after its close a demo market asks to be decided, unless
`MAX_RESOLUTION_DELAY_DAYS` allows less."""

EXPIRES_AFTER: Final[timedelta] = timedelta(seconds=10)
"""How long after its proposal the trading of the proposal the demo lets expire would
close. Short, so that the demo shows an expired proposal a moment after the run; long
enough that the proposal is still one whose trading closes in the future when the
service checks it."""

BATCH_LABEL: Final[str] = "Demo codes"
BATCH_SIZE: Final[int] = 5
BATCH_VALID_FOR: Final[timedelta] = timedelta(days=30)

STOPPED: Final[frozenset[MarketStatus]] = frozenset(
    {MarketStatus.CLOSED, MarketStatus.RESOLVED, MarketStatus.VOID}
)
"""Where a demo market ends once its trading was stopped for good."""

UNDECIDED: Final[frozenset[MarketStatus]] = frozenset({MarketStatus.PROPOSED, MarketStatus.EXPIRED})
"""Where a demo market ends that the desk never decides on: still waiting, or expired
because nobody decided in time."""


class SeedRefusedError(RuntimeError):
    """The seed does not run here. The run that raised this registered nobody and made
    no market, trade, voucher or code."""


@dataclass(frozen=True, slots=True)
class Person:
    """A demo account, as it registers."""

    mailbox: str
    """What stands before the `@` of its address; the domain is the first of
    `ALLOWED_EMAIL_DOMAINS`."""

    name: str
    username: str
    degree_level: DegreeLevel
    degree_programme: str


@dataclass(frozen=True, slots=True)
class Buy:
    """A student taps a buy button of the trade sheet."""

    student: int
    """Who, as an index into `STUDENTS`."""

    outcome: Outcome
    preset: int
    """Which button, as an index into `TRADE_STAKE_PRESETS_CR`; the last one when there
    are fewer."""


@dataclass(frozen=True, slots=True)
class SellBack:
    """A student sells the shares one of their earlier buys in the market bought."""

    student: int
    bought_in: int
    """That buy, as an index into the trades of the market."""


type Step = Buy | SellBack


@dataclass(frozen=True, slots=True)
class DemoMarket:
    """A market of the demo: what its proposal says, and how far it is taken."""

    slug: str
    """Names the market in the Idempotency-Keys of its trades. Never shown."""

    question: str
    yes_definition: str
    no_definition: str
    resolution_criteria: str
    resolution_source: str
    proposer: int
    """Who proposes it, as an index into `STUDENTS`. A proposer cannot trade their own
    market (A.4)."""

    closes_in: timedelta
    """When trading closes, counted from the proposal; `MAX_MARKET_LIFETIME` at most."""

    ends: MarketStatus
    """The status the demo leaves it in, as the application reports it. EXPIRED is a
    proposal nobody decides on: it is left PROPOSED and expires at its deadline."""

    tier: MarketTier | None = None
    """The tier the desk opens it with. None for a market the desk never approves."""

    trades: tuple[Step, ...] = ()
    remark: str | None = None
    """What the desk says with the last step: the reason of a rejection, a halt or a
    void, the note of a close, the evidence of a resolution."""

    outcome: Outcome | None = None
    """What a market that ends RESOLVED resolves to."""


@dataclass(frozen=True, slots=True)
class DemoVoucher:
    """A voucher of the demo. Its product is the one at its own place in
    `VOUCHER_PRODUCTS`: the first voucher is for the first product, and so on round."""

    student: int
    scanned: bool
    """Whether the desk scans it, which leaves it REDEEMED."""


STUDENTS: Final[tuple[Person, ...]] = (
    Person("demo.anna", "Anna Demo", "anna_demo", DegreeLevel.BACHELOR, "BSc Computer Science"),
    Person("demo.ben", "Ben Demo", "ben_demo", DegreeLevel.MASTER, "MSc Data Science"),
    Person("demo.chiara", "Chiara Demo", "chiara_demo", DegreeLevel.BACHELOR, "BSc Mathematics"),
    Person(
        "demo.dario",
        "Dario Demo",
        "dario_demo",
        DegreeLevel.MASTER,
        "MSc Robotics, Systems and Control",
    ),
    Person("demo.elif", "Elif Demo", "elif_demo", DegreeLevel.DOCTORATE, "Doctorate Physics"),
    Person(
        "demo.finn", "Finn Demo", "finn_demo", DegreeLevel.BACHELOR, "BSc Mechanical Engineering"
    ),
)
"""The demo students. In the plan below none of them sends more than five trades or
proposes more than two markets, which is within `MAX_ORDERS_PER_MINUTE` and
`MAX_PROPOSALS_PER_USER_PER_DAY` as `.example.env` sets them. Where a limit is set
tighter, the refusal of the service shows."""

DESK: Final[Person] = Person("demo.desk", "Demo Desk", "demo_desk", DegreeLevel.OTHER, "ETH staff")
"""The account this script acts as for every admin action. See "Who acts" above."""

ANNA, BEN, CHIARA, DARIO, ELIF, FINN = range(len(STUDENTS))

MARKETS: Final[tuple[DemoMarket, ...]] = (
    DemoMarket(
        slug="mensa-aelplermagronen",
        question="Will the Polymensa serve Älplermagronen on the last Friday of this semester?",
        yes_definition="Älplermagronen are on the Polymensa's lunch menu that Friday.",
        no_definition="They are not on its lunch menu that Friday, or the Polymensa is closed.",
        resolution_criteria=(
            "The lunch menu the Polymensa publishes for that Friday decides. A side dish "
            "does not count."
        ),
        resolution_source="The menu plan on ethz.ch",
        proposer=ANNA,
        tier=MarketTier.TIGHT,
        closes_in=timedelta(days=14),
        ends=MarketStatus.OPEN,
        trades=(
            Buy(BEN, Outcome.YES, preset=1),
            Buy(CHIARA, Outcome.NO, preset=0),
            Buy(DARIO, Outcome.YES, preset=0),
            Buy(ELIF, Outcome.YES, preset=1),
        ),
    ),
    DemoMarket(
        slug="qs-first-ten",
        question="Will ETH Zurich be among the first ten of the next QS World University Rankings?",
        yes_definition="ETH Zurich is ranked 10th or better.",
        no_definition="ETH Zurich is ranked 11th or lower, or is not ranked.",
        resolution_criteria=(
            "The overall ranking as QS publishes it decides, not a ranking by subject."
        ),
        resolution_source="topuniversities.com",
        proposer=BEN,
        tier=MarketTier.DEFAULT,
        closes_in=timedelta(days=60),
        ends=MarketStatus.OPEN,
        trades=(
            Buy(ANNA, Outcome.YES, preset=1),
            Buy(CHIARA, Outcome.YES, preset=0),
            Buy(FINN, Outcome.NO, preset=1),
            Buy(DARIO, Outcome.NO, preset=0),
        ),
    ),
    DemoMarket(
        slug="polybahn-standstill",
        question="Will the Polybahn stand still for a whole day before the lecture period ends?",
        yes_definition="On at least one day of operation the Polybahn carries no passengers.",
        no_definition="It runs on every day of operation until the lecture period ends.",
        resolution_criteria=(
            "A day counts when the operator announces the standstill for the whole day. "
            "Planned maintenance counts too."
        ),
        resolution_source="The service notices of the operator",
        proposer=CHIARA,
        tier=MarketTier.MEDIUM,
        closes_in=timedelta(days=30),
        ends=MarketStatus.OPEN,
        trades=(
            Buy(ANNA, Outcome.NO, preset=0),
            Buy(ELIF, Outcome.YES, preset=0),
            Buy(FINN, Outcome.YES, preset=1),
        ),
    ),
    DemoMarket(
        slug="vis-assembly",
        question="Will more than 150 members come to the next general assembly of VIS?",
        yes_definition="The minutes count more than 150 members present.",
        no_definition="The minutes count 150 members or fewer.",
        resolution_criteria=(
            "The number of members present when the assembly opens, as its minutes state it."
        ),
        resolution_source="The minutes of the assembly on vis.ethz.ch",
        proposer=DARIO,
        tier=MarketTier.DEFAULT,
        closes_in=timedelta(days=21),
        ends=MarketStatus.HALTED,
        trades=(
            Buy(BEN, Outcome.NO, preset=1),
            Buy(ELIF, Outcome.YES, preset=0),
            Buy(ANNA, Outcome.YES, preset=0),
        ),
        remark="The date of the assembly is being moved. Trading pauses until it is fixed.",
    ),
    DemoMarket(
        slug="viscon-visitors",
        question="Will more than 400 people check in at VIScon this year?",
        yes_definition="The organisers count more than 400 check-ins.",
        no_definition="The organisers count 400 check-ins or fewer.",
        resolution_criteria=(
            "The number of check-ins over both days, as the organisers announce it at the closing."
        ),
        resolution_source="The closing announcement of VIScon",
        proposer=ELIF,
        tier=MarketTier.DEFAULT,
        closes_in=timedelta(days=10),
        ends=MarketStatus.CLOSED,
        trades=(
            Buy(CHIARA, Outcome.YES, preset=1),
            Buy(DARIO, Outcome.NO, preset=1),
            Buy(FINN, Outcome.YES, preset=0),
        ),
        remark="The doors are open and the count has begun.",
    ),
    DemoMarket(
        slug="first-day-rain",
        question="Will it rain in Zurich on the first day of the lecture period?",
        yes_definition="At least 0.1 mm of precipitation is measured that day.",
        no_definition="Less than 0.1 mm of precipitation is measured that day.",
        resolution_criteria="The daily precipitation at the station Zürich / Fluntern decides.",
        resolution_source="MeteoSwiss, station Zürich / Fluntern",
        proposer=FINN,
        tier=MarketTier.TIGHT,
        closes_in=timedelta(days=7),
        ends=MarketStatus.RESOLVED,
        trades=(
            Buy(ANNA, Outcome.YES, preset=1),
            Buy(BEN, Outcome.NO, preset=1),
            Buy(DARIO, Outcome.YES, preset=0),
            Buy(CHIARA, Outcome.YES, preset=1),
            SellBack(DARIO, bought_in=2),
            Buy(ELIF, Outcome.NO, preset=0),
        ),
        remark="MeteoSwiss measured 4.2 mm at Zürich / Fluntern on that day.",
        outcome=Outcome.YES,
    ),
    DemoMarket(
        slug="cab-cafeteria",
        question="Will the cafeteria in the CAB building open again before the exam session?",
        yes_definition="It serves customers on at least one day before the session starts.",
        no_definition="It stays closed until the session starts.",
        resolution_criteria="The opening hours ETH publishes for the cafeteria decide.",
        resolution_source="The catering pages on ethz.ch",
        proposer=ANNA,
        tier=MarketTier.DEFAULT,
        closes_in=timedelta(days=14),
        ends=MarketStatus.VOID,
        trades=(
            Buy(BEN, Outcome.YES, preset=0),
            Buy(FINN, Outcome.NO, preset=0),
            Buy(CHIARA, Outcome.YES, preset=0),
        ),
        remark=(
            "ETH moved the exam session, so the deadline of this market decides nothing. "
            "Every stake is refunded."
        ),
    ),
    DemoMarket(
        slug="dome-open-day",
        question="Will the dome of the main building be open to visitors on the next open day?",
        yes_definition="Visitors can go up into the dome at some time during the open day.",
        no_definition="The dome stays closed to visitors for the whole open day.",
        resolution_criteria="The programme ETH publishes for the open day decides.",
        resolution_source="The programme of the open day on ethz.ch",
        proposer=BEN,
        closes_in=timedelta(days=45),
        ends=MarketStatus.PROPOSED,
    ),
    DemoMarket(
        slug="flatmate-exam",
        question="Will my flatmate pass the Analysis I exam this winter?",
        yes_definition="My flatmate passes the exam.",
        no_definition="My flatmate fails the exam or does not sit it.",
        resolution_criteria="The grade my flatmate tells me decides.",
        resolution_source="My flatmate",
        proposer=CHIARA,
        closes_in=timedelta(days=20),
        ends=MarketStatus.REJECTED,
        remark=(
            "A market must not be about a private person. Ask about something everybody can check."
        ),
    ),
    DemoMarket(
        slug="library-all-night",
        question="Will the main library stay open all night during the next exam session?",
        yes_definition="On at least one night of the session it does not close at all.",
        no_definition="It closes on every night of the session.",
        resolution_criteria="The opening hours the ETH Library publishes for the session decide.",
        resolution_source="library.ethz.ch",
        proposer=DARIO,
        closes_in=EXPIRES_AFTER,
        ends=MarketStatus.EXPIRED,
    ),
)
"""The demo markets, in the order they are made."""

VOUCHERS: Final[tuple[DemoVoucher, ...]] = (
    DemoVoucher(ANNA, scanned=False),
    DemoVoucher(BEN, scanned=True),
    DemoVoucher(ELIF, scanned=False),
)


async def seed(container: Container) -> None:
    """Make the demo data, or finish making it; a run that finds it all makes nothing.

    Everything goes through the services of `container`, in this order:

    1. Refuse (`SeedRefusedError`) when `APP_ENV` is production, and when an address
       of `STUDENTS` or of `DESK` is one of `ADMIN_EMAILS`. Nothing has been asked of
       any store by then.
    2. People. `IdentityService.authenticate(address, name)` says who an address is,
       as a request with the portal's headers would. All of them are asked first, and
       a demo account that is suspended is refused before anybody is registered. Then
       every address without a profile gets one through `ProfileService.save`, which
       accepts the usage policy for it: the user, the account and the signup grant in
       one transaction.
    3. Markets, each as far as `DemoMarket.ends` says: `MarketService.propose` as its
       proposer, with the usage policy accepted; then as the desk `approve`, with the
       tier of the plan, or `reject`; the trades (`TradingService.trade`, each under
       its own fixed Idempotency-Key); then `halt`, or `close`, or `close` and
       `resolve`, or `close` and `void`. The status the market has decides which step
       is next, so a market that is where it ends is left alone, and so is one
       somebody took elsewhere by hand. A market that ends PROPOSED or EXPIRED is
       proposed and then left to itself: the second of the two has a deadline a
       moment away (`EXPIRES_AFTER`) and expires there, without a write.
    4. Vouchers (`VoucherService.issue`), and the scan of one of them at the desk
       (`VoucherService.redeem`). A voucher that is there and still waits for its scan
       gets a fresh code first (`replace_code`): the one it was issued with is known
       to nobody.
    5. The batch of codes (`CodeService.create_batch`), unless a batch with
       `BATCH_LABEL` exists. The run that makes it prints its codes to stdout. A run
       that finds it prints none: opening the codes of a batch again is an admin's
       to do, in the admin panel, and every time it is done is in the audit log.

    A line is printed for everything the run did and for everything it found done.
    """
    if container.settings.app.is_production:
        raise SeedRefusedError(
            "scripts.seed makes demo accounts and demo markets. It does not run with "
            "APP_ENV=production. Nothing was written."
        )
    await _Seed(container).run()


class _Seed:
    """One run of the seed: the services it calls and the people it acts as."""

    def __init__(self, container: Container) -> None:
        self._identity = container.identity
        self._roles = container.email_policy
        self._profile = container.profile
        self._account = container.account
        self._markets = container.markets
        self._lifecycle = container.market_lifecycle
        self._trading = container.trading
        self._vouchers = container.vouchers
        self._codes = container.codes
        self._clock = container.clock
        self._settings = container.settings
        self._students: list[AuthenticatedUser] = []

    async def run(self) -> None:
        self._refuse_bootstrap_admins()
        _say("People")
        *self._students, desk = await self._people((*STUDENTS, DESK))
        # The one place where the desk becomes an admin: for the calls of this run.
        acting_admin = replace(desk, role=UserRole.ADMIN)
        _say("Markets")
        for market in MARKETS:
            await self._market(market, acting_admin)
        _say("Vouchers")
        for place, voucher in enumerate(VOUCHERS):
            await self._voucher(place, voucher, acting_admin)
        _say("Codes")
        await self._code_batch(acting_admin)

    def _address(self, person: Person) -> str:
        return f"{person.mailbox}@{self._settings.identity.allowed_email_domains[0]}"

    def _refuse_bootstrap_admins(self) -> None:
        """Step 1: no demo account is somebody `ADMIN_EMAILS` names."""
        for person in (*STUDENTS, DESK):
            address = self._address(person)
            if self._roles.is_bootstrap_admin(self._roles.identify(address)):
                raise SeedRefusedError(
                    f"{address} is in ADMIN_EMAILS. The seed registers demo accounts only "
                    "and acts for no admin the configuration names. Nothing was written."
                )

    async def _people(self, people: Sequence[Person]) -> list[AuthenticatedUser]:
        """Step 2: the registered callers behind the demo addresses, in the order given.
        Who has no profile yet is registered, once it is known that nobody is refused."""
        callers = [
            await self._identity.authenticate(self._address(person), person.name)
            for person in people
        ]
        for caller in callers:
            if caller.user is not None and not caller.user.is_active:
                raise SeedRefusedError(
                    f"The demo account {caller.identity.email} is suspended. The seed acts "
                    "for nobody the API would refuse."
                )
        return [
            await self._registered(person, caller)
            for person, caller in zip(people, callers, strict=True)
        ]

    async def _registered(self, person: Person, caller: Caller) -> AuthenticatedUser:
        """The user `caller` is, with a profile saved now if the address had none."""
        address = caller.identity.email
        if caller.user is not None:
            _say(f"  {address}  {caller.user.username}  already there")
            return caller.user
        await self._profile.save(
            caller.identity,
            SaveProfileRequest(
                username=person.username,
                degree_level=person.degree_level,
                degree_programme=person.degree_programme,
                policy_accepted=True,
            ),
        )
        # Asked again, as the next request of that person would be.
        user = (await self._identity.authenticate(address, person.name)).user
        if user is None:
            raise RuntimeError(f"{address} has no profile right after it was saved")
        _say(f"  {address}  {user.username}  registered")
        return user

    async def _market(self, plan: DemoMarket, desk: AuthenticatedUser) -> None:
        """Step 3: take one market from wherever it is to where it ends."""
        proposer = self._students[plan.proposer]
        market_id = await self._proposal_of(proposer, plan.question)
        if market_id is None:
            detail = await self._propose(proposer, plan)
        else:
            detail = await self._markets.detail(desk, market_id)
        market_id = detail.id

        if detail.status is MarketStatus.PROPOSED and plan.ends not in UNDECIDED:
            if plan.ends is MarketStatus.REJECTED:
                detail = await self._lifecycle.reject(
                    desk, market_id, RejectMarketRequest(reason=_said(plan))
                )
            else:
                if plan.tier is None:
                    raise ValueError(f"the demo market {plan.slug} opens with no tier")
                detail = await self._lifecycle.approve(
                    desk, market_id, ApproveMarketRequest(tier=plan.tier)
                )

        if detail.trading_open and detail.version < len(plan.trades):
            await self._trade(plan, market_id)
            detail = await self._markets.detail(desk, market_id)

        if plan.ends is MarketStatus.HALTED and detail.trading_open:
            detail = await self._lifecycle.halt(
                desk, market_id, HaltMarketRequest(reason=_said(plan))
            )
        if plan.ends in STOPPED and detail.status in (MarketStatus.OPEN, MarketStatus.HALTED):
            note = plan.remark if plan.ends is MarketStatus.CLOSED else None
            detail = await self._lifecycle.close(desk, market_id, CloseMarketRequest(note=note))
        if plan.ends is MarketStatus.RESOLVED and detail.status is MarketStatus.CLOSED:
            if plan.outcome is None:
                raise ValueError(f"the demo market {plan.slug} resolves to no outcome")
            detail = await self._lifecycle.resolve(
                desk, market_id, ResolveMarketRequest(outcome=plan.outcome, evidence=_said(plan))
            )
        if plan.ends is MarketStatus.VOID and detail.status is MarketStatus.CLOSED:
            detail = await self._lifecycle.void(
                desk, market_id, VoidMarketRequest(reason=_said(plan))
            )
        _say(f"  {detail.status:<9} {detail.question}")

    async def _proposal_of(self, proposer: AuthenticatedUser, question: str) -> UUID | None:
        """The id of the market with this question among the proposer's proposals."""
        for proposal in (await self._account.proposals(proposer)).items:
            if proposal.question == question:
                return proposal.id
        return None

    async def _propose(self, proposer: AuthenticatedUser, plan: DemoMarket) -> MarketDetailResponse:
        close_at = self._clock.now() + min(plan.closes_in, self._settings.risk.max_market_lifetime)
        resolve_by = close_at + min(RESOLVE_WITHIN, self._settings.lifecycle.max_resolution_delay)
        return await self._markets.propose(
            proposer,
            ProposeMarketRequest(
                question=plan.question,
                yes_definition=plan.yes_definition,
                no_definition=plan.no_definition,
                resolution_criteria=plan.resolution_criteria,
                resolution_source=plan.resolution_source,
                close_at=close_at,
                resolve_by=resolve_by,
                policy_accepted=True,
            ),
        )

    async def _trade(self, plan: DemoMarket, market_id: UUID) -> None:
        """The trades of one market, in the order of the plan. Each is sent under the
        key of its place in the plan, so one that an earlier run executed is answered
        with the stored trade and nothing is traded twice."""
        executed: list[TradeResponse] = []
        for place, step in enumerate(plan.trades):
            if isinstance(step, Buy):
                request = TradeRequest(
                    side=TradeSide.BUY, outcome=step.outcome, amount_cr=self._preset(step.preset)
                )
            else:
                bought = executed[step.bought_in]
                request = TradeRequest(
                    side=TradeSide.SELL, outcome=bought.outcome, shares=bought.shares
                )
            key = uuid5(SEED_NAMESPACE, f"{plan.slug}/{place}")
            trader = self._students[step.student]
            executed.append(await self._trading.trade(trader, market_id, key, request))

    def _preset(self, place: int) -> Decimal:
        """The amount of a buy button, in CR."""
        presets = self._settings.pricing.stake_presets_micro
        return to_cr(presets[min(place, len(presets) - 1)])

    async def _voucher(self, place: int, plan: DemoVoucher, desk: AuthenticatedUser) -> None:
        """Step 4: one voucher, and its scan when the plan has one."""
        products = self._settings.vouchers.products
        product = products[place % len(products)]
        owner = self._students[plan.student]
        wallet = (await self._vouchers.list_for_user(owner)).items
        held = next((voucher for voucher in wallet if voucher.product_id == product.id), None)
        code: str | None = None
        if held is None:
            issued = await self._vouchers.issue(owner, IssueVoucherRequest(product_id=product.id))
            code, done = issued.code, "issued"
        else:
            done = "already there"
            if plan.scanned and held.status is VoucherStatus.ACTIVE:
                code = (await self._vouchers.replace_code(owner, held.id)).code
        if plan.scanned and code is not None:
            await self._vouchers.redeem(desk, code)
            done = f"{done}, scanned at the desk"
        _say(f"  {owner.username}  {product.name}  {done}")

    async def _code_batch(self, desk: AuthenticatedUser) -> None:
        """Step 5: the batch, and its codes on stdout."""
        if any(batch.label == BATCH_LABEL for batch in (await self._codes.list_batches()).items):
            _say(
                f'  The batch "{BATCH_LABEL}" is already there. An admin opens the codes '
                "nobody has redeemed in the admin panel."
            )
            return
        created = await self._codes.create_batch(
            desk,
            CreateCodeBatchRequest(
                label=BATCH_LABEL,
                count=BATCH_SIZE,
                expires_at=self._clock.now() + BATCH_VALID_FOR,
                one_per_user=True,
            ),
        )
        batch = created.batch
        _say(
            f'  The batch "{batch.label}": {batch.count} codes worth '
            f"{format_cr(to_micro(batch.value_cr))} CR each, one per user, valid until "
            f"{batch.expires_at:%Y-%m-%d}:"
        )
        for code in created.codes:
            _say(f"    {code.code}  {code.redeem_url}")


def _said(plan: DemoMarket) -> str:
    """What the desk says with the last step of a market that needs it said."""
    if plan.remark is None:
        raise ValueError(f"the demo market {plan.slug} ends {plan.ends} and says nothing")
    return plan.remark


def _say(line: str) -> None:
    print(line, flush=True)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.app.log_level)
    container = Container.build(settings)
    try:
        await seed(container)
    except SeedRefusedError as refused:
        sys.exit(str(refused))
    finally:
        await container.stop()


if __name__ == "__main__":
    asyncio.run(main())
