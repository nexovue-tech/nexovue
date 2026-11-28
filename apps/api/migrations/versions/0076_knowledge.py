"""Runbooks and chat's knowledge chunks, with keyword search and no vector column

F4 (`docs/features/ai-chatbot/`), plan Step 5 (T5.1; design section 6, *Data and migration
impact*; ADR-067).

**Why this change is required.** Chat answers "what do we do when…" from runbooks a team
wrote and from what fixed a problem before. `runbooks` holds a project's runbooks;
`knowledge_chunks` holds what chat searches: a runbook's pieces, written in the runbook's own
transaction, and a fact line nexovue composes from a recovered finding, written by the
scheduler's knowledge sweep. `content_tsv` is generated from the text, so keyword search
needs no writer of its own and cannot drift from it.

**Affected store and schema.** Postgres, two new tables:

- `runbooks`: title, optional service, text (at most 32 KiB, checked here as the service
  does), author (`SET NULL` when the person goes), times. `UNIQUE (org_id, project_id, id)`
  so a chunk names its runbook with its project, and the database refuses a chunk in another
  project than its runbook.
- `knowledge_chunks`: `source_kind` (runbook or fact), and **one nullable foreign key per
  source, each `ON DELETE CASCADE`** (design, *Why three nullable foreign keys*): deleting a
  runbook, or a finding by any path including the expiry sweep's bulk deletion, takes its
  chunks in the same statement (BR16, AC38). A check holds exactly the key its kind names. A
  fact line carries its service, signal and day, and a partial unique index (nulls not
  distinct, for a project finding's null service) holds one per service, signal and day.
  `embedded_model` and `embedding_due` wait for Step 6; the `embedding` column itself is
  added by `setup`, never here (design, *Extension*), so this chain never needs `vector`.
- Indexes: GIN on `content_tsv`; `(org_id, project_id, embedded_model)`; a partial index
  where `embedding_due`; each source key, which also serves its cascade.

**Compatibility and backfill.** None: new tables. Notes join in Step 7 with `note_id`.

**Rollback and recovery.** Roll forward only: runbooks are what people wrote, found nowhere
else. Going back past 0076 is a restore.

**Tenant, retention, and security impact.** Every row carries `org_id` and goes with its
project (`organizations/tables.py`); rows of a deleted project or organization go at the next
expiry sweep (`nexovue_platform.knowledge.EXPIRED`). Runbook text never passes intake
masking; FR40's send-time mask and the save-time credential warning are its only net. The AI
account gets no grant here: search runs in the API on the question's ticket (D31), and
Step 6's embedding grant comes with `setup`.

Revision ID: 0076
Revises: 0075
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0076"
down_revision: str | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies of `nexovue_platform.knowledge`, held equal by `test_knowledge_records.py`:
# a later change to them is a migration of its own.
_SOURCE_KINDS = "'runbook', 'fact'"
_MAX_RUNBOOK_BYTES = 32 * 1024
_MAX_TITLE = 200
_MAX_SERVICE = 200
_MAX_CHUNK = 2_000
_MAX_POSITION = 17


def _org() -> sa.Column[uuid.UUID]:
    return sa.Column(
        "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "runbooks",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        _org(),
        sa.Column("project_id", _UUID, nullable=False),
        sa.Column("service_name", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by", _UUID, nullable=True),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_runbooks_project",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (created_by)",
            name="fk_runbooks_created_by",
        ),
        sa.UniqueConstraint("org_id", "project_id", "id", name="uq_runbooks_org_id_project_id_id"),
        sa.CheckConstraint(f"char_length(title) BETWEEN 1 AND {_MAX_TITLE}", name="title_length"),
        sa.CheckConstraint(
            f"service_name IS NULL OR char_length(service_name) BETWEEN 1 AND {_MAX_SERVICE}",
            name="service_name_length",
        ),
        sa.CheckConstraint(
            f"octet_length(content) BETWEEN 1 AND {_MAX_RUNBOOK_BYTES}", name="content_size"
        ),
    )
    op.create_index(
        "ix_runbooks_org_id_project_id_updated_at",
        "runbooks",
        ["org_id", "project_id", sa.text("updated_at DESC")],
    )
    # A person's export reads the runbooks they wrote (AC34).
    op.create_index("ix_runbooks_org_id_created_by", "runbooks", ["org_id", "created_by"])

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        _org(),
        sa.Column("project_id", _UUID, nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("runbook_id", _UUID, nullable=True),
        sa.Column("finding_id", _UUID, nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', content)", persisted=True),
            nullable=False,
        ),
        sa.Column("fact_service", sa.Text(), nullable=True),
        sa.Column("fact_signal", sa.Text(), nullable=True),
        sa.Column("fact_day", sa.Date(), nullable=True),
        sa.Column("embedded_model", sa.Text(), nullable=True),
        sa.Column("embedding_due", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunks_project",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id", "runbook_id"],
            ["runbooks.org_id", "runbooks.project_id", "runbooks.id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunks_runbook",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id", "finding_id"],
            ["findings.org_id", "findings.project_id", "findings.id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunks_finding",
        ),
        sa.CheckConstraint(f"source_kind IN ({_SOURCE_KINDS})", name="source_kind_is_known"),
        # Exactly the source its kind names, so no chunk outlives its source by a null key.
        sa.CheckConstraint(
            "(source_kind = 'runbook' AND runbook_id IS NOT NULL AND finding_id IS NULL"
            " AND fact_signal IS NULL AND fact_day IS NULL AND fact_service IS NULL)"
            " OR (source_kind = 'fact' AND finding_id IS NOT NULL AND runbook_id IS NULL"
            " AND fact_signal IS NOT NULL AND fact_day IS NOT NULL)",
            name="one_source",
        ),
        sa.CheckConstraint(
            f"char_length(content) BETWEEN 1 AND {_MAX_CHUNK}", name="content_length"
        ),
        sa.CheckConstraint(f"position BETWEEN 0 AND {_MAX_POSITION}", name="position_in_range"),
    )
    op.create_index(
        "ix_knowledge_chunks_content_tsv",
        "knowledge_chunks",
        ["content_tsv"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_knowledge_chunks_org_id_project_id_embedded_model",
        "knowledge_chunks",
        ["org_id", "project_id", "embedded_model"],
    )
    op.create_index(
        "ix_knowledge_chunks_embedding_due",
        "knowledge_chunks",
        ["org_id", "project_id"],
        postgresql_where=sa.text("embedding_due"),
    )
    op.create_index(
        "ix_knowledge_chunks_runbook",
        "knowledge_chunks",
        ["org_id", "project_id", "runbook_id", "position"],
        postgresql_where=sa.text("runbook_id IS NOT NULL"),
    )
    # One chunk a finding; also what its cascade looks rows up by.
    op.create_index(
        "uq_knowledge_chunks_finding",
        "knowledge_chunks",
        ["org_id", "project_id", "finding_id"],
        unique=True,
        postgresql_where=sa.text("finding_id IS NOT NULL"),
    )
    # One fact line a service, signal and day (FR28); a project finding's service is null.
    op.create_index(
        "uq_knowledge_chunks_fact_per_day",
        "knowledge_chunks",
        ["org_id", "project_id", "fact_service", "fact_signal", "fact_day"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("source_kind = 'fact'"),
    )


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: runbooks are what people wrote, found nowhere else. Going back "
        "past 0076 is a restore."
    )
