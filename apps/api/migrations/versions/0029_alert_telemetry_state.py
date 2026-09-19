"""alert telemetry state: when each service was last heard, which errors are known

F21 Step 5 (`docs/features/alerts/`). What the service conditions need that no
table held.

**Why this change is required.** *Service went silent* needs the last time each
service sent anything; `usage_daily_services` records only which days a service
reported. *New error group* needs to know which groups a project has already
seen; error groups are computed at read time and stored nowhere.

**Affected store and schema.** Postgres only. Three tables:

- `service_last_seen` — written by the Go usage meter, in the transaction that
  counts a flush, with `GREATEST` so a late or replayed flush never moves it back.
- `error_groups_seen` — one row per group a project has had. The scheduler's
  `INSERT ... ON CONFLICT DO NOTHING RETURNING` *is* the decision that a group is
  new, so a sweep run twice cannot announce one group twice (AC8).
- `error_group_watermarks` — how far, in receive time, each project's new-error
  sweep has read, and when its baseline was finished. Without the baseline, a rule
  added to a project with a history would announce every error it ever had.
  `baselined_at` is empty while a history larger than one read is still being
  marked seen, a part a minute; nothing is announced until it is set.

**Compatibility and backfill.** None needed: the meter fills `service_last_seen`
from its next flush, and a baseline is taken when a project's first new-error rule
is evaluated.

**Rollback and recovery.** `downgrade` drops the three tables.

**Tenant, retention, and security impact.** Each carries `org_id` and cascades
from its project (`organizations/tables.py`). Service names and group ids only;
no telemetry content.

Revision ID: 0029
Revises: 0028
Created: 2026-09-19
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)
_SERVICE_NAME_LENGTH = 255


def _tenant() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id",
            _UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "service_last_seen",
        *_tenant(),
        sa.Column("service_name", sa.String(_SERVICE_NAME_LENGTH), primary_key=True),
        sa.Column("last_seen_at", _TIMESTAMP, nullable=False),
    )
    op.create_table(
        "error_groups_seen",
        *_tenant(),
        sa.Column("group_id", sa.String(64), primary_key=True),
        sa.Column("first_seen_at", _TIMESTAMP, nullable=False),
    )
    op.create_table(
        "error_group_watermarks",
        *_tenant(),
        sa.Column("received_through", _TIMESTAMP, nullable=False),
        sa.Column("baselined_at", _TIMESTAMP, nullable=True),
    )


def downgrade() -> None:
    for table in ("error_group_watermarks", "error_groups_seen", "service_last_seen"):
        op.drop_table(table)
