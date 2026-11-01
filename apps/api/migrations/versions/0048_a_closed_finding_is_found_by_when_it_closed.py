"""ix_findings_closed_at: the expiry sweep finds closed findings by when they closed

F6 (`docs/features/anomaly-detection/`), plan task T6.3, design revision 2.1,
security m4.

**Why this change is required.** A finding keeps telemetry text, service and
endpoint names in its title and signals, so a closed one is purged once its
project's derived retention has passed (`nexovue_scheduler.detection.retention`).
The expiry sweep asks for that every minute, and without an index each ask reads
every finding of every tenant.

**Affected store and schema.** Postgres: `ix_findings_closed_at` on
`findings (closed_at) WHERE state = 'closed'`. Only closed findings have a
`closed_at` (the `closed_says_when` check), and only they are purged.

**Compatibility and backfill.** None needed: an index changes no result. It is built
`CONCURRENTLY` in an autocommit block, so writes to `findings` continue during the
build (`docs/engineering/database.md`).

**Rollback and recovery.** `downgrade` drops the index, also concurrently. A build
that fails, for instance on `lock_timeout` behind a long transaction, leaves an
invalid index behind; the upgrade drops any such leftover first, so running it
again when the table is quiet recovers.

**Tenant, retention, and security impact.** None by itself: it is what lets the
purge that enforces the derived retention on findings run cheaply.

Revision ID: 0048
Revises: 0047
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_findings_closed_at"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # An earlier failed build leaves an invalid index of this name, which would make
        # CREATE fail with "already exists" rather than build it again.
        op.drop_index(INDEX, "findings", postgresql_concurrently=True, if_exists=True)
        op.create_index(
            INDEX,
            "findings",
            ["closed_at"],
            postgresql_where=sa.text("state = 'closed'"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(INDEX, "findings", postgresql_concurrently=True)
