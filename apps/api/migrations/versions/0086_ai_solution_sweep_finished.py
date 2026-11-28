"""When rung 3's sweep last finished, for the operator's alert

F5 (`docs/features/ai-solution/`), plan Step 9 (T9.1; design, *Observability and
operations*: "A second alert fires when `sweep_fixes` has not finished in 15 minutes").

**Why this change is required.** `fixes.sweep_fixes` deletes stored code when its time is up
(BR9), ends stuck rows and records each result once. It runs in a `scheduler-connections`
worker's child, whose metrics no scrape sees (`nexovue_scheduler/metrics.py`), and a failed
pass leaves nothing in the `jobs` table once its attempts are spent. So beat cannot tell a
sweep that finished from one that raised every minute (F5 open decision 70). The sweep now
records the end of each whole pass here, and beat reads its age.

**Affected store and schema.** Postgres: one new table, `fix_sweep_status`, one row per sweep
name, the only name allowed being `sweep_fixes`.

**Compatibility and backfill.** None: no row until the first pass after this migration, which
beat reads as never finished.

**Rollback and recovery.** A downgrade drops the table. It holds one time the next pass writes
again, so nothing is lost that exists nowhere else.

**Tenant, retention, and security impact.** Install-wide bookkeeping, like
`alert_evaluation_status`: no organization, no person, no code. Named in the tenant inventory's
excluded tables.

Revision ID: 0086
Revises: 0085
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0086"
down_revision: str | None = "0085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A frozen copy of `nexovue_platform.fixes.records.FIX_SWEEP_NAME`, held equal by
# `test_fix_sweep_status.py`.
_SWEEP = "sweep_fixes"


def upgrade() -> None:
    op.create_table(
        "fix_sweep_status",
        sa.Column("sweep", sa.Text(), primary_key=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"sweep = '{_SWEEP}'", name="sweep_is_known"),
    )


def downgrade() -> None:
    op.drop_table("fix_sweep_status")
