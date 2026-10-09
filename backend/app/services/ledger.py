"""The only writer of the ledger (SPEC §1, §7, Appendix A.8, golden rule 2).

Every credit movement in MarkETH is one call to `LedgerService.post`: a journal entry
whose postings sum to zero, inserted together with the matching change of each
account's balance. Nothing else inserts into `journal_entries` or `postings`, and
nothing else changes `accounts.balance_micro`.

This module also fixes the vocabulary every caller shares, so that five services cannot
spell it five ways: what a journal entry refers to (`LedgerRef`) and its deterministic
idempotency key (the `*_key` functions, one per row of Appendix A.8).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.core.enums import SIGNUP_GRANT_PERIOD, AccountKind, JournalKind
from app.core.errors import InsufficientBalanceError
from app.core.units import format_cr
from app.models.ledger import JournalEntry, Posting
from app.repositories.unit_of_work import UnitOfWork


class LedgerRef(StrEnum):
    """`journal_entries.ref_type`: the table whose row `ref_id` is the id of."""

    VOUCHER_GRANT = "voucher_grant"
    """SIGNUP_GRANT and MONTHLY_GRANT: `voucher_grants.id`."""

    VOUCHER_CODE = "voucher_code"
    """CODE_REDEMPTION: `voucher_codes.id`."""

    TRADE = "trade"
    """TRADE_BUY and TRADE_SELL: `trades.id`."""

    MARKET = "market"
    """MARKET_RESOLUTION and MARKET_VOID: `markets.id`."""

    COFFEE_TICKET = "coffee_ticket"
    """COFFEE_TICKET_ISSUE, _REDEEM and _REFUND: `coffee_tickets.id`."""


def signup_grant_key(user_id: UUID) -> str:
    return f"grant:{user_id}:{SIGNUP_GRANT_PERIOD}"


def monthly_grant_key(user_id: UUID, period: str) -> str:
    """`period` is `YYYY-MM`, as `app.core.clock.month_period` formats it."""
    return f"grant:{user_id}:{period}"


def code_redemption_key(code_id: UUID) -> str:
    return f"code:{code_id}"


def trade_key(trade_id: UUID) -> str:
    return f"trade:{trade_id}"


def resolution_key(market_id: UUID) -> str:
    return f"resolution:{market_id}"


def void_key(market_id: UUID) -> str:
    return f"void:{market_id}"


def ticket_issue_key(ticket_id: UUID) -> str:
    return f"ticket:{ticket_id}:issue"


def ticket_redeem_key(ticket_id: UUID) -> str:
    return f"ticket:{ticket_id}:redeem"


def ticket_refund_key(ticket_id: UUID) -> str:
    return f"ticket:{ticket_id}:refund"


@dataclass(frozen=True, slots=True)
class PostingLine:
    """One leg of an entry. A positive amount increases the account's balance."""

    account_id: UUID
    amount_micro: int


class InvalidEntryError(ValueError):
    """The lines given to `LedgerService.post` do not make a journal entry.

    A programming error, never a user's: it is raised before anything is written.
    """


class LedgerService:
    async def post(
        self,
        uow: UnitOfWork,
        *,
        kind: JournalKind,
        ref_type: str,
        ref_id: UUID,
        idempotency_key: str,
        lines: Sequence[PostingLine],
        at: datetime,
    ) -> JournalEntry:
        """Book one journal entry inside the caller's transaction and return it.

        1. Lines on the same account are merged into one posting and lines that come to
           zero are dropped (A.8: zero-amount postings are omitted).
        2. An entry with no posting left, or whose postings do not sum to zero, raises
           `InvalidEntryError` before the database is touched. A caller whose entry can
           be all zeros (settling a market nobody traded) must not call `post`.
        3. The accounts are locked in ascending id order. An unknown account raises
           `InvalidEntryError`. A user account that would go below zero raises
           `InsufficientBalanceError` (409) with `details.balance_cr` and
           `details.required_cr`; nothing has been written at that point.
        4. The entry and its postings are inserted, and each balance is updated, again
           in ascending account id order.

        A caller that needs a user's account locks it before it locks anything else of
        that user's and before calling this (lock order: market row, user account, the
        rest). The system and market accounts are locked here, last: look them up
        without a lock (`accounts.get_system_account(kind)`,
        `accounts.get_market_account(market_id)`) and pass their ids.

        `idempotency_key` is one of this module's `*_key` values. Using one a second
        time violates `uq_journal_entries_idempotency_key`: the INSERT raises
        `IntegrityError`, which ends the caller's transaction (it must leave the
        `async with` block). `ref_type` is a `LedgerRef`.
        """
        deltas = _net_by_account(lines)
        if not deltas:
            raise InvalidEntryError(f"{kind} entry {idempotency_key!r} has no postings")
        if (imbalance := sum(deltas.values())) != 0:
            raise InvalidEntryError(
                f"{kind} entry {idempotency_key!r} is off by {imbalance} µCR; postings must "
                "sum to zero"
            )

        accounts = await uow.accounts.lock_many(list(deltas))
        if len(accounts) != len(deltas):
            unknown = deltas.keys() - {account.id for account in accounts}
            raise InvalidEntryError(f"{kind} entry {idempotency_key!r} posts to {unknown}")
        for account in accounts:
            delta_micro = deltas[account.id]
            if account.kind is AccountKind.USER and account.balance_micro + delta_micro < 0:
                raise InsufficientBalanceError(
                    details={
                        "balance_cr": format_cr(account.balance_micro),
                        "required_cr": format_cr(-delta_micro),
                    }
                )

        entry = await uow.journal.add_entry(
            JournalEntry(
                kind=kind,
                ref_type=str(ref_type),
                ref_id=ref_id,
                idempotency_key=idempotency_key,
                created_at=at,
            )
        )
        await uow.journal.add_postings(
            [
                Posting(entry_id=entry.id, account_id=account_id, amount_micro=delta_micro)
                for account_id, delta_micro in deltas.items()
            ]
        )
        for account_id, delta_micro in deltas.items():
            await uow.accounts.add_to_balance(account_id, delta_micro)
        return entry


def _net_by_account(lines: Sequence[PostingLine]) -> dict[UUID, int]:
    """Net amount per account, without the accounts that net to zero, in ascending id order."""
    totals: dict[UUID, int] = {}
    for line in lines:
        totals[line.account_id] = totals.get(line.account_id, 0) + line.amount_micro
    return {account_id: totals[account_id] for account_id in sorted(totals) if totals[account_id]}
