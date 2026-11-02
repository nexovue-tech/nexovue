"""findings.flapping: a service whose findings keep opening is said once

F6 (`docs/features/anomaly-detection/`), plan task T4.2, design *Notifications*,
the flap guard (C7).

**Why this change is required.** F21's guard says a subject that opens a fifth
time within an hour once, as *flapping*, and then nothing until it has stayed
recovered for 30 minutes (`alerts/incidents.py`). It keeps that on the record,
as `incidents.flapping`. Findings take the same guard per project and service
(C7), and a later run of the project job must know that a finding it is
escalating, joining or closing is the flapping one, so the finding records it
too. Nothing else holds it: a project finding has no per-service state row, and
the guard must work when no rule has any channel, so the outbox cannot hold it.

**Affected store and schema.** Postgres: one boolean column on `findings`,
false by default.

**Compatibility and backfill.** Every existing finding takes false, which is
what it was: no guard existed before this revision.

**Rollback and recovery.** `downgrade` drops the column; nothing is lost but
which findings were flapping.

**Tenant, retention, and security impact.** None: a flag on a row already
scoped by `org_id` and removed with its project.

Revision ID: 0042
Revises: 0041
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column("flapping", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("findings", "flapping")
