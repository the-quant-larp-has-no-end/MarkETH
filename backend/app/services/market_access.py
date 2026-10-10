"""Who reaches a market (SPEC §6).

A market is for everybody or for the members of one degree programme
(`markets.audience_programme`). This is the one place that turns the market id of a
request into a market row for a viewer, and the one place that says who may see a
market and who is inside it. No service has a check of its own.

The words. A **member** of a market is a caller whose `degree_programme` equals its
`audience_programme`, character for character. **In the audience** is everybody for a
public market, and the admins and the members for a private one. **Inside** a market
that was opened is an admin, its proposer, or a caller in its audience. A **holder** has
a `positions` row in the market, whatever its shares. A **proposal** is a market stored
PROPOSED or REJECTED.

    The market is               may see it                        is inside it
    a proposal                  admins and its proposer           nobody
    public, opened              everybody                         everybody
    private, opened             admins, members, its proposer,    admins, members,
                                holders                           its proposer

A holder outside the audience keeps the page of the market, its chart and their own
record, and nothing other people put there.

Every refusal of a loader is the default `NotFoundError`: the answer an id no market
has gets, so a caller cannot tell a market they may not see from one that does not
exist. Membership is read with the request (`AuthenticatedUser.degree_programme`) and
kept nowhere.
"""

from collections.abc import Collection, Iterable
from typing import Final
from uuid import UUID

from app.core.errors import ForbiddenError, NotFoundError
from app.core.identity import AuthenticatedUser
from app.models.market import Market
from app.repositories.unit_of_work import UnitOfWork
from app.services.market_view import HIDDEN_STATUSES

NOT_IN_AUDIENCE_MESSAGE: Final[str] = "This market is for {programme} only, so you cannot trade it."
"""Message of the `ForbiddenError` that refuses a trade or a quote to a viewer who sees
a private market from outside its audience. `programme` is the market's label."""


class MarketAccess:
    @staticmethod
    def in_audience(viewer: AuthenticatedUser, audience_programme: str | None) -> bool:
        """Whether a market with this audience is for `viewer`: everybody for a public
        one, the admins and the members for a private one."""
        return (
            audience_programme is None
            or viewer.is_admin
            or viewer.degree_programme == audience_programme
        )

    @staticmethod
    def may_see(viewer: AuthenticatedUser, market: Market, *, took_part: bool) -> bool:
        """Whether `viewer` may know of `market`. `took_part` says whether the viewer
        holds a position row in it."""
        if viewer.is_admin or market.created_by == viewer.id:
            return True
        if market.status in HIDDEN_STATUSES:
            return False
        return MarketAccess.in_audience(viewer, market.audience_programme) or took_part

    @staticmethod
    def is_inside(viewer: AuthenticatedUser, market: Market) -> bool:
        """Whether `viewer` reaches what people put on `market`: its questions, its
        chat, its live stream and its pushed events. Nobody is inside a proposal, which
        has none of them."""
        if market.status in HIDDEN_STATUSES:
            return False
        return MarketAccess.may_see(viewer, market, took_part=False)

    async def get_visible(
        self,
        uow: UnitOfWork,
        viewer: AuthenticatedUser,
        market_id: UUID,
        *,
        for_update: bool = False,
    ) -> Market:
        """The market, for whoever may see it; a proposal for an admin and its proposer.

        `for_update=True` takes the market lock of SPEC §8 with the read. The viewer's
        position is asked for only where the answer depends on it: a private market
        that was opened and that the viewer is neither an admin, the proposer nor a
        member of.
        """
        return await self._admitted(uow, viewer, await self._read(uow, market_id, for_update))

    async def get_opened(
        self,
        uow: UnitOfWork,
        viewer: AuthenticatedUser,
        market_id: UUID,
        *,
        for_update: bool = False,
    ) -> Market:
        """As `get_visible`, for what exists only once a market was opened: a proposal
        is `NotFoundError` for everybody, its proposer and the admins included."""
        market = await self._read(uow, market_id, for_update)
        if market.status in HIDDEN_STATUSES:
            raise NotFoundError
        return await self._admitted(uow, viewer, market)

    async def get_inside(
        self, uow: UnitOfWork, viewer: AuthenticatedUser, market_id: UUID
    ) -> Market:
        """The market, for whoever is inside it. It asks for no position: a holder
        outside the audience is refused like anybody else."""
        market = await self._read(uow, market_id, for_update=False)
        if not self.is_inside(viewer, market):
            raise NotFoundError
        return market

    async def visible_among(
        self,
        uow: UnitOfWork,
        viewer: AuthenticatedUser,
        markets: Iterable[Market],
        *,
        took_part_in: Collection[UUID] | None = None,
    ) -> list[Market]:
        """Of `markets`, the ones `viewer` may see, in the order given.

        `took_part_in` are the ids of markets the caller of this function knows the
        viewer holds a position row in. Without it the positions are asked once, and
        only for the private, opened markets the viewer does not see otherwise: a
        collection of public markets and the viewer's own costs no query.
        """
        markets = list(markets)
        seen = {market.id for market in markets if self.may_see(viewer, market, took_part=False)}
        open_to_holders = [
            market.id
            for market in markets
            if market.id not in seen and market.status not in HIDDEN_STATUSES
        ]
        if open_to_holders:
            held = (
                await uow.positions.market_ids_of_user(viewer.id, among=open_to_holders)
                if took_part_in is None
                else took_part_in
            )
            seen.update(market_id for market_id in open_to_holders if market_id in held)
        return [market for market in markets if market.id in seen]

    def inside_among(self, viewer: AuthenticatedUser, markets: Iterable[Market]) -> list[Market]:
        """Of `markets`, the ones `viewer` is inside of, in the order given. No query."""
        return [market for market in markets if self.is_inside(viewer, market)]

    def require_audience(self, viewer: AuthenticatedUser, market: Market) -> None:
        """`ForbiddenError` unless `viewer` is in the audience of `market`: whoever sees
        a private market as its proposer or as a holder alone does not trade it."""
        if not self.in_audience(viewer, market.audience_programme):
            raise ForbiddenError(
                NOT_IN_AUDIENCE_MESSAGE.format(programme=market.audience_programme)
            )

    @staticmethod
    async def _read(uow: UnitOfWork, market_id: UUID, for_update: bool) -> Market:
        market = await (
            uow.markets.get_for_update(market_id) if for_update else uow.markets.get(market_id)
        )
        if market is None:
            raise NotFoundError
        return market

    async def _admitted(self, uow: UnitOfWork, viewer: AuthenticatedUser, market: Market) -> Market:
        if self.may_see(viewer, market, took_part=False):
            return market
        # What is left is a proposal of somebody else, which nobody else sees, or a
        # private market the viewer is outside of, which its holders see.
        if market.status in HIDDEN_STATUSES:
            raise NotFoundError
        if not await uow.positions.exists_for_user_market(viewer.id, market.id):
            raise NotFoundError
        return market
