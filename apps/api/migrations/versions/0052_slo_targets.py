"""Reliability targets, their status, daily counts and change history

F19 (`docs/features/slo-tracking/`), plan T1.2; follows F22's 0051.

**Why this change is required.** A reliability target is a stored definition, and
its monthly figure has to outlive the rollups it is counted from: a self-hosted
organization keeps them 30 days (design, *Context and goal*; spec D2). So the
scheduler keeps one row per target per day here, and the list reads Postgres alone
(design, *Status and days*).

**Affected store and schema.** Postgres, four new tables:

- `slo_targets`: the definition, soft-deleted for 30 days (FR31), with the
  sampling answer (FR5), the definition and history-read versions (AC64) and two
  partial unique indexes over live targets: the name, and the definition key, so
  two targets with one definition cannot read alike in a message (D35, AC105).
  `UNIQUE (org_id, id)` lets the children key on both, so the database refuses a
  row filed under another tenant's target.
- `slo_status`: one row per target, rewritten each minute by the job.
- `slo_daily_counts`: one row per target and local day (D2): counts, quiet, lost
  and excluded minutes, the day's quiet stretches (so a stretch can cross
  midnight from Postgres alone), and the definition version it was counted under.
- `slo_target_changes`: one row per change (FR30). No foreign key to the target,
  because it stays when the target is purged (FR31, D46); it goes with the project.

**Compatibility and backfill.** None. New tables, no existing rows to migrate.

**Rollback and recovery.** Roll forward only: `slo_daily_counts` and
`slo_target_changes` hold figures and a record that exist nowhere else once the
rollups expire. Going back past this revision is a restore.

**Tenant, retention, and security impact.** Every table carries `org_id`; the two
children of `slo_targets` reference it by `(org_id, id)`. `slo_targets` and
`slo_target_changes` cascade from `projects`, the others from `slo_targets`
(`organizations/tables.py`). People's ids are kept on a member's removal and
renamed by the scrub; free text is a target's name and description only, which
never enters an incident or a notification (FR33). Lapsing at 400 days and the
purge come with Step 3.

Revision ID: 0052
Revises: 0051
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies of `nexovue_platform.slo`'s names, held equal to it by
# `test_slo_model.py`: a later change to them is a migration of its own.
_KINDS = "'service_availability', 'service_latency', 'endpoint_availability', 'check_availability'"
_SAMPLING = "'no', 'yes', 'not_sure'"
_CHANGES = "'created', 'changed', 'deleted', 'restored'"
_CHANGED_WHY = "'exclusion', 'exclusion_removed', 'definition'"
_NAME_LONGEST = 120
_DESCRIPTION_LONGEST = 500
_SOURCE_NAME_LONGEST = 255
_LEFT_OUT_MOST = 20
_LAST_BOUNDARY = 62


def upgrade() -> None:
    _slo_targets()
    _slo_status()
    _slo_daily_counts()
    _slo_target_changes()


def _slo_targets() -> None:
    op.create_table(
        "slo_targets",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("service_name", sa.Text(), nullable=True),
        sa.Column("endpoint_name", sa.Text(), nullable=True),
        # A deleted check leaves the target pointing at nothing: Source gone (AC70).
        sa.Column(
            "check_id", _UUID, sa.ForeignKey("checks.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("objective_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("window_days", sa.SmallInteger(), nullable=False),
        sa.Column("boundary_index", sa.SmallInteger(), nullable=True),
        sa.Column("asked_ms", sa.Float(), nullable=True),
        sa.Column(
            "left_out_endpoints",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("upstream_sampling", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("definition_key", sa.Text(), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("history_read_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("history_read_through", sa.Date(), nullable=True),
        sa.Column("first_event_at", _TIMESTAMP, nullable=True),
        sa.Column("exhausted_warned_at", _TIMESTAMP, nullable=True),
        sa.Column("exhausted_armed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", _UUID, nullable=True),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", _TIMESTAMP, nullable=True),
        sa.Column("deleted_by", _UUID, nullable=True),
        sa.UniqueConstraint("org_id", "id", name="uq_slo_targets_org_id_id"),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (created_by)",
            name="fk_slo_targets_created_by",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "deleted_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (deleted_by)",
            name="fk_slo_targets_deleted_by",
        ),
        sa.CheckConstraint(f"length(name) BETWEEN 1 AND {_NAME_LONGEST}", name="a_name_is_short"),
        sa.CheckConstraint(
            f"length(description) <= {_DESCRIPTION_LONGEST}", name="a_description_is_short"
        ),
        sa.CheckConstraint(f"kind IN ({_KINDS})", name="kind_is_known"),
        sa.CheckConstraint(
            f"length(service_name) <= {_SOURCE_NAME_LONGEST} "
            f"AND length(endpoint_name) <= {_SOURCE_NAME_LONGEST}",
            name="source_names_are_short",
        ),
        sa.CheckConstraint(
            "objective_percent BETWEEN 90 AND 99.99", name="objective_is_within_range"
        ),
        sa.CheckConstraint("window_days IN (7, 28)", name="window_is_7_or_28_days"),
        sa.CheckConstraint(
            f"boundary_index BETWEEN 0 AND {_LAST_BOUNDARY}", name="boundary_is_a_real_boundary"
        ),
        sa.CheckConstraint(
            f"cardinality(left_out_endpoints) <= {_LEFT_OUT_MOST}",
            name="left_out_endpoints_are_few",
        ),
        sa.CheckConstraint(f"upstream_sampling IN ({_SAMPLING})", name="sampling_is_known"),
        # Each kind has its own fields set, and no other kind's (design, *Data*).
        # A check target may name a service, only to join findings (D19).
        sa.CheckConstraint(
            "kind = 'check_availability' OR service_name IS NOT NULL",
            name="a_request_kind_names_its_service",
        ),
        sa.CheckConstraint(
            "(endpoint_name IS NOT NULL) = (kind = 'endpoint_availability')",
            name="only_an_endpoint_target_names_an_endpoint",
        ),
        sa.CheckConstraint(
            "(boundary_index IS NOT NULL) = (kind = 'service_latency') "
            "AND (asked_ms IS NULL OR kind = 'service_latency')",
            name="only_a_latency_target_has_a_boundary",
        ),
        sa.CheckConstraint(
            "cardinality(left_out_endpoints) = 0 OR kind = 'service_availability'",
            name="only_service_availability_leaves_endpoints_out",
        ),
        sa.CheckConstraint(
            "(upstream_sampling IS NULL) = (kind = 'check_availability')",
            name="request_kinds_answer_the_sampling_question",
        ),
        sa.CheckConstraint(
            "check_id IS NULL OR kind = 'check_availability'",
            name="only_a_check_target_names_a_check",
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL) OR (deleted_at >= created_at)",
            name="deleted_after_created",
        ),
    )
    # A name, and a definition, once among a project's live targets (D35, AC116).
    op.create_index(
        "uq_slo_targets_live_name",
        "slo_targets",
        ["org_id", "project_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_slo_targets_live_definition",
        "slo_targets",
        ["org_id", "project_id", "definition_key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # The list, live and deleted, of one project.
    op.create_index(
        "ix_slo_targets_org_id_project_id_created_at",
        "slo_targets",
        ["org_id", "project_id", "created_at"],
    )


def _slo_status() -> None:
    op.create_table(
        "slo_status",
        sa.Column("slo_id", _UUID, primary_key=True),
        sa.Column("org_id", _UUID, nullable=False),
        sa.Column("project_id", _UUID, nullable=False),
        sa.Column("evaluated_minute", _TIMESTAMP, nullable=False),
        # Each burn tier's figures at `evaluated_minute` (Step 4); empty until then.
        sa.Column("tiers", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("last_event_minute", _TIMESTAMP, nullable=True),
        sa.Column("burning_since", _TIMESTAMP, nullable=True),
        sa.Column("last_held_minute", _TIMESTAMP, nullable=True),
        sa.Column("unjudged_since", _TIMESTAMP, nullable=True),
        sa.Column("longest_quiet_7d", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["org_id", "slo_id"],
            ["slo_targets.org_id", "slo_targets.id"],
            ondelete="CASCADE",
            name="fk_slo_status_target",
        ),
        sa.CheckConstraint("longest_quiet_7d >= 0", name="quiet_is_not_negative"),
    )
    # `queue_jobs` and the blind notice read status by project, oldest first.
    op.create_index(
        "ix_slo_status_org_id_project_id_evaluated_minute",
        "slo_status",
        ["org_id", "project_id", "evaluated_minute"],
    )


def _slo_daily_counts() -> None:
    op.create_table(
        "slo_daily_counts",
        sa.Column("org_id", _UUID, nullable=False),
        sa.Column("slo_id", _UUID, nullable=False),
        # The project's local day (D23).
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("good", sa.BigInteger(), nullable=False),
        sa.Column("total", sa.BigInteger(), nullable=False),
        sa.Column("failures", sa.BigInteger(), nullable=False),
        sa.Column("quiet_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("lost_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("excluded_minutes", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("quiet_leading_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("quiet_trailing_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("longest_quiet_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("expected_runs", sa.Integer(), nullable=True),
        sa.Column("missed_runs", sa.Integer(), nullable=True),
        sa.Column("maintenance_runs", sa.Integer(), nullable=True),
        sa.Column("loss_known", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("final", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("computed_at", _TIMESTAMP, nullable=False),
        sa.Column("changed_at", _TIMESTAMP, nullable=True),
        sa.Column("changed_why", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("org_id", "slo_id", "day", name="pk_slo_daily_counts"),
        sa.ForeignKeyConstraint(
            ["org_id", "slo_id"],
            ["slo_targets.org_id", "slo_targets.id"],
            ondelete="CASCADE",
            name="fk_slo_daily_counts_target",
        ),
        sa.CheckConstraint(
            "good >= 0 AND total >= good AND failures = total - good",
            name="counts_add_up",
        ),
        sa.CheckConstraint(
            "quiet_minutes >= 0 AND lost_minutes >= 0 AND excluded_minutes >= 0 "
            "AND quiet_leading_minutes >= 0 AND quiet_trailing_minutes >= 0 "
            "AND longest_quiet_minutes >= 0",
            name="minutes_are_not_negative",
        ),
        sa.CheckConstraint(f"changed_why IN ({_CHANGED_WHY})", name="changed_why_is_known"),
    )


def _slo_target_changes() -> None:
    op.create_table(
        "slo_target_changes",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        # No foreign key: the history outlives the target's purge (FR31, D46).
        sa.Column("slo_id", _UUID, nullable=False),
        sa.Column("at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("actor_user_id", _UUID, nullable=True),
        sa.Column("what", sa.Text(), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "actor_user_id"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (actor_user_id)",
            name="fk_slo_target_changes_actor",
        ),
        sa.CheckConstraint(f"what IN ({_CHANGES})", name="what_is_known"),
    )
    op.create_index(
        "ix_slo_target_changes_org_id_slo_id_at",
        "slo_target_changes",
        ["org_id", "slo_id", "at"],
    )
    # The personal-data export and the scrub read a person's own changes.
    op.create_index(
        "ix_slo_target_changes_org_id_actor_user_id",
        "slo_target_changes",
        ["org_id", "actor_user_id"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: slo_daily_counts and slo_target_changes hold figures and a "
        "record that exist nowhere else once the rollups expire. Going back past 0052 "
        "is a restore."
    )
