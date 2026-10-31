"""ix_notifications_channel_id_dedup_key: a claim finds a pinned PagerDuty key by an index

Follows 0044 (F6, D35), which added `notifications.dedup_key` without an index.

**Why this change is required.** Each claim of the outbox (`nexovue_scheduler.alerts.outbox`)
looks a key up within one channel: the key a subject's earlier rows went under, and whether an
alert still carries another subject that is open (`member.channel_id = … AND member.dedup_key = …`).
Through `ix_notifications_channel_id_state` that reads every row a channel has ever had, so a
claim slows as a channel's history grows.

**Affected store and schema.** Postgres: `ix_notifications_channel_id_dedup_key` on
`notifications (channel_id, dedup_key) WHERE dedup_key IS NOT NULL`. The channel leads because
every lookup names it; only keyed rows are indexed because every lookup either compares the key
with `=` or asks for it to be set, and rows delivered before 0044 keep none.

**Compatibility and backfill.** None needed: an index changes no result. It is built
`CONCURRENTLY` in an autocommit block, so writes to `notifications` continue during the build
(`docs/engineering/database.md`).

**Rollback and recovery.** `downgrade` drops the index, also concurrently. A build that fails,
for instance on `lock_timeout` behind a long transaction, leaves an invalid index behind; the
upgrade drops any such leftover first, so running it again when the table is quiet recovers.

**Tenant, retention, and security impact.** None: no data is added, and every lookup still
names `org_id`.

Revision ID: 0045
Revises: 0044
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_notifications_channel_id_dedup_key"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # An earlier failed build leaves an invalid index of this name, which would make
        # CREATE fail with "already exists" rather than build it again.
        op.drop_index(INDEX, "notifications", postgresql_concurrently=True, if_exists=True)
        op.create_index(
            INDEX,
            "notifications",
            ["channel_id", "dedup_key"],
            postgresql_where=sa.text("dedup_key IS NOT NULL"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(INDEX, "notifications", postgresql_concurrently=True)
