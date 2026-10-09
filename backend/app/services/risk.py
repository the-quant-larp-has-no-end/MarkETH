"""The admin risk page: how much VIS could lose and owe right now (SPEC §5, A.6).

Everything here is read from Postgres at the moment of the request; nothing is cached.
"""

from app.core.clock import Clock
from app.core.config import Settings
from app.core.identity import AuthenticatedUser
from app.schemas.admin.risk import AlertItem, RiskResponse
from app.services import UowFactory
from app.services.audit import AuditService
from app.services.market_view import MarketView


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
          `pending_coffee_tickets_cr = coffee_tickets.pending_value()`;
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
          `accounts.list_market_accounts`) and `exposure_cr = max(q_yes, q_no) -
          balance`.
        * `alerts = alerts.list_unacknowledged()`, newest first.
        """
        raise NotImplementedError

    async def acknowledge_alert(self, actor: AuthenticatedUser, alert_id: int) -> AlertItem:
        """Mark an alert as seen (`POST /admin/alerts/{id}/ack`).

        One transaction: `alerts.acknowledge(alert_id, actor.id, now)`. False means
        nothing changed: load the alert to tell why. No such alert raises
        `NotFoundError` (404); one acknowledged before raises `ConflictError` (409).
        On success audit `ALERT_ACK` with target `AuditService.target(
        AuditTarget.ALERT, alert_id)` and return the alert with its `acked_at`.
        """
        raise NotImplementedError
