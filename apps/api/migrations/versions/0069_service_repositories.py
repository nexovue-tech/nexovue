"""Which repository a service's issues go to

F22 (`docs/features/case-management/`), plan T6.2; follows 0068.

**Why this change is required.** Creating an issue asks for a repository each time. A
mapping from a project's service to one of the connection's repositories lets the panel
preselect it (spec, *Scope*; design, *Connections*), and F5 reads the same mapping later
to find the service's code. It is optional: a service with no mapping still gets a
choice.

**Affected store and schema.** Postgres, one new table, `service_repositories`, the
foundation's name (section 8). One row per project and service, naming a *place*
(`connection_repositories`).

- Keyed `(org_id, project_id, service_name)`, so a service maps to one place at most.
- `(org_id, project_id)` refers to `projects` through 0068's index, and `(org_id,
  place_id)` to `connection_repositories`: the database refuses a mapping that crosses
  organizations either way (AC72).
- The service name is the telemetry's own, as `findings.service_name` holds it, at most
  255 characters. It is never sent anywhere: an issue carries the strict form alone.

**Compatibility and backfill.** None: a new table, and nothing is mapped.

**Rollback and recovery.** The downgrade drops the table. A mapping is a convenience an
admin sets again in a minute, and no case or link depends on it, as 0055's connections
are set up afresh.

**Tenant, retention, and security impact.** `org_id` leads each key. A mapping goes with
its project, and with its repository when the connection that reaches it goes
(`organizations/tables.py`). No secret, and no text from a tracker.

Revision ID: 0069
Revises: 0068
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)
# `models.SERVICE_NAME_LENGTH`, frozen here: a later change to it is a migration of its own.
_SERVICE_NAME_LENGTH = 255


def upgrade() -> None:
    op.create_table(
        "service_repositories",
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("project_id", _UUID, nullable=False),
        sa.Column("service_name", sa.String(_SERVICE_NAME_LENGTH), nullable=False),
        sa.Column("place_id", _UUID, nullable=False),
        sa.Column("changed_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint(
            "org_id", "project_id", "service_name", name="pk_service_repositories"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_service_repositories_project",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "place_id"],
            ["connection_repositories.org_id", "connection_repositories.id"],
            ondelete="CASCADE",
            name="fk_service_repositories_place",
        ),
        sa.CheckConstraint("length(service_name) > 0", name="service_is_named"),
    )
    # A place's deletion finds its mappings by this, not by a scan.
    op.create_index(
        "ix_service_repositories_org_id_place_id", "service_repositories", ["org_id", "place_id"]
    )


def downgrade() -> None:
    op.drop_table("service_repositories")
