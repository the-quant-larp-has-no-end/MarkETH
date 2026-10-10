"""The settings the tests run on: the committed `.example.env`, with APP_ENV=test and
the bootstrap admins the tests need.

A test asks for the `base_settings` fixture. What has to be known while a test module is
being imported (the bounds of a Hypothesis strategy, the rows of a parametrised test)
cannot wait for a fixture and calls `load_base_settings` instead. Both are one object.

The list of bootstrap admins is the one thing that is not the example's. An admin who
traded a market may not settle it (A.4), and no admin acts on their own account, so a
test of either rule needs a second admin, and `.example.env` names one. The tests
therefore run with two; `Factory.admin` registers them (tests/support/factories.py).
"""

from functools import lru_cache
from pathlib import Path
from typing import Final

from app.core.config import REPO_ROOT, Settings

EXAMPLE_ENV_FILE: Final[Path] = REPO_ROOT / ".example.env"

TEST_ADMIN_EMAILS: Final[tuple[str, ...]] = ("admin@ethz.ch", "second.admin@ethz.ch")
"""ADMIN_EMAILS of the test settings: the two bootstrap admins."""


@lru_cache(maxsize=1)
def load_base_settings() -> Settings:
    """`.example.env` with APP_ENV=test and two bootstrap admins. Nothing runs behind
    its hosts."""
    example = Settings.load(EXAMPLE_ENV_FILE)
    identity = example.identity.model_copy(update={"admin_emails": list(TEST_ADMIN_EMAILS)})
    return example.model_copy(
        update={"app": example.app.model_copy(update={"env": "test"}), "identity": identity}
    )
