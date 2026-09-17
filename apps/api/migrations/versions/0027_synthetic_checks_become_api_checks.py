"""synthetic checks become API checks, and gain state, secrets, files and windows

F18 (`docs/features/synthetic-checks/`). Everything the feature stores, in one
revision, because none of it is usable without the rest: a check whose secret
table does not exist yet cannot be saved.

**Browser checks are deleted, not migrated.** The founder removed them from the
product (spec D13); nexovue is pre-deployment, so there is no customer whose flow
this loses.

**An `http` check becomes an `api` check that asserts what the old probe did.**
The HTTP probe called a status under 400 up. The same rule is written into the
new configuration as an assertion, so a check that was up yesterday is judged by
the same standard today rather than silently by a stricter one.

**`target` goes.** The URL lives in `config`, and a TCP host or a DNS name is not
a URL; a second column holding a copy of one field of the JSON would be a column
to keep in step forever.

Revision ID: 0027
Revises: 0026
Created: 2026-09-17
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)
_NAME_LENGTH = 120
_LABEL_LENGTH = 64

_CARRIED_OVER_STATUS_ASSERTION = """
jsonb_build_object(
    'type', 'api',
    'request', jsonb_build_object(
        'method', 'GET',
        'url', target,
        'assertions', jsonb_build_array(
            jsonb_build_object('source', 'status_code', 'comparison', 'less_than', 'target', '400')
        )
    )
)
"""


def _tenant_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
    ]


def _check_reference(ondelete: str, *, nullable: bool) -> sa.Column[Any]:
    return sa.Column(
        "check_id", _UUID, sa.ForeignKey("checks.id", ondelete=ondelete), nullable=nullable
    )


def _created_at() -> sa.Column[Any]:
    return sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    op.execute("DELETE FROM checks WHERE type <> 'http'")
    op.add_column("checks", sa.Column("name", sa.String(_NAME_LENGTH), nullable=True))
    op.execute(
        f"UPDATE checks SET type = 'api', name = left(target, {_NAME_LENGTH}), "
        f"config = {_CARRIED_OVER_STATUS_ASSERTION}"
    )
    op.alter_column("checks", "name", nullable=False)
    op.alter_column("checks", "config", server_default=None)
    op.drop_column("checks", "target")

    op.add_column(
        "checks",
        sa.Column(
            "locations",
            postgresql.ARRAY(sa.String(_LABEL_LENGTH)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column("checks", sa.Column("degraded_ms", sa.Integer(), nullable=True))
    op.add_column("checks", sa.Column("failing_ms", sa.Integer(), nullable=True))
    op.add_column(
        "checks",
        sa.Column("retries", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column("checks", sa.Column("heartbeat_token_hash", sa.String(64), nullable=True))
    op.add_column("checks", sa.Column("last_ping_at", _TIMESTAMP, nullable=True))
    op.add_column(
        "checks",
        sa.Column("updated_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
    )
    op.create_unique_constraint(
        "uq_checks_heartbeat_token_hash", "checks", ["heartbeat_token_hash"]
    )
    op.create_check_constraint(
        "type_is_known", "checks", "type IN ('api', 'multistep', 'heartbeat', 'tcp', 'dns')"
    )
    op.create_check_constraint(
        "interval_is_within_bounds", "checks", "interval_seconds BETWEEN 60 AND 86400"
    )
    op.create_check_constraint("retries_are_bounded", "checks", "retries BETWEEN 1 AND 3")
    op.create_check_constraint(
        "degraded_is_below_failing",
        "checks",
        "degraded_ms IS NULL OR failing_ms IS NULL OR degraded_ms < failing_ms",
    )
    op.create_index("ix_checks_project_id", "checks", ["project_id"])

    op.create_table(
        "check_states",
        *_tenant_columns(),
        _check_reference("CASCADE", nullable=False),
        sa.Column("state", sa.String(_LABEL_LENGTH), nullable=False),
        sa.Column("since", _TIMESTAMP, nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("location", sa.String(_LABEL_LENGTH), nullable=True),
        sa.Column("last_run_at", _TIMESTAMP, nullable=False),
        sa.UniqueConstraint("check_id", name="uq_check_states_check_id"),
        sa.CheckConstraint(
            "state IN ('up', 'degraded', 'down', 'misconfigured')", name="state_is_known"
        ),
    )

    op.create_table(
        "check_state_changes",
        *_tenant_columns(),
        _check_reference("CASCADE", nullable=False),
        sa.Column("from_state", sa.String(_LABEL_LENGTH), nullable=True),
        sa.Column("to_state", sa.String(_LABEL_LENGTH), nullable=False),
        sa.Column("changed_at", _TIMESTAMP, nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("location", sa.String(_LABEL_LENGTH), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_check_state_changes_check_id_changed_at",
        "check_state_changes",
        ["check_id", "changed_at"],
    )

    for table, value_column in (
        ("check_secrets", sa.Column("sealed_value", sa.LargeBinary(), nullable=False)),
        ("check_variables", sa.Column("value", sa.Text(), nullable=False)),
    ):
        op.create_table(
            table,
            *_tenant_columns(),
            sa.Column("name", sa.String(_LABEL_LENGTH), nullable=False),
            value_column,
            _created_at(),
            sa.Column("updated_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("project_id", "name", name=f"uq_{table}_project_id_name"),
        )

    op.create_table(
        "check_files",
        *_tenant_columns(),
        _check_reference("SET NULL", nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        _created_at(),
        # When a check stopped using it. The sweep removes a detached file an hour
        # on, and one never attached a day on; deleting a check detaches its files,
        # so their objects go only after the deletion has committed.
        sa.Column("detached_at", _TIMESTAMP, nullable=True),
        sa.CheckConstraint("size_bytes >= 0", name="size_is_not_negative"),
    )
    op.create_index("ix_check_files_check_id", "check_files", ["check_id"])
    op.create_index(
        "ix_check_files_unattached_created_at",
        "check_files",
        ["created_at"],
        postgresql_where=sa.text("check_id IS NULL"),
    )

    op.create_table(
        "maintenance_windows",
        *_tenant_columns(),
        sa.Column("name", sa.String(_NAME_LENGTH), nullable=False),
        sa.Column("starts_at", _TIMESTAMP, nullable=False),
        sa.Column("ends_at", _TIMESTAMP, nullable=False),
        sa.Column(
            "recurrence",
            sa.String(_LABEL_LENGTH),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
        sa.Column("repeat_until", _TIMESTAMP, nullable=True),
        sa.Column("applies_to_all", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        _created_at(),
        sa.CheckConstraint("ends_at > starts_at", name="ends_after_it_starts"),
        sa.CheckConstraint("recurrence IN ('none', 'daily', 'weekly')", name="recurrence_is_known"),
    )
    op.create_index("ix_maintenance_windows_project_id", "maintenance_windows", ["project_id"])

    op.create_table(
        "maintenance_window_checks",
        *_tenant_columns(),
        sa.Column(
            "window_id",
            _UUID,
            sa.ForeignKey("maintenance_windows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _check_reference("CASCADE", nullable=False),
        sa.UniqueConstraint(
            "window_id", "check_id", name="uq_maintenance_window_checks_window_id_check_id"
        ),
    )
    op.create_index(
        "ix_maintenance_window_checks_check_id", "maintenance_window_checks", ["check_id"]
    )


def downgrade() -> None:
    """Reversible for the one kind the old schema understood.

    An `api` check that makes a plain `GET` keeps its URL; every other check, and
    every secret, file, state and window, is lost — the previous schema has
    nowhere to hold them.
    """
    for table in (
        "maintenance_window_checks",
        "maintenance_windows",
        "check_files",
        "check_variables",
        "check_secrets",
        "check_state_changes",
        "check_states",
    ):
        op.drop_table(table)

    op.drop_index("ix_checks_project_id", table_name="checks")
    for constraint in (
        "degraded_is_below_failing",
        "retries_are_bounded",
        "interval_is_within_bounds",
        "type_is_known",
    ):
        op.drop_constraint(constraint, "checks", type_="check")
    op.drop_constraint("uq_checks_heartbeat_token_hash", "checks", type_="unique")
    for column in (
        "updated_at",
        "last_ping_at",
        "heartbeat_token_hash",
        "retries",
        "failing_ms",
        "degraded_ms",
        "locations",
    ):
        op.drop_column("checks", column)

    op.execute("DELETE FROM checks WHERE type <> 'api'")
    op.add_column("checks", sa.Column("target", sa.Text(), nullable=True))
    op.execute("UPDATE checks SET type = 'http', target = config->'request'->>'url', config = '{}'")
    op.alter_column("checks", "target", nullable=False)
    op.alter_column("checks", "config", server_default=sa.text("'{}'::jsonb"))
    op.drop_column("checks", "name")
