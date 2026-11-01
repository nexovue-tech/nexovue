"""detection_sweep: the detection sweep's own record, which *detection stalled* reads

F6 (`docs/features/anomaly-detection/`), plan task T7.1, design *Where it runs*
("it records its own finish") and *Observability and operations*.

**Why this change is required.** *Detection stalled* fires when the newest
minute detection has settled is more than 10 minutes old. Nothing stored that
minute: the sweep read it from ClickHouse and Kafka, handed it to the project
jobs and forgot it. Without a record, a scheduler whose minute sweep has stopped
is seen by nobody: the owners' *detection stopped* email is sent by the sweep
itself, and a quiet install has no project to fall behind.

**Affected store and schema.** Postgres: `detection_sweep`, at most one row. The
sweep writes it at the end of every run: when it ran, and the newest minute it
has handed to project jobs, which never moves backwards. Beat's leader reads its
age for `/metrics`.

**Compatibility and backfill.** None needed: the table starts empty, and an
empty table reads as a sweep that has never settled a minute, which is the truth.

**Rollback and recovery.** `downgrade` drops the table. The sweep writes the
row again within a minute of the upgrade being run again.

**Tenant, retention, and security impact.** None. The row describes nexovue's
pipeline, names no tenant and holds no customer data, so it carries no `org_id`
and is excluded from tenant deletion with that reason (`organizations/tables.py`).
It never grows past one row, so nothing prunes it.

Revision ID: 0049
Revises: 0048
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "detection_sweep",
        # Always true: the key that holds the table to one row.
        sa.Column("one_row", sa.Boolean(), primary_key=True, server_default=sa.text("true")),
        sa.Column("swept_at", _TIMESTAMP, nullable=False),
        sa.Column("settled_minute", _TIMESTAMP, nullable=True),
        sa.CheckConstraint("one_row", name="holds_one_row"),
    )


def downgrade() -> None:
    op.drop_table("detection_sweep")
