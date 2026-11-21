"""A finding's follow-up message, ratings of its explanation, and rules stopped from naming causes

F3 (`docs/features/ai-insights/`), plan Step 2 (T2.6, T2.12).

**Why this change is required.** Three things Step 2 adds need the database.

- **`explained`**, one follow-up message naming a finding's likely place to look, written
  from facts by template (spec FR21, FR22). The notification kinds are checked, so the
  check is widened, frozen here as `_NOTIFICATION_KINDS` (`test_alert_vocabulary.py`).
- **`notification_channels.sends_explained`**, the per-channel switch that turns it off
  (FR48). True by default: a channel people read gets the follow-up unless someone says no.
- **`finding_explanation_ratings`**, "Was this useful?" (FR20): one per person per version,
  `helpful`, and a reason from a closed list, never free text (A21).
  **`explanation_stopped_rules`**, the rules an organization has stopped from naming a
  cause (FR54, A64). A stopped rule's facts stay; it names no cause there.

**Affected store and schema.** Postgres: one check widened, one column, two new tables.

**Compatibility and backfill.** None needed. The column's default is a constant, so
Postgres adds it without rewriting the table. The kind check is added `NOT VALID` and then
validated, so the table is never locked against writes while existing rows are checked.

**Rollback and recovery.** Roll forward only: ratings are people's judgements and stopped
rules their decisions, found nowhere else. Going back past 0067 is a restore.

**Tenant, retention, and security impact.** A rating's composite key to its explanation
starts with `org_id`, so the database refuses a rating of another organization's
version; it cascades with the version, and so with the finding and its retention. A
person's deletion keeps their rating with no name (`SET NULL`), as an audit row does.
Stopped rules belong to the organization and are deleted with it
(`organizations/tables.py`). Neither table holds a name or free text.

Revision ID: 0067
Revises: 0066
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies, held equal to `nexovue_platform` by `test_alert_vocabulary.py` and
# `test_evidence_vocabulary.py`: a later change to them is a migration of its own.
_NOTIFICATION_KINDS = (
    "'opened', 'escalated', 'resolved', 'repeated', 'flapping', 'closed', 'joined', "
    "'adopted', 'stopped', 'running_again', 'explained'"
)
_RATING_REASONS = "'wrong_cause', 'not_useful', 'bad_wording'"
_CAUSE_RULES = (
    "'signed_deploy', 'dependency_finding', 'signed_deploy_weak', 'dependency_deploy', "
    "'dependency_edge', 'traffic_rose', 'project_deploy', 'project_shared_dependency'"
)


def upgrade() -> None:
    op.drop_constraint("kind_is_known", "notifications", type_="check")
    op.create_check_constraint(
        "kind_is_known",
        "notifications",
        f"kind IN ({_NOTIFICATION_KINDS})",
        postgresql_not_valid=True,
    )
    op.execute("ALTER TABLE notifications VALIDATE CONSTRAINT ck_notifications_kind_is_known")

    op.add_column(
        "notification_channels",
        sa.Column("sends_explained", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "finding_explanation_ratings",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("explanation_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("helpful", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["org_id", "explanation_id"],
            ["finding_explanations.org_id", "finding_explanations.id"],
            ondelete="CASCADE",
            name="fk_finding_explanation_ratings_explanation",
        ),
        # One rating per person per version; a change replaces it.
        sa.UniqueConstraint(
            "explanation_id", "user_id", name="uq_finding_explanation_ratings_person"
        ),
        sa.CheckConstraint(
            f"reason IS NULL OR reason IN ({_RATING_REASONS})", name="reason_is_known"
        ),
        # "Yes" carries no reason; "No" always carries one (FR20).
        sa.CheckConstraint("helpful = (reason IS NULL)", name="reason_only_when_not_helpful"),
    )
    op.create_index(
        "ix_finding_explanation_ratings_org_id", "finding_explanation_ratings", ["org_id"]
    )

    op.create_table(
        "explanation_stopped_rules",
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column(
            "created_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("org_id", "rule", name="pk_explanation_stopped_rules"),
        sa.CheckConstraint(f"rule IN ({_CAUSE_RULES})", name="rule_is_known"),
    )


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: finding_explanation_ratings holds people's judgements of an "
        "explanation, and explanation_stopped_rules their decisions, found nowhere else. "
        "Going back past 0067 is a restore."
    )
