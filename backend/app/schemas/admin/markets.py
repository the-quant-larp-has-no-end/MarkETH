"""Bodies of the seven admin actions on a market (SPEC §6, §10, Appendix A.5).

Each action answers with the market's `MarketDetailResponse` as it is afterwards.
"""

from typing import Final, Self

from pydantic import AwareDatetime, model_validator

from app.core.enums import MarketTier, Outcome
from app.schemas.common import PositiveCreditAmount, RequestModel, on_field
from app.schemas.markets import LongText, QuestionText, ShortText

BUDGET_NEEDED_MESSAGE: Final[str] = "Enter the budget of a custom market."
"""What an approval says on `budget_cr` when the tier is CUSTOM and it names none."""

BUDGET_NOT_TAKEN_MESSAGE: Final[str] = "Leave the budget out unless the tier is custom."
"""What an approval says on `budget_cr` when a fixed tier comes with one."""


class ApproveMarketRequest(RequestModel):
    """`POST /admin/markets/{id}/approve`: pick the tier, optionally reword, and open.

    A field left out (or null) keeps what the proposal says.
    """

    tier: MarketTier
    budget_cr: PositiveCreditAmount | None = None
    """The loss budget of a CUSTOM market; b is derived from it (A.5). Required for
    CUSTOM, not accepted for a fixed tier."""

    question: QuestionText | None = None
    yes_definition: ShortText | None = None
    no_definition: ShortText | None = None
    resolution_criteria: LongText | None = None
    resolution_source: ShortText | None = None
    close_at: AwareDatetime | None = None
    resolve_by: AwareDatetime | None = None

    @model_validator(mode="after")
    def _budget_only_for_custom(self) -> Self:
        if self.tier is MarketTier.CUSTOM and self.budget_cr is None:
            raise on_field("budget_cr", "value_error", BUDGET_NEEDED_MESSAGE)
        if self.tier is not MarketTier.CUSTOM and self.budget_cr is not None:
            raise on_field("budget_cr", "value_error", BUDGET_NOT_TAKEN_MESSAGE)
        return self


class RejectMarketRequest(RequestModel):
    """`POST /admin/markets/{id}/reject`. The proposer sees the reason."""

    reason: ShortText


class HaltMarketRequest(RequestModel):
    """`POST /admin/markets/{id}/halt`. The reason goes to the audit log."""

    reason: ShortText


class ResumeMarketRequest(RequestModel):
    """`POST /admin/markets/{id}/resume`."""

    note: ShortText | None = None
    """Optional remark for the audit log."""


class CloseMarketRequest(RequestModel):
    """`POST /admin/markets/{id}/close`: stop trading for good, before the deadline or after."""

    note: ShortText | None = None
    """Optional remark for the audit log."""


class ResolveMarketRequest(RequestModel):
    """`POST /admin/markets/{id}/resolve`: the outcome and what proves it."""

    outcome: Outcome
    evidence: LongText


class VoidMarketRequest(RequestModel):
    """`POST /admin/markets/{id}/void`: refund every net stake. The reason is published
    as the market's resolution."""

    reason: LongText
