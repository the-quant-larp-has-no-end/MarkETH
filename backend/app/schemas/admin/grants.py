"""Result of granting every owed monthly voucher at once (SPEC §2, Appendix A.2)."""

from app.schemas.common import CreditAmount, ResponseModel


class MonthlyGrantRunResponse(ResponseModel):
    """`POST /admin/grants/monthly`. Running it again right away books nothing."""

    users_checked: int
    grants_booked: int
    """Monthly grants this run booked, over all users and months."""

    total_cr: CreditAmount
    """Credits this run issued."""
