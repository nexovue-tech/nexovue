"""People's case notes join chat's knowledge, each one chunk keyed by its note

F4 (`docs/features/ai-chatbot/`), plan Step 7 (T7.1; design section 6, *Why three nullable
foreign keys*; ADR-088).

**Why this change is required.** Chat answers "how did we fix this last time?" from what
people wrote on a case (FR28, UR9). The scheduler's knowledge sweep copies each note of an
organization with chat on into one chunk (FR30, D58), so `knowledge_chunks` gains `note_id`,
a third nullable source key that **cascades from `finding_notes`**: deleting a note, or its
finding by any path including the expiry sweep's bulk deletion, takes its chunk in the same
statement (BR16, AC38), with no code to forget. F22's note service does not change.

**Affected store and schema.** Postgres.

- `finding_notes` gains `UNIQUE (org_id, id)`, so a chunk can name its note with its
  organization and the database refuses a chunk naming another organization's note (D34).
  Built concurrently and taken over by the constraint, as 0051 did, since the table holds rows.
- `knowledge_chunks` gains `note_id`, its composite key to that constraint `ON DELETE CASCADE`,
  and a unique index on it (one chunk a note), built concurrently.
- `source_kind` gains `note`; the check that a chunk carries exactly the key its kind names
  gains the note's case; a note chunk may hold a whole note (F22's 4,000 characters), while a
  runbook's or a fact's stays at 2,000. Each check is replaced `NOT VALID`, then validated, so
  the table is not locked against writes while existing rows are checked.

**Compatibility and backfill.** None: no note chunk exists; the sweep writes them for
organizations with chat on within one pass.

**Rollback and recovery.** A downgrade deletes the note chunks, which are copies the sweep can
write again from the notes, and restores 0076's checks. Nothing is lost that exists nowhere
else.

**Tenant, retention, and security impact.** A note chunk carries its note's organization by
key, and the project the sweep reads from the note's finding; it goes with its note, its
finding and its project. A note never passes intake masking: FR40's send-time mask is applied
before it is embedded or sent, as for runbooks. The AI account gains no grant: it reads chunk
text only to embed it, through its existing column grants, never `note_id`.

Revision ID: 0080
Revises: 0079
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0080"
down_revision: str | None = "0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of `nexovue_platform.knowledge` and `nexovue_platform.cases`, held equal by
# `test_knowledge_records.py`: a later change to them is a migration of its own.
_SOURCE_KINDS = "'runbook', 'fact', 'note'"
_MAX_CHUNK = 2_000
_MAX_NOTE_CHUNK = 4_000

_NOTE_KEY = "uq_finding_notes_org_id_id"
_NOTE_INDEX = "uq_knowledge_chunks_note"

_ONE_SOURCE = (
    "(source_kind = 'runbook' AND runbook_id IS NOT NULL AND finding_id IS NULL"
    " AND note_id IS NULL"
    " AND fact_signal IS NULL AND fact_day IS NULL AND fact_service IS NULL)"
    " OR (source_kind = 'fact' AND finding_id IS NOT NULL AND runbook_id IS NULL"
    " AND note_id IS NULL AND fact_signal IS NOT NULL AND fact_day IS NOT NULL)"
    " OR (source_kind = 'note' AND note_id IS NOT NULL AND runbook_id IS NULL"
    " AND finding_id IS NULL"
    " AND fact_signal IS NULL AND fact_day IS NULL AND fact_service IS NULL)"
)
_CONTENT_LENGTH = (
    "char_length(content) BETWEEN 1 AND "
    f"(CASE WHEN source_kind = 'note' THEN {_MAX_NOTE_CHUNK} ELSE {_MAX_CHUNK} END)"
)
# 0076's, restored by a downgrade.
_OLD_SOURCE_KINDS = "'runbook', 'fact'"
_OLD_ONE_SOURCE = (
    "(source_kind = 'runbook' AND runbook_id IS NOT NULL AND finding_id IS NULL"
    " AND fact_signal IS NULL AND fact_day IS NULL AND fact_service IS NULL)"
    " OR (source_kind = 'fact' AND finding_id IS NOT NULL AND runbook_id IS NULL"
    " AND fact_signal IS NOT NULL AND fact_day IS NOT NULL)"
)
_OLD_CONTENT_LENGTH = f"char_length(content) BETWEEN 1 AND {_MAX_CHUNK}"


def _replace_check(name: str, condition: str) -> None:
    """Swap a check without holding the table against writes while rows are read: the new
    one is added `NOT VALID`, then validated, which takes a weaker lock."""
    op.drop_constraint(name, "knowledge_chunks", type_="check")
    # Raw SQL takes no naming convention, so the name is spelled as the metadata's.
    named = f"ck_knowledge_chunks_{name}"
    op.execute(
        sa.text(
            f"ALTER TABLE knowledge_chunks ADD CONSTRAINT {named} CHECK ({condition}) NOT VALID"
        )
    )
    op.execute(sa.text(f"ALTER TABLE knowledge_chunks VALIDATE CONSTRAINT {named}"))


def upgrade() -> None:
    with op.get_context().autocommit_block():
        already = op.get_bind().execute(
            sa.text("SELECT 1 FROM pg_constraint WHERE conname = :name"), {"name": _NOTE_KEY}
        )
        if already.scalar() is None:
            op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_NOTE_KEY}"))
            op.execute(
                sa.text(
                    f"CREATE UNIQUE INDEX CONCURRENTLY {_NOTE_KEY} ON finding_notes (org_id, id)"
                )
            )
            op.execute(
                sa.text(
                    f"ALTER TABLE finding_notes ADD CONSTRAINT {_NOTE_KEY} "
                    f"UNIQUE USING INDEX {_NOTE_KEY}"
                )
            )

    op.add_column("knowledge_chunks", sa.Column("note_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_knowledge_chunks_note",
        "knowledge_chunks",
        "finding_notes",
        ["org_id", "note_id"],
        ["org_id", "id"],
        ondelete="CASCADE",
    )
    _replace_check("source_kind_is_known", f"source_kind IN ({_SOURCE_KINDS})")
    _replace_check("one_source", _ONE_SOURCE)
    _replace_check("content_length", _CONTENT_LENGTH)

    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_NOTE_INDEX}"))
        # One chunk a note; also what the note's cascade looks rows up by.
        op.create_index(
            _NOTE_INDEX,
            "knowledge_chunks",
            ["org_id", "note_id"],
            unique=True,
            postgresql_where=sa.text("note_id IS NOT NULL"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM knowledge_chunks WHERE source_kind = 'note'"))
    op.drop_index(_NOTE_INDEX, table_name="knowledge_chunks")
    _replace_check("content_length", _OLD_CONTENT_LENGTH)
    _replace_check("one_source", _OLD_ONE_SOURCE)
    _replace_check("source_kind_is_known", f"source_kind IN ({_OLD_SOURCE_KINDS})")
    op.drop_constraint("fk_knowledge_chunks_note", "knowledge_chunks", type_="foreignkey")
    op.drop_column("knowledge_chunks", "note_id")
    op.drop_constraint(_NOTE_KEY, "finding_notes", type_="unique")
