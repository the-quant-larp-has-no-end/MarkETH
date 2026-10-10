"""`LedgerService`: the only writer of the ledger (SPEC §1, §12 "Accounting", A.8).

Against a real Postgres, so the service is checked together with what backs it up: the
balance trigger, the unique idempotency key and the user-balance CHECK.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Final
from uuid import UUID, uuid7

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.container import Container
from app.core.enums import AccountKind, JournalKind, Outcome
from app.core.errors import InsufficientBalanceError
from app.core.units import MICRO
from app.models import Account, JournalEntry, Posting
from app.repositories.unit_of_work import UnitOfWork
from app.services.ledger import (
    InvalidEntryError,
    LedgerRef,
    LedgerService,
    PostingLine,
    code_redemption_key,
    monthly_grant_key,
    resolution_key,
    signup_grant_key,
    trade_key,
    void_key,
    voucher_issue_key,
    voucher_redeem_key,
    voucher_refund_key,
)
from tests.support.clock import TEST_NOW
from tests.support.factories import Factory

pytestmark = pytest.mark.integration

type UowFactory = Callable[[], UnitOfWork]

CR: Final[int] = MICRO
NOW: Final[datetime] = TEST_NOW


@pytest.fixture
def ledger(container: Container) -> LedgerService:
    return container.ledger


async def account_id(uow_factory: UowFactory, kind: AccountKind) -> UUID:
    async with uow_factory() as uow:
        return (await uow.accounts.get_system_account(kind)).id


async def balance(uow_factory: UowFactory, account: UUID) -> int:
    async with uow_factory() as uow:
        row = await uow.accounts.get(account)
        assert row is not None
        return row.balance_micro


async def count_rows(uow_factory: UowFactory, model: type[JournalEntry] | type[Posting]) -> int:
    async with uow_factory() as uow:
        return (await uow.session.execute(select(func.count()).select_from(model))).scalar_one()


async def post(
    ledger: LedgerService,
    uow_factory: UowFactory,
    lines: list[PostingLine],
    *,
    key: str = "test:entry",
    kind: JournalKind = JournalKind.CODE_REDEMPTION,
) -> JournalEntry:
    async with uow_factory() as uow:
        return await ledger.post(
            uow,
            kind=kind,
            ref_type=LedgerRef.CODE,
            ref_id=uuid7(),
            idempotency_key=key,
            lines=lines,
            at=NOW,
        )


async def test_a_balanced_entry_moves_the_balances_and_all_accounts_sum_to_zero(
    ledger: LedgerService, uow_factory: UowFactory, factory: Factory
) -> None:
    user = await factory.user()
    issuance = await account_id(uow_factory, AccountKind.VIS_ISSUANCE)
    async with uow_factory() as uow:
        user_account = await uow.accounts.get_user_account(user.id, for_update=True)
        assert user_account is not None
        entry = await ledger.post(
            uow,
            kind=JournalKind.CODE_REDEMPTION,
            ref_type=LedgerRef.CODE,
            ref_id=uuid7(),
            idempotency_key="code:first",
            lines=[PostingLine(issuance, -3 * CR), PostingLine(user_account.id, 3 * CR)],
            at=NOW,
        )
        # The loaded copy follows the UPDATE, so a service can report the new balance.
        assert user_account.balance_micro == 3 * CR

    assert entry.kind is JournalKind.CODE_REDEMPTION
    assert entry.ref_type == "code"
    assert entry.created_at == NOW
    assert await factory.balance_micro(user) == 3 * CR
    assert await balance(uow_factory, issuance) == -3 * CR
    assert await factory.accounts_total_micro() == 0

    async with uow_factory() as uow:
        postings = (
            await uow.session.scalars(select(Posting).where(Posting.entry_id == entry.id))
        ).all()
    assert sorted(posting.amount_micro for posting in postings) == [-3 * CR, 3 * CR]
    assert sum(posting.amount_micro for posting in postings) == 0


async def test_an_entry_may_have_many_legs(
    ledger: LedgerService, uow_factory: UowFactory, factory: Factory
) -> None:
    """A resolution pays several winners from one market account in one entry (A.8)."""
    alice = await factory.user(balance_micro=CR)
    bob = await factory.user(balance_micro=CR)
    market = await factory.market()
    await factory.position(alice, market, Outcome.YES, shares_micro=CR, cost_micro=CR)
    await factory.position(bob, market, Outcome.YES, shares_micro=CR, cost_micro=CR)
    pnl = await account_id(uow_factory, AccountKind.VIS_MM_PNL)

    async with uow_factory() as uow:
        market_account = await uow.accounts.get_market_account(market.id)
        accounts = [await uow.accounts.get_user_account(u.id) for u in (alice, bob)]
        assert market_account is not None
        assert all(accounts)
        pot = market_account.balance_micro
        await ledger.post(
            uow,
            kind=JournalKind.MARKET_RESOLUTION,
            ref_type=LedgerRef.MARKET,
            ref_id=market.id,
            idempotency_key=resolution_key(market.id),
            lines=[
                *(PostingLine(account.id, 600_000) for account in accounts if account),
                PostingLine(market_account.id, -pot),
                PostingLine(pnl, pot - 1_200_000),
            ],
            at=NOW,
        )

    assert pot == 2 * CR
    assert await factory.balance_micro(alice) == 600_000
    assert await factory.balance_micro(bob) == 600_000
    assert await balance(uow_factory, pnl) == 800_000
    assert await factory.accounts_total_micro() == 0


async def test_an_unbalanced_entry_is_refused_before_the_database(
    ledger: LedgerService, uow_factory: UowFactory, factory: Factory
) -> None:
    user = await factory.user()
    issuance = await account_id(uow_factory, AccountKind.VIS_ISSUANCE)
    async with uow_factory() as uow:
        user_account = await uow.accounts.get_user_account(user.id)
        assert user_account is not None
        with pytest.raises(InvalidEntryError, match="off by 1 µCR"):
            await ledger.post(
                uow,
                kind=JournalKind.CODE_REDEMPTION,
                ref_type=LedgerRef.CODE,
                ref_id=uuid7(),
                idempotency_key="code:unbalanced",
                lines=[PostingLine(issuance, -CR), PostingLine(user_account.id, CR + 1)],
                at=NOW,
            )
        # Nothing was sent to Postgres, so the transaction is still usable.
        assert await uow.accounts.total_balance() == 0

    assert await count_rows(uow_factory, JournalEntry) == 0
    assert await count_rows(uow_factory, Posting) == 0
    assert await factory.balance_micro(user) == 0


@pytest.mark.parametrize("zero_lines", [0, 1, 2], ids=["no-lines", "one-zero", "two-zeros"])
async def test_an_entry_without_a_posting_is_refused_before_the_database(
    ledger: LedgerService, uow_factory: UowFactory, zero_lines: int
) -> None:
    issuance = await account_id(uow_factory, AccountKind.VIS_ISSUANCE)
    with pytest.raises(InvalidEntryError, match="no postings"):
        await post(ledger, uow_factory, [PostingLine(issuance, 0)] * zero_lines)
    assert await count_rows(uow_factory, JournalEntry) == 0


async def test_lines_that_cancel_out_leave_no_entry(
    ledger: LedgerService, uow_factory: UowFactory
) -> None:
    issuance = await account_id(uow_factory, AccountKind.VIS_ISSUANCE)
    with pytest.raises(InvalidEntryError, match="no postings"):
        await post(ledger, uow_factory, [PostingLine(issuance, CR), PostingLine(issuance, -CR)])


async def test_lines_on_the_same_account_are_merged_into_one_posting(
    ledger: LedgerService, uow_factory: UowFactory, factory: Factory
) -> None:
    user = await factory.user()
    issuance = await account_id(uow_factory, AccountKind.VIS_ISSUANCE)
    async with uow_factory() as uow:
        user_account = await uow.accounts.get_user_account(user.id, for_update=True)
        assert user_account is not None
        entry = await ledger.post(
            uow,
            kind=JournalKind.CODE_REDEMPTION,
            ref_type=LedgerRef.CODE,
            ref_id=uuid7(),
            idempotency_key="code:merged",
            lines=[
                PostingLine(user_account.id, CR),
                PostingLine(issuance, -CR),
                PostingLine(user_account.id, 2 * CR),
                PostingLine(issuance, -2 * CR),
            ],
            at=NOW,
        )
        postings = (
            await uow.session.scalars(select(Posting).where(Posting.entry_id == entry.id))
        ).all()

    assert {posting.account_id: posting.amount_micro for posting in postings} == {
        user_account.id: 3 * CR,
        issuance: -3 * CR,
    }
    assert await factory.balance_micro(user) == 3 * CR


async def test_a_zero_line_is_dropped_and_the_rest_is_booked(
    ledger: LedgerService, uow_factory: UowFactory, factory: Factory
) -> None:
    """A.8: zero-amount postings are omitted, e.g. a market maker that broke even."""
    user = await factory.user()
    issuance = await account_id(uow_factory, AccountKind.VIS_ISSUANCE)
    pnl = await account_id(uow_factory, AccountKind.VIS_MM_PNL)
    async with uow_factory() as uow:
        user_account = await uow.accounts.get_user_account(user.id, for_update=True)
        assert user_account is not None
        entry = await ledger.post(
            uow,
            kind=JournalKind.MARKET_RESOLUTION,
            ref_type=LedgerRef.MARKET,
            ref_id=uuid7(),
            idempotency_key="resolution:zero-line",
            lines=[
                PostingLine(issuance, -CR),
                PostingLine(user_account.id, CR),
                PostingLine(pnl, 0),
            ],
            at=NOW,
        )
        accounts_posted = (
            await uow.session.scalars(
                select(Posting.account_id).where(Posting.entry_id == entry.id)
            )
        ).all()

    assert set(accounts_posted) == {issuance, user_account.id}
    assert await balance(uow_factory, pnl) == 0


async def test_a_repeated_idempotency_key_is_rejected_and_books_nothing(
    ledger: LedgerService, uow_factory: UowFactory, factory: Factory
) -> None:
    user = await factory.user()
    issuance = await account_id(uow_factory, AccountKind.VIS_ISSUANCE)
    async with uow_factory() as uow:
        user_account = await uow.accounts.get_user_account(user.id)
        assert user_account is not None
    lines = [PostingLine(issuance, -CR), PostingLine(user_account.id, CR)]

    await post(ledger, uow_factory, lines, key="code:once")
    with pytest.raises(IntegrityError, match="uq_journal_entries_idempotency_key"):
        await post(ledger, uow_factory, lines, key="code:once")

    assert await factory.balance_micro(user) == CR
    assert await count_rows(uow_factory, JournalEntry) == 1
    assert await count_rows(uow_factory, Posting) == 2
    assert await factory.accounts_total_micro() == 0


async def test_a_user_balance_cannot_go_below_zero(
    ledger: LedgerService, uow_factory: UowFactory, factory: Factory
) -> None:
    user = await factory.user(balance_micro=CR)
    pending = await account_id(uow_factory, AccountKind.VIS_REDEMPTION_PENDING)
    async with uow_factory() as uow:
        user_account = await uow.accounts.get_user_account(user.id, for_update=True)
        assert user_account is not None
        with pytest.raises(InsufficientBalanceError) as refused:
            await ledger.post(
                uow,
                kind=JournalKind.VOUCHER_ISSUE,
                ref_type=LedgerRef.VOUCHER,
                ref_id=uuid7(),
                idempotency_key="voucher:too-expensive",
                lines=[PostingLine(user_account.id, -CR - 1), PostingLine(pending, CR + 1)],
                at=NOW,
            )

    assert refused.value.details == {"balance_cr": "1", "required_cr": "1.000001"}
    assert await factory.balance_micro(user) == CR
    assert await balance(uow_factory, pending) == 0
    assert await count_rows(uow_factory, JournalEntry) == 1, "only the factory's grant"


async def test_a_user_may_spend_their_whole_balance(
    ledger: LedgerService, uow_factory: UowFactory, factory: Factory
) -> None:
    user = await factory.user(balance_micro=CR)
    pending = await account_id(uow_factory, AccountKind.VIS_REDEMPTION_PENDING)
    async with uow_factory() as uow:
        user_account = await uow.accounts.get_user_account(user.id, for_update=True)
        assert user_account is not None
        await ledger.post(
            uow,
            kind=JournalKind.VOUCHER_ISSUE,
            ref_type=LedgerRef.VOUCHER,
            ref_id=uuid7(),
            idempotency_key="voucher:all-in",
            lines=[PostingLine(user_account.id, -CR), PostingLine(pending, CR)],
            at=NOW,
        )
    assert await factory.balance_micro(user) == 0
    assert await balance(uow_factory, pending) == CR


async def test_system_accounts_may_go_negative(
    ledger: LedgerService, uow_factory: UowFactory
) -> None:
    """`vis:issuance` is the source of every credit and `vis:mm_pnl` may lose (SPEC §7)."""
    issuance = await account_id(uow_factory, AccountKind.VIS_ISSUANCE)
    pnl = await account_id(uow_factory, AccountKind.VIS_MM_PNL)
    await post(ledger, uow_factory, [PostingLine(pnl, -7 * CR), PostingLine(issuance, 7 * CR)])
    assert await balance(uow_factory, pnl) == -7 * CR


async def test_an_unknown_account_is_refused_before_anything_is_written(
    ledger: LedgerService, uow_factory: UowFactory
) -> None:
    issuance = await account_id(uow_factory, AccountKind.VIS_ISSUANCE)
    nobody = uuid7()
    with pytest.raises(InvalidEntryError, match=str(nobody)):
        await post(ledger, uow_factory, [PostingLine(issuance, -CR), PostingLine(nobody, CR)])
    assert await count_rows(uow_factory, JournalEntry) == 0
    assert await balance(uow_factory, issuance) == 0


async def test_an_entry_rolls_back_with_its_transaction(
    ledger: LedgerService, uow_factory: UowFactory, factory: Factory
) -> None:
    user = await factory.user()
    issuance = await account_id(uow_factory, AccountKind.VIS_ISSUANCE)

    class Abort(Exception):
        pass

    async def book_then_fail() -> None:
        async with uow_factory() as uow:
            user_account = await uow.accounts.get_user_account(user.id, for_update=True)
            assert user_account is not None
            await ledger.post(
                uow,
                kind=JournalKind.CODE_REDEMPTION,
                ref_type=LedgerRef.CODE,
                ref_id=uuid7(),
                idempotency_key="code:rolled-back",
                lines=[PostingLine(issuance, -CR), PostingLine(user_account.id, CR)],
                at=NOW,
            )
            raise Abort

    with pytest.raises(Abort):
        await book_then_fail()

    assert await factory.balance_micro(user) == 0
    assert await count_rows(uow_factory, JournalEntry) == 0
    assert await count_rows(uow_factory, Posting) == 0


async def test_balances_always_equal_the_sum_of_their_postings(
    ledger: LedgerService, uow_factory: UowFactory, factory: Factory
) -> None:
    user = await factory.user(balance_micro=5 * CR)
    market = await factory.market()
    await factory.position(user, market, Outcome.YES, shares_micro=CR)
    await factory.position(user, market, Outcome.NO, shares_micro=CR // 2)

    posted = (
        select(Posting.account_id, func.sum(Posting.amount_micro).label("posted"))
        .group_by(Posting.account_id)
        .subquery()
    )
    async with uow_factory() as uow:
        rows = await uow.session.execute(
            select(Account.balance_micro, func.coalesce(posted.c.posted, 0)).outerjoin(
                posted, posted.c.account_id == Account.id
            )
        )
        pairs = [(row[0], int(row[1])) for row in rows]

    assert len(pairs) >= 6, "four system accounts, a user and a market"
    assert all(stored == summed for stored, summed in pairs)
    assert await factory.accounts_total_micro() == 0


def test_idempotency_keys_are_those_of_appendix_a8() -> None:
    ident = UUID("0199c1d4-7a55-7f0e-9a53-3f1f0c7f7a10")
    assert signup_grant_key(ident) == f"grant:{ident}:SIGNUP"
    assert monthly_grant_key(ident, "2026-11") == f"grant:{ident}:2026-11"
    assert code_redemption_key(ident) == f"code:{ident}"
    assert trade_key(ident) == f"trade:{ident}"
    assert resolution_key(ident) == f"resolution:{ident}"
    assert void_key(ident) == f"void:{ident}"
    assert voucher_issue_key(ident) == f"voucher:{ident}:issue"
    assert voucher_redeem_key(ident) == f"voucher:{ident}:redeem"
    assert voucher_refund_key(ident) == f"voucher:{ident}:refund"
