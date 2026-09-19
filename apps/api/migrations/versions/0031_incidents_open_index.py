"""incidents: a partial index on the open ones

F21 (`docs/features/alerts/`).

**Why this change is required.** The sidebar shows how many incidents are open
beside Alerts, and asks on every screen every fifteen seconds. Open incidents are
a handful; closed ones accumulate for as long as the project exists. Without
this, `ix_incidents_project_id_opened_at` makes each count read the project's
whole history to find the few with no `resolved_at`.

**Affected store and schema.** Postgres only: `ix_incidents_project_id_open` on
`incidents (project_id) WHERE resolved_at IS NULL`. It serves the Alerts screen's
open list too.

**Compatibility and backfill.** None: an index, built from rows already there.
nexovue is pre-deployment, so a plain (not concurrent) build is correct.

**Rollback and recovery.** `downgrade` drops the index.

**Tenant, retention, and security impact.** None: every read through it still
names `org_id`.

Revision ID: 0031
Revises: 0030
Created: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_incidents_project_id_open",
        "incidents",
        ["project_id"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_incidents_project_id_open", table_name="incidents")
