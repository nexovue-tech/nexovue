"""When a suggestion's branch was deleted on GitHub

F5 (`docs/features/ai-solution/`), live test A14 (readiness R1): after **Delete this branch**
the page kept offering it, because the deletion was recorded only in the audit log
(`fix_writer._delete`). A second click did nothing harmful, the head being gone, but the page
said something false.

**Why this change is required.** The read decides whether to offer the action from the row
(`fix_draft_read._delete_action`), and the row did not know. It now records when the job
deleted the branch, and the read and the route both refuse a branch already deleted.

**Affected store and schema.** Postgres: one nullable column, `finding_fixes.
pull_request_branch_deleted_at`. `scheduler-connections` may update it; its grants are applied
on each start from `nexovue_api.connections_account`.

**Compatibility and backfill.** None: nexovue is pre-deployment. A branch deleted before this
migration stays offered once more; deleting it again finds no head and changes nothing.

**Rollback and recovery.** A downgrade drops the column, and the button is offered again after
a deletion, as before.

**Tenant, retention, and security impact.** A time on an organization's own row, read and
written with its `org_id`. No code, no person, no secret.

Revision ID: 0087
Revises: 0086
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0087"
down_revision: str | None = "0086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "finding_fixes",
        sa.Column("pull_request_branch_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("finding_fixes", "pull_request_branch_deleted_at")
