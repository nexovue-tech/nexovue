"""notifications.sequence: an integer, so a sequence that only grows never runs out

F6 (`docs/features/anomaly-detection/`), plan Step 4, after T4.6.

**Why this change is required.** `sequence` tells apart the notifications of one
subject, channel and kind that are each meant to be sent: an incident's repeats
(at most five), a finding's joined signals, and — since D24 — each time detection
stops or runs again for a project. That last one has no ceiling: a project that
keeps stopping counts on for as long as notifications are kept, and nothing
prunes them. As a `smallint` the 32,768th stop could not be recorded, and the
owners would stop hearing about a project that keeps failing.

**Affected store and schema.** Postgres: `notifications.sequence` from `smallint`
to `integer`.

**Cost, and why it is accepted.** The two types are stored differently, so
Postgres rewrites the whole table and rebuilds every index on it, including the
incident key that names `sequence`, while holding an ACCESS EXCLUSIVE lock: no
delivery is claimed, recorded or read until it ends, for as long as copying every
notification takes. Done later, on an install with a long outbox, that would be a
delivery outage. nexovue is pre-deployment, so no install holds notifications yet
and the rewrite costs nothing now; it is accepted here because once there are
installs it would not be. The finding and project keys that also name `sequence`
are built after it, in 0050, so they are not rebuilt here.

**Compatibility and backfill.** Every stored value fits in an `integer`; the
rewrite copies them unchanged.

**Rollback and recovery.** `downgrade` narrows the column back, with the same
rewrite and lock, and refuses while any row holds a value a `smallint` cannot,
rather than losing it.

**Tenant, retention, and security impact.** None: the column's meaning, scope and
retention are unchanged.

Revision ID: 0043
Revises: 0042
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "notifications",
        "sequence",
        existing_type=sa.SmallInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )


def downgrade() -> None:
    op.alter_column(
        "notifications",
        "sequence",
        existing_type=sa.Integer(),
        type_=sa.SmallInteger(),
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )
