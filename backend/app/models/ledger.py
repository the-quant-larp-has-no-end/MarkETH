"""The double-entry ledger: `accounts`, `journal_entries`, `postings` (SPEC §1, §7, A.8).

Every credit movement is a journal entry whose postings sum to zero. A positive posting
increases the balance of its account. Entries and postings are append-only: the app role
has no UPDATE or DELETE on them, and a deferred constraint trigger (created by the
baseline migration) rejects an entry that does not net to zero at commit.

`accounts.balance_micro` is the running sum of an account's postings, kept by
`LedgerService`. User accounts cannot go negative; `vis:issuance` and `vis:mm_pnl` can,
and a market account can go down to minus its budget.
"""

from datetime import datetime
from typing import Final
from uuid import UUID, uuid7

from sqlalchemy import CheckConstraint, ForeignKey, Identity, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AccountKind, JournalKind
from app.db.base import Base, enum_column

REF_TYPE_MAX_LENGTH: Final[int] = 32
JOURNAL_IDEMPOTENCY_KEY_MAX_LENGTH: Final[int] = 128
"""The longest key of A.8, `ticket:{uuid}:redeem`, has 50 characters."""


class Account(Base):
    """A ledger account: one per user, one per market, and one per VIS_* kind."""

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("kind <> 'USER' OR balance_micro >= 0", name="user_balance_non_negative"),
        CheckConstraint("(kind = 'USER') = (owner_id IS NOT NULL)", name="owner_iff_user"),
        CheckConstraint("(kind = 'MARKET') = (market_id IS NOT NULL)", name="market_iff_market"),
        Index(
            "uq_accounts_owner_id",
            "owner_id",
            unique=True,
            postgresql_where=text("kind = 'USER'"),
        ),
        Index(
            "uq_accounts_market_id",
            "market_id",
            unique=True,
            postgresql_where=text("kind = 'MARKET'"),
        ),
        Index(
            "uq_accounts_system_kind",
            "kind",
            unique=True,
            postgresql_where=text("kind NOT IN ('USER', 'MARKET')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    kind: Mapped[AccountKind] = mapped_column(enum_column(AccountKind, "kind"))
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    market_id: Mapped[UUID | None] = mapped_column(ForeignKey("markets.id"))
    balance_micro: Mapped[int] = mapped_column(default=0)


class JournalEntry(Base):
    """Why credits moved. `idempotency_key` is deterministic, so a replay cannot book twice."""

    __tablename__ = "journal_entries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    kind: Mapped[JournalKind] = mapped_column(enum_column(JournalKind, "kind"))
    ref_type: Mapped[str] = mapped_column(String(REF_TYPE_MAX_LENGTH))
    ref_id: Mapped[UUID]
    idempotency_key: Mapped[str] = mapped_column(
        String(JOURNAL_IDEMPOTENCY_KEY_MAX_LENGTH), unique=True
    )
    created_at: Mapped[datetime]


class Posting(Base):
    """One leg of a journal entry. `id` is also the cursor of a user's ledger view."""

    __tablename__ = "postings"
    __table_args__ = (
        CheckConstraint("amount_micro <> 0", name="amount_non_zero"),
        Index(None, "account_id", "id"),
    )

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    entry_id: Mapped[UUID] = mapped_column(ForeignKey("journal_entries.id"), index=True)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"))
    amount_micro: Mapped[int]
