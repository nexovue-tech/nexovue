"""Releases, the periods each served, and how far the sync has read

F28 (`docs/features/deployment-release-tracking/`), plan T1.8; follows F19's 0053.

**Why this change is required.** A release is a stored record, not a view over the
version rollup (design 2, D2): `service_versions_1m` keeps at most 35 days, so a
first-seen time read from it would drift forward as rows expire; a version only ever
reported has no rollup row at all; and F3's evidence needs ids that do not change.
A version can come back, so each stretch in service is its own period, and a past
period only ever gains an end (D15, FR19).

**Affected store and schema.** Postgres, three new tables:

- `releases`: one row per project, service and version (FR18), with the first and
  last time it was seen and reported, the last state a sender gave, and
  `last_activity_at`, the latest *arrival* of a report or a count, which decides
  retention, so a sender cannot shorten it. `UNIQUE (org_id, id)` lets periods key
  on both, so the database refuses a period filed under another tenant's release.
  '' and `__other__` are never a release: the worker's no-version and fold.
- `release_periods`: one row per stretch in service, with how its start was learned
  (FR42), what it replaced (FR20), whether it was already running when counting
  began (FR43), whether it is a return, and the job's stored reading (FR55). One
  open period per release, and one period per release and start, so a retried sync
  cannot write a period twice.
- `release_sync_state`: per project, the last minute the sync has read (design 3,
  step 10). It only moves forward.

**Compatibility and backfill.** None. New tables; the sync fills them from the
rollup's next minutes, and the first version it counts on a service is flagged
already running, so connecting creates no false releases (AC58).

**Rollback and recovery.** Roll forward only: once the rollup's rows expire, a
release's first-seen time and its periods exist nowhere else. Going back past this
revision is a restore.

**Tenant, retention, and security impact.** Every table carries `org_id`. `releases`
and `release_sync_state` cascade from `projects`, periods from their release
(`organizations/tables.py`). Names are telemetry text, shown as plain text and never
handed to a model as words (design, *The fact F3 receives*). No sender free text is
stored here. Retention by the project's derived retention comes with T4.6.

Revision ID: 0054
Revises: 0053
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies of `nexovue_platform.releases`' names and the deploy states (spec,
# *States*), held equal by `test_releases.py`: a later change is a migration of its own.
_NAME_LONGEST = 200
_LABEL_LONGEST = 64
_NOT_A_RELEASE = "'', '__other__'"
_START_BASES = "'seen', 'reported'"
_DEPLOY_STATES = "'started', 'finished', 'failed', 'rolled_back'"


def upgrade() -> None:
    _releases()
    _release_periods()
    _release_sync_state()


def _releases() -> None:
    op.create_table(
        "releases",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("service_name", sa.String(_NAME_LONGEST), nullable=False),
        sa.Column("version", sa.String(_NAME_LONGEST), nullable=False),
        sa.Column("first_seen_at", _TIMESTAMP, nullable=True),
        sa.Column("last_seen_at", _TIMESTAMP, nullable=True),
        sa.Column("first_reported_at", _TIMESTAMP, nullable=True),
        sa.Column("last_reported_at", _TIMESTAMP, nullable=True),
        sa.Column("report_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_state", sa.String(_LABEL_LONGEST), nullable=True),
        sa.Column("last_activity_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "id", name="uq_releases_org_id_id"),
        sa.UniqueConstraint(
            "project_id", "service_name", "version", name="uq_releases_project_service_version"
        ),
        sa.CheckConstraint(f"version NOT IN ({_NOT_A_RELEASE})", name="version_is_a_release"),
        sa.CheckConstraint("service_name <> ''", name="service_is_named"),
        sa.CheckConstraint(
            "first_seen_at IS NOT NULL OR first_reported_at IS NOT NULL",
            name="seen_or_reported",
        ),
        sa.CheckConstraint("report_count >= 0", name="report_count_is_not_negative"),
        sa.CheckConstraint(f"last_state IN ({_DEPLOY_STATES})", name="last_state_is_known"),
    )
    # The retention sweep and eviction, oldest activity first (design 3).
    op.create_index("ix_releases_last_activity_at", "releases", ["last_activity_at"])


def _release_periods() -> None:
    op.create_table(
        "release_periods",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("org_id", _UUID, nullable=False),
        sa.Column("release_id", _UUID, nullable=False),
        sa.Column("project_id", _UUID, nullable=False),
        sa.Column("service_name", sa.String(_NAME_LONGEST), nullable=False),
        sa.Column("started_at", _TIMESTAMP, nullable=False),
        sa.Column("start_basis", sa.String(_LABEL_LONGEST), nullable=False),
        sa.Column("ended_at", _TIMESTAMP, nullable=True),
        sa.Column("previous_version", sa.String(_NAME_LONGEST), nullable=True),
        sa.Column("already_running", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_return", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_reading", postgresql.JSONB(), nullable=True),
        sa.Column("mixed_environments", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "release_id"],
            ["releases.org_id", "releases.id"],
            ondelete="CASCADE",
            name="fk_release_periods_release",
        ),
        sa.UniqueConstraint(
            "org_id", "release_id", "started_at", name="uq_release_periods_release_start"
        ),
        sa.CheckConstraint(f"start_basis IN ({_START_BASES})", name="start_basis_is_known"),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name="ended_after_started"
        ),
        sa.CheckConstraint(
            f"previous_version NOT IN ({_NOT_A_RELEASE})", name="previous_version_is_a_release"
        ),
        sa.CheckConstraint(
            "NOT (already_running AND is_return)", name="a_return_was_not_already_running"
        ),
    )
    # One period in service per release at a time.
    op.create_index(
        "uq_release_periods_one_open",
        "release_periods",
        ["org_id", "release_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    # The list and the markers, newest first; the "before this" read, per service.
    op.create_index(
        "ix_release_periods_org_id_project_id_started_at",
        "release_periods",
        ["org_id", "project_id", sa.text("started_at DESC"), "id"],
    )
    op.create_index(
        "ix_release_periods_org_id_project_id_service_name_started_at",
        "release_periods",
        ["org_id", "project_id", "service_name", sa.text("started_at DESC")],
    )
    # An open period overlaps every later window, so the list reads them by this.
    op.create_index(
        "ix_release_periods_open",
        "release_periods",
        ["org_id", "project_id"],
        postgresql_where=sa.text("ended_at IS NULL"),
    )


def _release_sync_state() -> None:
    op.create_table(
        "release_sync_state",
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id",
            _UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("synced_through", _TIMESTAMP, nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: once the version rollup's rows expire, a release's first-seen "
        "time and its periods exist nowhere else. Going back past 0054 is a restore."
    )
