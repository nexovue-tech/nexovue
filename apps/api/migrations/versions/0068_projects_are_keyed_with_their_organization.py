"""A project is keyed with its organization, built without stopping writes

F22 (`docs/features/case-management/`), plan T6.2; follows 0067 (F3 Step 2).

**Why this change is required.** A service's repository mapping (0069) names a project
and a repository. The repository is keyed with its organization already, so the database
refuses a mapping to another organization's repository; the project must be keyed the
same way, or a direct insert could map one organization's repository to another's
project (AC72). A composite foreign key needs a unique index on `(org_id, id)` to refer to.

**Affected store and schema.** Postgres: `uq_projects_org_id_id`, a unique index on
`projects (org_id, id)`. `id` is already the primary key, so it cannot refuse a row
that exists today; it only gives the pair an index to point at.

**Locks.** `projects` holds rows, so the index is built `CONCURRENTLY`, in its own
migration, outside a transaction (`docs/engineering/database.md`). A failed build leaves
an invalid index behind, so one of this name is dropped first.

**Compatibility and backfill.** None: every existing row already satisfies it.

**Rollback and recovery.** `downgrade` drops the index concurrently, once 0069's table,
the only thing that refers to it, is gone.

**Tenant, retention, and security impact.** Tightens tenancy: a key to a project can now
carry its organization, and the database checks both.

Revision ID: 0068
Revises: 0067
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROJECT_KEY_INDEX = "uq_projects_org_id_id"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(PROJECT_KEY_INDEX, "projects", postgresql_concurrently=True, if_exists=True)
        op.create_index(
            PROJECT_KEY_INDEX,
            "projects",
            ["org_id", "id"],
            unique=True,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(PROJECT_KEY_INDEX, "projects", postgresql_concurrently=True)
