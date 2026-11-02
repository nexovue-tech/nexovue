"""detection_project_runs.traffic_lost_since: each project's vote for the install-wide guard

F6 (`docs/features/anomaly-detection/`), plan task T6.2, design revision 2.1, item 1.

**Why this change is required.** The install-wide guard holds every project's
minutes when at least 5 organizations lost traffic in the minutes nexovue's own
intake fell, because the cause is then upstream of all of them. It counts
organizations, so the coordinator must read, across projects, which of them are
losing traffic and since when. Each project job knows that only for its own
project, and keeps it nowhere another can read.

**Affected store and schema.** Postgres: one nullable `timestamptz` on
`detection_project_runs`, the row each project job already writes when it
completes. It holds the earliest minute from which a service of the project is
reading *losing traffic* at the project's last judged minute, or null when none
is. The coordinator reads it with `GROUP BY org_id`, so one organization with
many projects is one vote. No index: the table has one row per project.

**Compatibility and backfill.** Every existing row takes null: no vote, which
is what is known until each project's next run.

**Rollback and recovery.** `downgrade` drops the column; only current votes are
lost, and the next runs write them again.

**Tenant, retention, and security impact.** None: a time on a row already scoped
by `org_id` and removed with its project. The guard's read across organizations
is a count, and names no tenant to anyone.

Revision ID: 0047
Revises: 0046
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "detection_project_runs",
        sa.Column("traffic_lost_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("detection_project_runs", "traffic_lost_since")
