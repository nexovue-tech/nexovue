"""per-project masking rules

The worker's masking stage reads its rules from here, cached and refreshed on a
ticker rather than queried per span. Empty means the shipped defaults apply:
authorization headers, cookies, Luhn-valid card numbers, and attribute keys that
name a credential. Everything else is opt-in, because masking is irreversible and
the bar for a default rule is that a developer could never legitimately need the
value to debug.

Shaped like the `sampling_config` beside it deliberately — one query, one cache
and one refresh in the worker serve both stages.

Revision ID: 0003
Revises: 0002
Created: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "masking_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "masking_config")
