"""usage: profile samples, metered and shown, not billed

F17 (`docs/features/profiling/`).

**Why this change is required.** Profiles are the fourth signal, and the usage
meter counts every signal it reads per organization, project and day. Profiling
is metered and shown to the customer but not billed while it is in preview (spec
D1): its samples get their own counter, and add nothing to `bytes` — what the
allowance spends — or to `usage_daily_services` — what the plan prices.

**Affected store and schema.** Postgres only: `usage_daily.profile_samples`,
BIGINT NOT NULL DEFAULT 0, beside `metric_points`.

**Compatibility and backfill.** None needed: every existing day sent no
profiles, which is what the default says. nexovue is pre-deployment.

**Rollback and recovery.** `downgrade` drops the column.

**Tenant, retention, and security impact.** None: a count on a row that already
carries `org_id`.

Revision ID: 0030
Revises: 0029
Created: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usage_daily",
        sa.Column("profile_samples", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("usage_daily", "profile_samples")
