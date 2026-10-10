"""The settings the tests run on: the committed `.example.env`, with APP_ENV=test, the
bootstrap admins the tests need, and a streak that books nothing.

A test asks for the `base_settings` fixture. What has to be known while a test module is
being imported (the bounds of a Hypothesis strategy, the rows of a parametrised test)
cannot wait for a fixture and calls `load_base_settings` instead. Both are one object.

The list of bootstrap admins is the one thing that is not the example's. An admin who
traded a market may not settle it (A.4), and no admin acts on their own account, so a
test of either rule needs a second admin, and `.example.env` names one. The tests
therefore run with two; `Factory.admin` registers them (tests/support/factories.py).

A trade counts a day of its trader's streak, and with the example's values that day
books tokens. A test of a trade asserts on balances to the micro-credit, so the tests
run with a daily reward of nothing and no milestone: the days are counted all the same.
A test that is about what a streak books carries the marker `streak_rewards` and gets
the example's values (`streak_settings_of`).
"""

from functools import lru_cache
from pathlib import Path
from typing import Final

from app.core.config import REPO_ROOT, Settings, StreakSettings

EXAMPLE_ENV_FILE: Final[Path] = REPO_ROOT / ".example.env"

TEST_ADMIN_EMAILS: Final[tuple[str, ...]] = ("admin@ethz.ch", "second.admin@ethz.ch")
"""ADMIN_EMAILS of the test settings: the two bootstrap admins."""


STREAK_REWARDS_MARKER: Final[str] = "streak_rewards"
"""The marker of a test that runs with the streak values of `.example.env`."""


@lru_cache(maxsize=1)
def load_base_settings() -> Settings:
    """`.example.env` with APP_ENV=test, two bootstrap admins, and a streak that books
    nothing. Nothing runs behind its hosts."""
    example = Settings.load(EXAMPLE_ENV_FILE)
    identity = example.identity.model_copy(update={"admin_emails": list(TEST_ADMIN_EMAILS)})
    streaks = example.streaks.model_copy(update={"daily_reward_micro": 0, "milestones": ()})
    return example.model_copy(
        update={
            "app": example.app.model_copy(update={"env": "test"}),
            "identity": identity,
            "streaks": streaks,
        }
    )


@lru_cache(maxsize=1)
def example_streak_settings() -> StreakSettings:
    """`STREAK_DAILY_REWARD_CR`, `STREAK_MILESTONES` and `STREAK_LOCK_TIMEOUT_MS` as
    `.example.env` has them: what a test with the marker `streak_rewards` runs on."""
    return Settings.load(EXAMPLE_ENV_FILE).streaks
