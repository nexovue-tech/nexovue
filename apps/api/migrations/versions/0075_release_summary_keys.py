"""A release summary's keys, without stopping writes

F28 (`docs/features/deployment-release-tracking/`), plan Step 5 (T5.6; design section 6,
D52), follows 0074.

**Why this change is required.** A retried summary job must write nothing twice, and a
second release's summary on one channel must not be dropped as the first one's repeat
(AC82). `notifications` holds rows, so each key is built `CONCURRENTLY`, outside a
transaction (`docs/engineering/database.md`).

**Affected store and schema.** Postgres, in an autocommit block:

- `uq_notifications_release_period_id_channel_id_kind_sequence`, a unique constraint,
  as the incident's and the finding's are: its index is built concurrently and the
  constraint takes it over (`ADD CONSTRAINT … USING INDEX`), which needs only a brief
  lock. `sequence` is always 0 for a summary, so the writer's `ON CONFLICT DO NOTHING`
  makes a retry insert nothing. It also serves the period's cascade.
- `uq_notifications_project_id_channel_id_kind_sequence_project`, the project notices'
  partial key, rebuilt with `release_period_id IS NULL` in its condition. Without it,
  every summary on a channel names neither an incident nor a finding, with sequence 0,
  so the second release's summary would conflict with the first and be dropped. The new
  index is built under a second name, the old one dropped, and the new one renamed,
  so the key never lapses.
- `ix_projects_release_summary_channel_id`, partial on the set rows, so deleting a
  channel finds the projects to turn off without reading every project.

**Compatibility and backfill.** None: no row names a period before this revision.

**Rollback and recovery.** `downgrade` puts the project key back as 0050 built it, and
drops the other two. With two summaries on one channel that key cannot be built, so the
downgrade fails, leaving the rebuilt key in place, until those rows are deleted. A
failed concurrent build leaves an invalid index behind; the upgrade drops any such
leftover first, so running it again when the table is quiet recovers. A run that built
the constraint and failed before Alembic recorded the revision finds it and leaves it be.

**Tenant, retention, and security impact.** None: no data is added.

Revision ID: 0075
Revises: 0074
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0075"
down_revision: str | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERIOD_KEY = "uq_notifications_release_period_id_channel_id_kind_sequence"
PROJECT_KEY = "uq_notifications_project_id_channel_id_kind_sequence_project"
REBUILT_PROJECT_KEY = "uq_notifications_project_key_rebuilt"
CHANNEL_INDEX = "ix_projects_release_summary_channel_id"
PROJECT_KEY_COLUMNS = ["project_id", "channel_id", "kind", "sequence"]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        _build_period_key()
        _rebuild_project_key(
            "incident_id IS NULL AND finding_id IS NULL AND release_period_id IS NULL"
        )
        op.drop_index(CHANNEL_INDEX, "projects", postgresql_concurrently=True, if_exists=True)
        op.create_index(
            CHANNEL_INDEX,
            "projects",
            ["release_summary_channel_id"],
            postgresql_where=sa.text("release_summary_channel_id IS NOT NULL"),
            postgresql_concurrently=True,
        )


def _build_period_key() -> None:
    # Once the constraint owns the index, the index cannot be dropped as a leftover.
    already_built = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conrelid = 'notifications'::regclass AND conname = :name"
            ),
            {"name": PERIOD_KEY},
        )
        .first()
    )
    if already_built:
        return
    op.drop_index(PERIOD_KEY, "notifications", postgresql_concurrently=True, if_exists=True)
    op.create_index(
        PERIOD_KEY,
        "notifications",
        ["release_period_id", "channel_id", "kind", "sequence"],
        unique=True,
        postgresql_concurrently=True,
    )
    op.execute(
        f"ALTER TABLE notifications ADD CONSTRAINT {PERIOD_KEY} UNIQUE USING INDEX {PERIOD_KEY}"
    )


def _rebuild_project_key(condition: str) -> None:
    """The project key under a new condition, with a key in place throughout."""
    op.drop_index(
        REBUILT_PROJECT_KEY, "notifications", postgresql_concurrently=True, if_exists=True
    )
    op.create_index(
        REBUILT_PROJECT_KEY,
        "notifications",
        PROJECT_KEY_COLUMNS,
        unique=True,
        postgresql_where=sa.text(condition),
        postgresql_concurrently=True,
    )
    op.drop_index(PROJECT_KEY, "notifications", postgresql_concurrently=True, if_exists=True)
    op.execute(f"ALTER INDEX {REBUILT_PROJECT_KEY} RENAME TO {PROJECT_KEY}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(CHANNEL_INDEX, "projects", postgresql_concurrently=True, if_exists=True)
        _rebuild_project_key("incident_id IS NULL AND finding_id IS NULL")
        op.drop_constraint(PERIOD_KEY, "notifications", type_="unique")
