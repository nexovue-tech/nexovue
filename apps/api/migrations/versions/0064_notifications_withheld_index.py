"""The withheld notifications a release reads, indexed without stopping writes

F19 (`docs/features/slo-tracking/`), plan T5.1; follows 0063.

**Why this change is required.** Every claim of a channel, and every delivery sweep,
looks for that channel's withheld rows not yet released (FR23). Those are a few rows
among every notification the channel was ever sent, so they get a partial index of
their own. 0063's check on `withheld_why` was added `NOT VALID`; it is validated here.

**Affected store and schema.** Postgres: `ix_notifications_withheld_unreleased` on
`notifications (org_id, channel_id)`, over rows with `withheld_why` set and
`released_at` null; `VALIDATE CONSTRAINT ck_notifications_withheld_why_is_known`.

**Locks.** `notifications` holds rows, so the index is built `CONCURRENTLY` in its own
migration, outside a transaction (`docs/engineering/database.md`). A failed build leaves
an invalid index behind, so one of this name is dropped first. The validation holds
SHARE UPDATE EXCLUSIVE, which lets reads and writes go on, as 0059 does for 0058.

**Compatibility and backfill.** None: every row is null in 0063's columns.

**Rollback and recovery.** `downgrade` drops the index concurrently; the release then
reads the channel's rows without it, slower but the same. A validated check is the
same check, so validity is not undone.

**Tenant, retention, and security impact.** None: no data is added; every lookup still
names `org_id`.

Revision ID: 0064
Revises: 0063
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WITHHELD_INDEX = "ix_notifications_withheld_unreleased"
# 0063's NOT VALID check, by the name the naming convention gave it.
NOT_VALID_CHECK = "ck_notifications_withheld_why_is_known"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TABLE notifications VALIDATE CONSTRAINT {NOT_VALID_CHECK}")
        op.drop_index(WITHHELD_INDEX, "notifications", postgresql_concurrently=True, if_exists=True)
        op.create_index(
            WITHHELD_INDEX,
            "notifications",
            ["org_id", "channel_id"],
            postgresql_where=sa.text("withheld_why IS NOT NULL AND released_at IS NULL"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(WITHHELD_INDEX, "notifications", postgresql_concurrently=True)
