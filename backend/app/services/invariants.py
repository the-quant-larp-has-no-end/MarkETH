"""The circuit breaker's three checks (SPEC §5, Appendix A.4).

They run after a trade's writes and before its commit. If one fails, the trade is
rolled back and the market halted: the books are wrong and must not get worse.
"""

from app.models.market import Market
from app.repositories.unit_of_work import UnitOfWork


class InvariantChecker:
    async def check(self, uow: UnitOfWork, market: Market) -> None:
        """Verify the accounting invariants inside the caller's transaction.

        `market` is the locked row with this trade's quantities already set. Flush
        first (`uow.flush()`), so the queries see every write of the transaction. Then,
        in this order, raise `InvariantViolationError(invariant, context)` for the
        first that fails:

        1. `"ledger_nets_to_zero"`: `accounts.total_balance() == 0`. Context:
           `{"total_balance_micro": ...}`.
        2. `"positions_match_market"`: `positions.sum_shares_by_outcome(market.id)`
           equals the market's `q_yes_micro` and `q_no_micro`. Context:
           `{"market_id", "q_yes_micro", "q_no_micro", "positions_yes_micro",
           "positions_no_micro"}`.
        3. `"no_negative_user_balance"`: `accounts.has_negative_user_balance()` is
           false. Context: `{}`.

        The context is stored in `alerts.context` (JSONB): ids as strings, amounts as
        integers. Returns None when all three hold. The caller, not this class, rolls
        back and halts the market.
        """
        raise NotImplementedError
