"""delivery log: an attempt says whether it carried a notification or a test

`docs/changes/console-review-fixes/`, finding D2.

**Why this change is required.** A channel's screen showed "Last delivered 21 hr.
ago" directly above a delivery log that promises *every* attempt of the last 30
days and listed none. Both were telling the truth about themselves: a *Send test*
healed the channel and stamped `last_delivered_at`, and deliberately wrote no
delivery-log row, because "a test is not a notification". A customer reading the
two together is simply told something false.

The log's promise is the one worth keeping — it is where somebody looks after
pressing *Send test* and seeing nothing arrive. So a test now records an attempt
like any other, and this column is what keeps the two kinds distinguishable
instead of leaving the screen to infer it from an attempt whose batch matches no
notification.

**Affected store and schema.** Postgres only: one column on
`notification_attempts`, with a CHECK holding it to the vocabulary in
`nexovue_platform.alerts.model`.

**Compatibility and backfill.** Every existing row was written by the outbox, so
the server default of `'notification'` is the correct value for all of them and
no backfill is needed. The default stays on the column: the outbox names the kind
explicitly, and a row that somehow omits it is a notification.

**Rollback and recovery.** `downgrade` drops the column. The test rows it
described remain in the log as ordinary attempts, which is what they were before
this change could distinguish them.

**Tenant, retention, and security impact.** None. The column holds one of two
fixed words, the table is already tenant-scoped and already pruned at 30 days.

Revision ID: 0036
Revises: 0035
Created: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen here, as 0028 froze the rest of the alert vocabulary, so a value added
# to the model without a migration fails `test_alert_vocabulary`.
_ATTEMPT_KINDS = "'notification', 'test'"


def upgrade() -> None:
    op.add_column(
        "notification_attempts",
        sa.Column("kind", sa.Text(), nullable=False, server_default="notification"),
    )
    op.create_check_constraint(
        "kind_is_known", "notification_attempts", f"kind IN ({_ATTEMPT_KINDS})"
    )


def downgrade() -> None:
    op.drop_constraint("kind_is_known", "notification_attempts", type_="check")
    op.drop_column("notification_attempts", "kind")
