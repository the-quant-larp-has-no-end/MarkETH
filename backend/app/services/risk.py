"""The admin risk page: how much VIS could lose and owe right now (SPEC §5, A.6).

Everything here is read from Postgres at the moment of the request; nothing is cached.
The figures come from several statements, not from one snapshot, so a trade that
commits between two of them can show in one figure and not yet in another.
"""

from datetime import datetime
from typing import Final

from app.core.clock import Clock, start_of_month
from app.core.config import Settings
from app.core.enums import AccountKind, MarketStatus
from app.core.errors import ConflictError, NotFoundError
from app.core.identity import AuthenticatedUser
from app.core.units import to_cr
from app.models.market import Market
from app.models.ops import Alert
from app.schemas.admin.risk import AlertItem, Obligations, RiskMarketRow, RiskResponse
from app.services import UowFactory
from app.services.audit import AuditAction, AuditService, AuditTarget
from app.services.market_view import UNSETTLED_STATUSES, MarketView

CAPPED_STATUSES: Final[frozenset[MarketStatus]] = frozenset(
    {MarketStatus.OPEN, MarketStatus.HALTED}
)
"""Stored statuses of the markets that count against `MAX_OPEN_MARKETS` and
`MAX_AGGREGATE_LOSS_BUDGET`: the ones approval counts (SPEC §5, A.5)."""

ALREADY_ACKNOWLEDGED_MESSAGE: Final[str] = "This alert was acknowledged before."
"""Message of the `ConflictError` a second acknowledgement of an alert gets."""


