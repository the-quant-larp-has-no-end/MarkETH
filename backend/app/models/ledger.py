"""The double-entry ledger: `accounts`, `journal_entries`, `postings` (SPEC §1, §7, A.8).

Every credit movement is a journal entry whose postings sum to zero. A positive posting
increases the balance of its account. What the database itself guarantees, whatever the
code above it does (the triggers are in `app.models.triggers`, the privileges in
`app.models.privileges`):

* An entry nets to zero at commit, or the transaction fails.
* `accounts.balance_micro` is the sum of the account's postings. A trigger adds every
  posting to its account; the app role can neither write the column nor open an account
  with a balance, so a balance cannot move without a posting or a posting without it.
* An entry is sealed by the transaction that created it (`created_xid`): no later
  transaction can add a posting to it. Entries and postings are append-only; the app
  role has no UPDATE or DELETE on them.

User accounts cannot go negative, and neither can a market account or the two
voucher accounts (A.8: a market account only takes in costs and pays out proceeds, and
its loss is booked to `vis:mm_pnl` when it settles). `vis:issuance` and `vis:mm_pnl` can.
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
"""The longest key of A.8, `adjustment:{uuid}:{uuid}`, has 84 characters."""

CURRENT_TRANSACTION_ID: Final[str] = "(pg_current_xact_id())::text::bigint"
"""SQL for the id of the running top-level transaction, as a BIGINT."""


class Account(Base):
    """A ledger account: one per user, one per market, and one per VIS_* kind."""

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("kind <> 'USER' OR balance_micro >= 0", name="user_balance_non_negative"),
        CheckConstraint(
            "kind <> 'MARKET' OR balance_micro >= 0", name="market_balance_non_negative"
        ),
        CheckConstraint(
            "kind NOT IN ('VIS_REDEMPTION_PENDING', 'VIS_REDEEMED') OR balance_micro >= 0",
            name="redemption_balance_non_negative",
        ),
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
    # Written by the database alone: 0 at first, then moved by the postings trigger.
    balance_micro: Mapped[int] = mapped_column(server_default=text("0"))


class JournalEntry(Base):
    """Why credits moved. `idempotency_key` is deterministic, so a replay cannot book twice.

    `created_xid` is the transaction that inserted the entry, filled in by the database.
    Only that transaction may add postings to it.
    """

    __tablename__ = "journal_entries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    kind: Mapped[JournalKind] = mapped_column(enum_column(JournalKind, "kind"))
    ref_type: Mapped[str] = mapped_column(String(REF_TYPE_MAX_LENGTH))
    ref_id: Mapped[UUID]
    idempotency_key: Mapped[str] = mapped_column(
        String(JOURNAL_IDEMPOTENCY_KEY_MAX_LENGTH), unique=True
    )
    created_at: Mapped[datetime]
    created_xid: Mapped[int] = mapped_column(server_default=text(CURRENT_TRANSACTION_ID))


class Posting(Base):
    """One leg of a journal entry. `id` is also the cursor of a user's ledger view."""

    __tablename__ = "postings"
    __table_args__ = (
        CheckConstraint("amount_micro <> 0", name="amount_non_zero"),
        Index(None, "account_id", "id"),
        # (entry_id, id): the balance check asks for "a later posting of this entry".
        Index(None, "entry_id", "id"),
    )

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    entry_id: Mapped[UUID] = mapped_column(ForeignKey("journal_entries.id"))
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"))
    amount_micro: Mapped[int]
