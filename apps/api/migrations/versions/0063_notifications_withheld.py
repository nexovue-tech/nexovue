"""What a reliability target's burn may withhold: why a notification was not sent

F19 (`docs/features/slo-tracking/`), plan T5.1; ADR-038. Follows F22's 0061 (re-chained
at commit).

**Why this change is required.** A burn, a finding and a threshold incident can all say
the same news about one service. On a channel people read, the second of them is now
recorded as `dropped` and not sent (FR22), and a service's snooze holds back a burn's
messages (FR26). When that reason ends while the subject is still open, one message
goes after all (FR23). Releasing a row is a query, so why it was withheld, and by whom,
must be columns: `outcome_reason` is words for the delivery log.

**Affected store and schema.** Postgres, `notifications`, four nullable columns:

- `withheld_why`: `same_news` or `service_snoozed`; null on every other row.
- `withheld_by_incident_id`, `withheld_by_finding_id`: the subject that had told the
  channel the same news. No foreign key: it stays a fact of this row, and the release
  reads a subject that has gone as one that no longer withholds.
- `released_at`: when this row stopped being held, a release written for it or none
  owed, so it is released once.

The index the release reads is built `CONCURRENTLY` in 0064, which also validates the
check below.

**Locks on tables that hold rows.** `notifications` holds rows. Adding nullable
columns with no default rewrites nothing. The check on `withheld_why` is added
`NOT VALID`, which holds new rows to it at once and reads none (`docs/engineering/
database.md`); every existing row is null, so it passes.

**Compatibility and backfill.** None: no row was withheld before this.

**Rollback and recovery.** `downgrade` drops the columns. Rows withheld until then lose
why, keep their words in `outcome_reason`, and are never released: their subjects'
next messages still go. Nothing here exists only in these columns.

**Tenant, retention, and security impact.** None new: the columns hold a fixed word,
ids of the same organization's subjects, and a time. Rows keep their `org_id` and
cascade with it.

Revision ID: 0063
Revises: 0061
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0063"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# A frozen copy of `nexovue_platform.alerts.model.WITHHELD_REASONS`, held equal to it by
# `test_alert_vocabulary`.
_WITHHELD_REASONS = "'same_news', 'service_snoozed'"
WITHHELD_WHY_CHECK = "withheld_why_is_known"


def upgrade() -> None:
    op.add_column("notifications", sa.Column("withheld_why", sa.Text(), nullable=True))
    op.add_column("notifications", sa.Column("withheld_by_incident_id", _UUID, nullable=True))
    op.add_column("notifications", sa.Column("withheld_by_finding_id", _UUID, nullable=True))
    op.add_column("notifications", sa.Column("released_at", _TIMESTAMP, nullable=True))
    op.create_check_constraint(
        WITHHELD_WHY_CHECK,
        "notifications",
        f"withheld_why IN ({_WITHHELD_REASONS})",
        postgresql_not_valid=True,
    )


def downgrade() -> None:
    op.drop_constraint(WITHHELD_WHY_CHECK, "notifications", type_="check")
    for column in (
        "released_at",
        "withheld_by_finding_id",
        "withheld_by_incident_id",
        "withheld_why",
    ):
        op.drop_column("notifications", column)
