"""A finding's frozen evidence, and the hour's error-group read

F3 (`docs/features/ai-insights/`), plan T1.1.

**Why this change is required.** Every new finding gets an explanation: the facts that
line up with it, each with a stable id and what it rests on, and a likely cause chosen
by fixed rules, or none (spec FR1 to FR13). The facts are read once and kept, because
the readings and raw data they come from age out on other clocks (design, *Evidence is
read once and kept*). A read that finds nothing new writes nothing (FR43), so a version
is a change in what is known, numbered per finding.

**Affected store and schema.** Postgres, two new tables.

- `finding_explanations`: one row per version. `evidence` holds names and numbers and
  never a log body or error text (A47). `aliases` holds the stand-in names a model may
  see in place of real ones; `model_input` holds only numbers, closed-list members and
  those stand-ins (A19). The wording columns exist from now so the record never changes
  shape when AI wording arrives (plan Step 5); until then every row is `off`.
  `read_again_at` holds a person's last "Read the facts again", which may come once a
  minute per finding (FR41); the API answers a sooner one with 409 and the time.
  `wording_state` and `wording_reason` carry `CHECK`s, frozen copies of
  `nexovue_platform.evidence`'s words, because an account other than the API will write
  them (AC96).
- `evidence_error_group_reads`: the most expensive read, "error groups not seen in the
  hour before", kept for an hour per project and minute, so a wide outage reads it once
  (A62). It holds group ids, checked exception types, services and counts, never a
  message (A47).

**Compatibility and backfill.** None: new tables. Explanations cover findings opened
from now on; the frozen record needs reads made at the time, so nothing is backfilled.

**Rollback and recovery.** The downgrade drops both tables. What they hold is derived
from the stores and the finding, at the moment of reading; nexovue is pre-deployment.

**Tenant, retention, and security impact.** `org_id` leads the composite key to the
finding, so the database refuses a version for another organization's finding. A
version cascades with its finding and so follows the finding's retention
(`detection/retention.py`); an error-group read expires after an hour
(`evidence/retention.py`). Both are listed in `organizations/tables.py`.

Revision ID: 0066
Revises: 0065
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies of `nexovue_platform.evidence`'s words, held equal to it by
# `test_evidence_vocabulary.py`: a later change to them is a migration of its own.
_WORDING_STATES = "'off', 'pending', 'done', 'failed', 'over_limit', 'abstained', 'held'"
_WORDING_REASONS = (
    "'died', 'expired', 'run_cap', 'too_long', 'bad_key', 'unavailable', 'limited', "
    "'refused', 'invalid'"
)


def upgrade() -> None:
    op.create_table(
        "finding_explanations",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("finding_id", _UUID, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("read_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        # SHA-256 over identities and conclusions only (FR43).
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("reads_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), nullable=False),
        sa.Column("likely_cause", postgresql.JSONB(), nullable=True),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("model_input", postgresql.JSONB(), nullable=True),
        sa.Column("wording", postgresql.JSONB(), nullable=True),
        sa.Column("wording_state", sa.Text(), nullable=False, server_default="off"),
        sa.Column("wording_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wording_changed_at", _TIMESTAMP, nullable=True),
        sa.Column("wording_reason", sa.Text(), nullable=True),
        sa.Column("wording_from_version", sa.Integer(), nullable=True),
        # When a person last asked for the facts to be read again (FR41: once a minute).
        sa.Column("read_again_at", _TIMESTAMP, nullable=True),
        sa.UniqueConstraint("org_id", "id", name="uq_finding_explanations_org_id_id"),
        # One row per version; also the feed's lateral read of the latest (design, *API*).
        sa.UniqueConstraint("finding_id", "version", name="uq_finding_explanations_version"),
        sa.ForeignKeyConstraint(
            ["org_id", "finding_id"],
            ["findings.org_id", "findings.id"],
            ondelete="CASCADE",
            name="fk_finding_explanations_finding",
        ),
        sa.CheckConstraint("version >= 1", name="version_counts_from_one"),
        sa.CheckConstraint("reads_failed >= 0", name="reads_failed_count_up"),
        sa.CheckConstraint(f"wording_state IN ({_WORDING_STATES})", name="wording_state_is_known"),
        sa.CheckConstraint(
            f"wording_reason IS NULL OR wording_reason IN ({_WORDING_REASONS})",
            name="wording_reason_is_known",
        ),
        sa.CheckConstraint("wording_generation >= 0", name="wording_generation_counts_up"),
    )
    op.create_table(
        "evidence_error_group_reads",
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("read_minute", _TIMESTAMP, nullable=False),
        sa.Column("groups", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint(
            "org_id", "project_id", "read_minute", name="pk_evidence_error_group_reads"
        ),
    )
    # The hour's expiry reads by age alone.
    op.create_index(
        "ix_evidence_error_group_reads_created_at", "evidence_error_group_reads", ["created_at"]
    )


def downgrade() -> None:
    op.drop_table("evidence_error_group_reads")
    op.drop_table("finding_explanations")
