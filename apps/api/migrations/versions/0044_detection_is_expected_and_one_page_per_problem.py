"""detection_project_runs.expected_since, and notifications.dedup_key

F6 (`docs/features/anomaly-detection/`), plan Step 4, after D34 and D35.

**Why this change is required.**

- *D34: a project whose detection never ran is told too.* Freshness was only the
  last completed run, so a project with traffic and no completed run, such as a
  fresh install whose ClickHouse is misconfigured, could never count as stopped.
  `expected_since` is when the stop sweep first expected the project to be
  judged in its current spell of traffic or open finding; it is cleared when
  detection stops working on the project. The 15 minutes (D24) and the API's 3
  minutes (FR25) count from the later of it and the last completed run, so a
  project that has just started sending, or sends again after a quiet day, has
  its chance to be judged first. `last_completed_at` becomes nullable: a project
  can be expected before it has ever been judged.
- *D35: one problem pages once, whichever fires first.* `dedup_key` is the
  PagerDuty alert a row was sent to. It is fixed at the first claim of a subject's
  first row on a channel and reused for the rest of that subject's life there, so
  a finding and a linked incident share one alert in either order, and the
  alert is resolved only by the last of them to close.

**Affected store and schema.** Postgres: `detection_project_runs.expected_since`
(new, nullable), `detection_project_runs.last_completed_at` (now nullable),
`notifications.dedup_key` (new, nullable). Its index is built concurrently in a
migration of its own, 0045.

**Compatibility and backfill.** None needed. Existing runs get `expected_since`
from the first stop sweep after the upgrade, which starts their clock then. Rows
already delivered keep a null key; a subject told before the upgrade is keyed
afresh at its next claim, as its own (nexovue is pre-deployment).

**Rollback and recovery.** `downgrade` drops both new columns and restores the
not-null on `last_completed_at`, first deleting the runs of projects never judged:
they hold only when detection began expecting them, which the next sweep rebuilds.

**Tenant, retention, and security impact.** Both tables are already scoped by
`org_id` and deleted with their project. No new data about a tenant is kept.

Revision ID: 0044
Revises: 0043
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "detection_project_runs",
        sa.Column("expected_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column(
        "detection_project_runs",
        "last_completed_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.add_column("notifications", sa.Column("dedup_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("notifications", "dedup_key")
    op.execute("DELETE FROM detection_project_runs WHERE last_completed_at IS NULL")
    op.alter_column(
        "detection_project_runs",
        "last_completed_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.drop_column("detection_project_runs", "expected_since")
