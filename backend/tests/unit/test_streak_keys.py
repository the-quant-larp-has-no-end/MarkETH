"""The keys under which a streak books (`app.services.ledger`).

A journal entry is booked once per idempotency key. A counted day books its tokens under
the user and the day, a milestone its bonus under the user and its length, so neither is
booked twice whatever becomes of the `streaks` row.
"""

from datetime import date
from typing import Final
from uuid import UUID

from app.core.config import MILESTONE_MAX_DAYS
from app.models.ledger import JOURNAL_IDEMPOTENCY_KEY_MAX_LENGTH
from app.services.ledger import LedgerRef, streak_bonus_key, streak_reward_key

USER_ID: Final[UUID] = UUID("0198c2f0-7b1e-7c53-9d0a-3a1f2b6c4d5e")
OTHER_ID: Final[UUID] = UUID("0198c2f0-7b1e-7c53-9d0a-3a1f2b6c4d5f")


def test_a_counted_day_books_under_its_user_and_its_date() -> None:
    assert streak_reward_key(USER_ID, date(2026, 10, 9)) == (
        "streak:0198c2f0-7b1e-7c53-9d0a-3a1f2b6c4d5e:2026-10-09"
    )
    assert streak_reward_key(USER_ID, date(2026, 1, 2)).endswith(":2026-01-02")


def test_a_milestone_books_under_its_user_and_its_length() -> None:
    assert streak_bonus_key(USER_ID, 7) == "streak:0198c2f0-7b1e-7c53-9d0a-3a1f2b6c4d5e:bonus:7"


def test_no_two_bookings_of_a_streak_share_a_key() -> None:
    keys = [
        streak_reward_key(USER_ID, date(2026, 10, 9)),
        streak_reward_key(USER_ID, date(2026, 10, 10)),
        streak_reward_key(OTHER_ID, date(2026, 10, 9)),
        streak_bonus_key(USER_ID, 7),
        streak_bonus_key(USER_ID, 30),
        streak_bonus_key(OTHER_ID, 7),
    ]

    assert len(set(keys)) == len(keys)


def test_the_longest_key_of_a_streak_fits_the_column_that_keeps_it() -> None:
    longest = max(
        streak_reward_key(USER_ID, date(9999, 12, 31)),
        streak_bonus_key(USER_ID, MILESTONE_MAX_DAYS),
        key=len,
    )

    assert len(longest) == 54
    assert len(longest) <= JOURNAL_IDEMPOTENCY_KEY_MAX_LENGTH


def test_an_entry_of_a_streak_refers_to_the_streak() -> None:
    assert LedgerRef.STREAK.value == "streak"
