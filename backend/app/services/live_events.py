"""The events a transaction announces, built from the rows it wrote (SPEC §9).

A service builds an event here and hands it to `uow.announce` inside its transaction;
the event reaches the open tabs once that transaction has committed.

    uow.announce(self._events.market_quote(market, now))
    uow.announce(me_changed([user.id], now))

Whom an event about a market is for is said in one place, `LiveEvents.about`, from the
market's own row: whoever is inside the market (`MarketAccess.is_inside`). No publisher
states the audience of a market by hand, and no body names one.

A carried figure is one every viewer of the audience is shown alike and a version
orders: the price and the volume of a market, read through `MarketView` as
`GET /markets/{id}` reads them. Everything else is announced without a figure and read
again by the tab.
"""

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Final
from uuid import UUID

from app.core.live import Audience, LiveEvent, LivePayload, Topic, TopicKind
from app.core.units import to_cr
from app.models.market import Market
from app.schemas.live import MarketQuoteEvent, MarketStatusEvent, MeChangedEvent
from app.services.market_view import HIDDEN_STATUSES, MarketView

MARKET_TOPIC_KINDS: Final[frozenset[TopicKind]] = frozenset(
    {TopicKind.MARKETS, TopicKind.MARKET, TopicKind.BROADCAST}
)
"""The kinds of topic an event about a market goes out on: the market lists, the market
itself and its broadcast."""


class LiveEvents:
    def __init__(self, view: MarketView) -> None:
        self._view = view

    def market_quote(self, market: Market, now: datetime) -> LiveEvent:
        """`market.quote`: the price and the volume of `market` as a trade leaves its
        row, on `markets` and `market:{id}`.

        It holds the numbers `GET /markets/{id}` reports for that version. `now` is the
        instant of the trade and the `t` of the chart's point. It carries figures, so
        it is announced outside any savepoint.
        """
        p_yes, p_no = self._view.probabilities(self._view.p_yes_micro(market))
        return self.about(
            market,
            _lists_and_market(market),
            MarketQuoteEvent(
                type="market.quote",
                market_id=market.id,
                version=market.version,
                p_yes=p_yes,
                p_no=p_no,
                volume_cr=to_cr(market.volume_micro),
                ts=now,
            ),
        )

    def market_status(self, market: Market, now: datetime) -> LiveEvent:
        """`market.status`: the status of `market` changed, on `markets` and
        `market:{id}`. It reports the effective status at `now`: an OPEN market past
        its `close_at` is CLOSED."""
        return self.about(
            market,
            _lists_and_market(market),
            MarketStatusEvent(
                type="market.status",
                market_id=market.id,
                status=self._view.effective_status(market, now),
                trading_open=self._view.is_trading_open(market, now),
                ts=now,
            ),
        )

    def about(self, market: Market, topics: Sequence[Topic], payload: LivePayload) -> LiveEvent:
        """An event about `market` on `topics`, for whoever is inside the market:
        everyone for a public one; the admins, the members of its programme and its
        proposer for a private one.

        `topics` are of this market: `markets`, `market:{id}`, `broadcast:{id}`, or
        several of them. `ValueError` for a market stored PROPOSED or REJECTED, which
        has no audience and no event, for no topic, and for a topic that is not of
        this market.
        """
        if market.status in HIDDEN_STATUSES:
            raise ValueError(f"a market stored {market.status.value} has no events")
        if not topics:
            raise ValueError("an event about a market goes out on at least one topic")
        for topic in topics:
            if topic.kind not in MARKET_TOPIC_KINDS or topic.key not in (None, market.id):
                raise ValueError(f"{topic} is no topic of market {market.id}")
        return LiveEvent(
            tuple(topics),
            Audience.market(market.id, market.audience_programme, market.created_by),
            payload,
        )


def me_changed(
    user_ids: Collection[UUID], now: datetime, *, ends_streams_of: UUID | None = None
) -> LiveEvent:
    """`me.changed`: something of each of these users' own changed, a balance, a
    position, a notice, the profile or the role. It goes to the streams of these users
    and to nobody else, and carries no figure: the tab reads again.

    `ends_streams_of` names a user whose streams end once the event has gone out,
    because who that user is to the API has changed. No user at all gives an event that
    reaches nobody.
    """
    users = sorted(set(user_ids))
    return LiveEvent(
        tuple(Topic(TopicKind.ME, user_id) for user_id in users),
        Audience.users(users),
        MeChangedEvent(type="me.changed", ts=now),
        ends_streams_of=ends_streams_of,
    )


def _lists_and_market(market: Market) -> tuple[Topic, Topic]:
    return Topic(TopicKind.MARKETS), Topic(TopicKind.MARKET, market.id)
