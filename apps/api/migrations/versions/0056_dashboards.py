"""Dashboards and the versions a save replaced

F20 (`docs/features/dashboards/`), plan T2.2. Numbered 0056 after the revisions F22
and F28 took in parallel; it revises 0055 (F22's connections).

**Why this change is required.** A dashboard is a saved, checked JSON document of
fixed panel kinds, owned by the organization and shown against a named project
(design, *Data and migration impact*; spec BR1, D4). Any working member may change
any dashboard, which is safe only with a way back (D19), so each save keeps the
version it replaced and a deletion can be undone for 30 days.

**Affected store and schema.** Postgres, two new tables:

- `dashboards`: the definition, its optimistic-lock `version` (FR19), who made and
  last changed it, when a batch read last opened it (FR37), and four columns written
  at save from the compiled definition, so list routes never read the JSON (AC96):
  `panel_count`, `service_names` (GIN, for "Dashboards showing this service"),
  `uses_picker` and `panels_not_reading`. Soft-deleted by `deleted_at`. A partial
  unique index keeps live titles unique ignoring case (BR12); `UNIQUE (org_id, id)`
  lets versions key on both, so the database refuses a version filed under another
  tenant's dashboard.
- `dashboard_versions`: the replaced title, description and definition, with who
  saved it and when, keyed by dashboard and version.

**Compatibility and backfill.** None. New tables; no existing rows.

**Rollback and recovery.** Roll forward only, by `docs/engineering/database.md`'s
rule: both tables hold what people built, which exists nowhere else. The design said
the downgrade drops both; that conflict is an open decision in F20's `status.md`.
Going back past this revision is a restore.

**Tenant, retention, and security impact.** Both carry `org_id`. `dashboards` is
deleted directly with the organization and its versions cascade
(`organizations/tables.py`); a project's deletion leaves dashboards alone. People's
ids are kept on a member's removal, which scrubs the person, and set null only when
the person's row goes with organization erasure. Titles, descriptions, notes and
link labels are free text a person typed and may hold a pasted secret: purge (Step 7)
is the path that removes them at once. Deleted dashboards expire after 30 days by
the scheduler's sweep (Step 7).

Revision ID: 0056
Revises: 0055
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies of `models.DASHBOARD_*_MAX_LENGTH`: a later change is a migration of its own.
_TITLE_LONGEST = 120
_DESCRIPTION_LONGEST = 500


def upgrade() -> None:
    _dashboards()
    _dashboard_versions()


def _dashboards() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", _UUID, nullable=True),
        sa.Column("updated_by", _UUID, nullable=True),
        sa.Column("last_viewed_at", _TIMESTAMP, nullable=True),
        sa.Column("panel_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "service_names",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("uses_picker", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("panels_not_reading", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", _TIMESTAMP, nullable=True),
        sa.UniqueConstraint("org_id", "id", name="uq_dashboards_org_id_id"),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (created_by)",
            name="fk_dashboards_created_by",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "updated_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (updated_by)",
            name="fk_dashboards_updated_by",
        ),
        sa.CheckConstraint("jsonb_typeof(definition) = 'object'", name="definition_is_an_object"),
        sa.CheckConstraint(
            f"char_length(title) BETWEEN 1 AND {_TITLE_LONGEST}", name="title_length"
        ),
        sa.CheckConstraint(
            f"char_length(description) <= {_DESCRIPTION_LONGEST}", name="description_length"
        ),
    )
    # BR12: a title once among the organization's live dashboards, ignoring case.
    op.create_index(
        "uq_dashboards_live_title",
        "dashboards",
        ["org_id", sa.text("lower(title)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # The list sorted by last opened (FR36).
    op.create_index(
        "ix_dashboards_org_id_last_viewed_at", "dashboards", ["org_id", "last_viewed_at"]
    )
    # "Dashboards showing this service" (FR57), without parsing definitions.
    op.create_index(
        "ix_dashboards_service_names", "dashboards", ["service_names"], postgresql_using="gin"
    )


def _dashboard_versions() -> None:
    op.create_table(
        "dashboard_versions",
        sa.Column("dashboard_id", _UUID, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("org_id", _UUID, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("saved_by", _UUID, nullable=True),
        sa.Column("saved_at", _TIMESTAMP, nullable=False),
        sa.PrimaryKeyConstraint("dashboard_id", "version", name="pk_dashboard_versions"),
        sa.ForeignKeyConstraint(
            ["org_id", "dashboard_id"],
            ["dashboards.org_id", "dashboards.id"],
            ondelete="CASCADE",
            name="fk_dashboard_versions_dashboard",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "saved_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (saved_by)",
            name="fk_dashboard_versions_saved_by",
        ),
        sa.CheckConstraint("jsonb_typeof(definition) = 'object'", name="definition_is_an_object"),
    )


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: dashboards and dashboard_versions hold what people built, which "
        "exists nowhere else. Going back past 0056 is a restore."
    )
