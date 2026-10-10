<%
    from alembic.script import ScriptDirectory

    from app.models.fingerprint import schema_fingerprint

    before = ScriptDirectory.from_config(config).get_revision(down_revision)
    assert before is not None, "a revision follows another: 0001 is the baseline"
%>"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision}

Say here what changes, and what becomes of the rows that are there: every database
with people's balances in it takes this step.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

from app.models.revisions import store_fingerprint

revision: str = "${up_revision}"
down_revision: str | Sequence[str] | None = "${down_revision}"
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}

FINGERPRINT: Final[str] = "${schema_fingerprint()}"
"""The fingerprint of the models this revision leads to, taken when it was written."""

PREVIOUS_FINGERPRINT: Final[str] = (
    "${before.module.FINGERPRINT}"
)
"""The one of revision ${down_revision}, which `downgrade` leads back to."""


def upgrade() -> None:
    # The steps go here, with `op` and `sa`. A table that is created needs
    # `grant_app_role()` of `app.models.revisions` after it; a rule of
    # `app.models.triggers` or `app.models.privileges` that changed is changed here as
    # SQL.
    store_fingerprint(FINGERPRINT)


def downgrade() -> None:
    # The steps of `upgrade`, undone in reverse.
    store_fingerprint(PREVIOUS_FINGERPRINT)