class RiskService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        view: MarketView,
        audit: AuditService,
    ) -> None:
        self._uow_factory = uow_factory
        self._settings = settings
        self._clock = clock
        self._view = view
        self._audit = audit

    async def snapshot(self) -> RiskResponse:
        """The risk figures of SPEC §5, in one read-only transaction (`GET /admin/risk`).

        * `open_markets = markets.count_by_statuses({OPEN, HALTED})` against
          `MAX_OPEN_MARKETS`; `loss_budget_cr = markets.sum_budget_by_statuses({OPEN,
          HALTED})` against `MAX_AGGREGATE_LOSS_BUDGET`. These are the two figures
          approval checks (A.5), on stored statuses.
        * `obligations`: `user_balances_cr = accounts.sum_balance_by_kind(USER)`;
          `pending_vouchers_cr = vouchers.pending_value()`;
          `market_exposure_cr = markets.sum_exposure_by_statuses(UNSETTLED_STATUSES)`:
          "each open market" of SPEC §5 is read as every market not yet settled, so
          one that is closed but still owes its payout counts (A.6); `total_cr` their
          sum; `alert_threshold_cr = OBLIGATIONS_ALERT_THRESHOLD_CR`;
          `above_threshold = total > threshold`. That flag is the alert of SPEC §5:
          the page shows it, and reading the page writes no `alerts` row (A.6).
        * `unredeemed_code_value_cr = codes.unredeemed_value(now)`, reported beside the
          obligations, not inside them (A.6).
        * `code_issuance_this_month_cr = codes.issued_value_since(start_of_month(now,
          APP_TIMEZONE))` against `MAX_CODE_ISSUANCE_PER_MONTH`.
        * `markets`: one row per market of `markets.list_by_statuses(
          UNSETTLED_STATUSES)` (soonest `close_at` first) with its effective status,
          display `p_yes`, the balance of its account (all of them in one query,
          `accounts.list_market_accounts`), `exposure_cr = max(q_yes, q_no) -
          balance`, and `void_cost_cr = <net stakes> - balance`, the net stakes
          being `positions.net_stake_totals(<the market ids>)`, one query for all of
          them, and 0 for a market nobody holds a position in. `void_cost_cr` is what
          `vis:mm_pnl` would add to the market's account if an admin voided it now
          (A.5): a void refunds every net stake, and traders who sold above their
          cost keep what they took out. It is not part of the obligations and is
          bounded by the stake limit per trader, not by the market's budget.
        * `alerts = alerts.list_unacknowledged()`, newest first.

        A market that is not settled has its account since its approval; one without
        is a broken book and raises `RuntimeError` (500) instead of being shown with a
        balance it does not have.
        """
        now = self._clock.now()
        risk = self._settings.risk
        month_start = start_of_month(now, self._settings.app.timezone)
        async with self._uow_factory() as uow:
            open_markets = await uow.markets.count_by_statuses(CAPPED_STATUSES)
            loss_budget_micro = await uow.markets.sum_budget_by_statuses(CAPPED_STATUSES)
            user_balances_micro = await uow.accounts.sum_balance_by_kind(AccountKind.USER)
            pending_vouchers_micro = await uow.vouchers.pending_value()
            market_exposure_micro = await uow.markets.sum_exposure_by_statuses(UNSETTLED_STATUSES)
            unredeemed_code_value_micro = await uow.codes.unredeemed_value(now)
            code_issuance_micro = await uow.codes.issued_value_since(month_start)
            markets = await uow.markets.list_by_statuses(UNSETTLED_STATUSES)
            market_ids = [market.id for market in markets]
            accounts = await uow.accounts.list_market_accounts(market_ids)
            net_stakes_micro = await uow.positions.net_stake_totals(market_ids)
            alerts = await uow.alerts.list_unacknowledged()

        rows: list[RiskMarketRow] = []
        for market in markets:
            account = accounts.get(market.id)
            if account is None:
                raise RuntimeError(f"market {market.id} is {market.status} but has no account")
            rows.append(
                self._market_row(
                    market, now, account.balance_micro, net_stakes_micro.get(market.id, 0)
                )
            )

        total_micro = user_balances_micro + pending_vouchers_micro + market_exposure_micro
        threshold_micro = risk.obligations_alert_threshold_micro
        return RiskResponse(
            open_markets=open_markets,
            max_open_markets=risk.max_open_markets,
            loss_budget_cr=to_cr(loss_budget_micro),
            max_loss_budget_cr=to_cr(risk.max_aggregate_loss_budget_micro),
            obligations=Obligations(
                user_balances_cr=to_cr(user_balances_micro),
                pending_vouchers_cr=to_cr(pending_vouchers_micro),
                market_exposure_cr=to_cr(market_exposure_micro),
                total_cr=to_cr(total_micro),
                alert_threshold_cr=to_cr(threshold_micro),
                above_threshold=total_micro > threshold_micro,
            ),
            unredeemed_code_value_cr=to_cr(unredeemed_code_value_micro),
            code_issuance_this_month_cr=to_cr(code_issuance_micro),
            max_code_issuance_per_month_cr=to_cr(self._settings.codes.max_issuance_per_month_micro),
            markets=rows,
            alerts=[_alert_item(alert) for alert in alerts],
        )

    async def acknowledge_alert(self, actor: AuthenticatedUser, alert_id: int) -> AlertItem:
        """Mark an alert as seen (`POST /admin/alerts/{id}/ack`).

        One transaction: `alerts.acknowledge(alert_id, actor.id, now)`. False means
        nothing changed: load the alert to tell why. No such alert raises
        `NotFoundError` (404); one acknowledged before raises `ConflictError` (409).
        On success audit `ALERT_ACK` with target `AuditService.target(
        AuditTarget.ALERT, alert_id)` and return the alert with its `acked_at`.

        Of two acknowledgements sent at once the second waits for the first inside
        `acknowledge` and then finds the alert acknowledged: one succeeds, one audit
        row is written.
        """
        async with self._uow_factory() as uow:
            now = self._clock.now()
            acknowledged = await uow.alerts.acknowledge(alert_id, actor.id, now)
            alert = await uow.alerts.get(alert_id)
            if alert is None:
                raise NotFoundError
            if not acknowledged:
                raise ConflictError(ALREADY_ACKNOWLEDGED_MESSAGE)
            await self._audit.record(
                uow,
                actor_id=actor.id,
                action=AuditAction.ALERT_ACK,
                target=AuditService.target(AuditTarget.ALERT, alert_id),
                before={"acked_at": None},
                after={"acked_at": now.isoformat()},
            )
            return _alert_item(alert)

    def _market_row(
        self, market: Market, now: datetime, balance_micro: int, net_stake_micro: int
    ) -> RiskMarketRow:
        """The row of one market that is not settled. `balance_micro` is the balance of
        its account, `net_stake_micro` the net stakes of its traders added up."""
        return RiskMarketRow(
            id=market.id,
            question=market.question,
            status=self._view.effective_status(market, now),
            tier=market.tier,
            b_cr=to_cr(market.b_micro),
            budget_cr=to_cr(market.budget_micro),
            p_yes=self._view.probabilities(self._view.p_yes_micro(market))[0],
            q_yes=to_cr(market.q_yes_micro),
            q_no=to_cr(market.q_no_micro),
            account_balance_cr=to_cr(balance_micro),
            exposure_cr=to_cr(max(market.q_yes_micro, market.q_no_micro) - balance_micro),
            void_cost_cr=to_cr(net_stake_micro - balance_micro),
            volume_cr=to_cr(market.volume_micro),
            close_at=market.close_at,
        )


def _alert_item(alert: Alert) -> AlertItem:
    return AlertItem(
        id=alert.id,
        severity=alert.severity,
        message=alert.message,
        context=alert.context,
        created_at=alert.created_at,
        acked_at=alert.acked_at,
    )
