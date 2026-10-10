"""Notices: what a user is told once (SPEC §6, A.5).

A notice is written by the transaction that gives rise to it, so it exists exactly if
that committed. One thing gives rise to one: an admin approves a market somebody else
proposed, and the proposer is told that it is open (`NoticeKind.PROPOSAL_APPROVED`).

`GET /me` carries the notices its user has not seen (`AccountService.me`), oldest
first. The app shows each one and marks it seen; a notice that is marked stays in the
table and is never listed again.
"""

from datetime import datetime
from uuid import UUID

from app.core.clock import Clock
from app.core.enums import NoticeKind
from app.core.errors import NotFoundError
from app.core.identity import AuthenticatedUser
from app.models.market import Market
from app.models.notice import Notice
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.me import NoticeItem
from app.services import UowFactory


class NoticeService:
    def __init__(self, uow_factory: UowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def proposal_approved(
        self, uow: UnitOfWork, market: Market, approver: AuthenticatedUser, now: datetime
    ) -> None:
        """Tell the proposer of `market` that it was approved, in the approval's own
        transaction (`MarketLifecycleService.approve`).

        One `notices` row for `market.created_by`, of kind PROPOSAL_APPROVED, dated
        `now`, the instant of the approval. Nothing is written when the approver is
        the proposer: an admin who approves their own proposal knows.
        """
        if market.created_by == approver.id:
            return
        await uow.notices.add(
            Notice(
                user_id=market.created_by,
                kind=NoticeKind.PROPOSAL_APPROVED,
                market_id=market.id,
                created_at=now,
            )
        )

    async def unseen(self, uow: UnitOfWork, user_id: UUID) -> list[NoticeItem]:
        """A user's notices that are not marked seen, oldest first, in the caller's
        transaction (`notices.list_unseen`). Each carries the question of its market
        as it reads now."""
        return [
            _item(notice, question) for notice, question in await uow.notices.list_unseen(user_id)
        ]

    async def mark_seen(self, user: AuthenticatedUser, notice_id: UUID) -> NoticeItem:
        """Mark one of the caller's notices seen (`POST /me/notices/{id}/seen`).

        One transaction: `notices.mark_seen(notice_id, user.id, now)` sets `seen_at`
        on a notice of this user that has none, and on no other row. The notice is
        then read back (`notices.get_for_user`) and returned. No such notice, or one
        that is somebody else's, raises `NotFoundError` (404): the two are told
        apart by nothing.

        Marking a notice that is marked already changes nothing and answers the same:
        `seen_at` keeps the instant of the first call. Of two calls sent at once the
        second waits for the first inside the UPDATE and then finds nothing to set.
        """
        async with self._uow_factory() as uow:
            await uow.notices.mark_seen(notice_id, user.id, self._clock.now())
            found = await uow.notices.get_for_user(notice_id, user.id)
            if found is None:
                raise NotFoundError
            notice, question = found
            return _item(notice, question)


def _item(notice: Notice, question: str) -> NoticeItem:
    return NoticeItem(
        id=notice.id,
        kind=notice.kind,
        market_id=notice.market_id,
        question=question,
        created_at=notice.created_at,
    )
