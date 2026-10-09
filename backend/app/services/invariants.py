"""The circuit breaker's four checks (SPEC §5, §8 step 6, Appendix A.4).

They run after a trade's writes and before its commit. If one fails, the trade is
rolled back and the market halted: the books are wrong and must not get worse.

The fourth check is what "the market's limits" of SPEC §8 comes to. The LMSR bounds the
market maker's loss at b · ln 2, which approval fits into the market's budget; no trade
is refused for it, but every trade is verified against it. Postgres backs some of this
up on its own (a market account and a user balance cannot go negative, a balance is the
sum of its postings); the checks here are what halts a market cleanly before a
constraint would merely fail the request.
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
        4. `"market_within_budget"`: with B the balance of
           `accounts.get_market_account(market.id)`, `max(q_yes_micro, q_no_micro) - B
           <= market.budget_micro`. The left side is what VIS would have to add if the
           more heavily held outcome won. Context: `{"market_id", "exposure_micro",
           "budget_micro"}`.

        The context is stored in `alerts.context` (JSONB): ids as strings, amounts as
        integers. Returns None when all four hold. The caller, not this class, rolls
        back and halts the market.
        """
        raise NotImplementedError
