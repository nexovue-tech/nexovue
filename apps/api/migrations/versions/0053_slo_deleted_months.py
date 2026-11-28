"""A purged reliability target's month results

F19 (`docs/features/slo-tracking/`), plan T3.1; follows F19's 0052.

**Why this change is required.** A deleted target can be restored for 30 days; then
its daily figures go (FR31). Its past months must still count, so "5 of 7 targets
met" cannot become "5 of 6" by deleting a miss (D46, AC112). The purge writes each
complete month's result here first, and the *Last month* view, the CSV and the copy
read it once the target's own rows are gone.

**Affected store and schema.** Postgres, one new table, `slo_deleted_months`: one row
per purged target and local month. It holds the figures a month line states (FR27,
FR27b): result, figure and objective, the counts, coverage, quiet, lost and excluded
time, a check's missed and maintenance runs, the days held, whether nexovue's losses
were known, the sampling answer, and the composed label (D43), so a report stays true
without the target. It holds no free text a person typed: the label is nexovue's.

**Compatibility and backfill.** None. A new table; no target has been purged yet.

**Rollback and recovery.** Roll forward only: these rows are the only record of a
purged target's months once its daily counts are gone. Going back past this
revision is a restore.

**Tenant, retention, and security impact.** Every row carries `org_id` and cascades
from its project (`organizations/tables.py`). No key to the target, which is gone.
Rows lapse 400 days after their month ends (`slos/retention.py`).

Revision ID: 0053
Revises: 0052
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies of `nexovue_platform.slo`'s names, held equal to it by
# `test_slo_model.py`: a later change to them is a migration of its own.
_KINDS = "'service_availability', 'service_latency', 'endpoint_availability', 'check_availability'"
_RESULTS = "'met', 'missed', 'not_judged'"
_SAMPLING = "'no', 'yes', 'not_sure'"


def upgrade() -> None:
    op.create_table(
        "slo_deleted_months",
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        # No foreign key: the target is purged when this row is written.
        sa.Column("slo_id", _UUID, nullable=False),
        # The first day of the project's local month.
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("objective_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("figure_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("good", sa.BigInteger(), nullable=False),
        sa.Column("total", sa.BigInteger(), nullable=False),
        sa.Column("coverage", sa.Float(), nullable=True),
        sa.Column("quiet_minutes", sa.Integer(), nullable=False),
        sa.Column("longest_quiet_minutes", sa.Integer(), nullable=False),
        sa.Column("lost_minutes", sa.Integer(), nullable=False),
        sa.Column("excluded_minutes", sa.Integer(), nullable=False),
        sa.Column("missed_runs", sa.Integer(), nullable=True),
        sa.Column("maintenance_runs", sa.Integer(), nullable=True),
        sa.Column("days_held", sa.SmallInteger(), nullable=False),
        sa.Column("days_in_month", sa.SmallInteger(), nullable=False),
        sa.Column("loss_known", sa.Boolean(), nullable=False),
        sa.Column("upstream_sampling", sa.Text(), nullable=True),
        sa.Column("deleted_at", _TIMESTAMP, nullable=False),
        sa.PrimaryKeyConstraint("org_id", "slo_id", "month", name="pk_slo_deleted_months"),
        sa.CheckConstraint(f"kind IN ({_KINDS})", name="kind_is_known"),
        sa.CheckConstraint(f"result IN ({_RESULTS})", name="result_is_known"),
        sa.CheckConstraint(f"upstream_sampling IN ({_SAMPLING})", name="sampling_is_known"),
        sa.CheckConstraint("extract(day FROM month) = 1", name="month_is_its_first_day"),
        sa.CheckConstraint(
            "objective_percent BETWEEN 90 AND 99.99", name="objective_is_within_range"
        ),
        sa.CheckConstraint("figure_percent BETWEEN 0 AND 100", name="figure_is_a_percentage"),
        sa.CheckConstraint("good >= 0 AND total >= good", name="counts_add_up"),
        sa.CheckConstraint(
            "quiet_minutes >= 0 AND longest_quiet_minutes >= 0 AND lost_minutes >= 0 "
            "AND excluded_minutes >= 0",
            name="minutes_are_not_negative",
        ),
        sa.CheckConstraint(
            "days_held BETWEEN 1 AND days_in_month AND days_in_month BETWEEN 28 AND 31",
            name="days_fit_the_month",
        ),
    )
    # The *Last month* view reads one project's month across its targets.
    op.create_index(
        "ix_slo_deleted_months_org_id_project_id_month",
        "slo_deleted_months",
        ["org_id", "project_id", "month"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: slo_deleted_months holds the only record of a purged "
        "target's months once its daily counts are gone. Going back past 0053 is a restore."
    )
