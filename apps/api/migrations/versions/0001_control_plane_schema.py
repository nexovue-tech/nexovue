"""control plane schema

Organizations, users, projects, keys, checks, integrations, deploy events,
usage, sessions and the audit log — the whole of P1-59, in one migration
because they are one schema and a half-created control plane is not useful.

Revision ID: 0001
Revises:
Created: 2026-08-26
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Postgres 18 generates time-ordered v7 ids, so no id is ever sent from Python.
UUIDV7 = sa.text("uuidv7()")
NOW = sa.text("now()")

USER_ROLE = postgresql.ENUM(
    "owner", "admin", "member", "viewer", name="user_role", create_type=False
)


def _uuid_pk() -> sa.Column[uuid.UUID]:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=UUIDV7)


def _org_fk(nullable: bool = False) -> sa.Column[uuid.UUID]:
    return sa.Column(
        "org_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=nullable,
    )


def _project_fk() -> sa.Column[uuid.UUID]:
    return sa.Column(
        "project_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )


def _created_at() -> sa.Column[datetime]:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW)


def upgrade() -> None:
    USER_ROLE.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "organizations",
        _uuid_pk(),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("plan", sa.String(64), nullable=False, server_default=sa.text("'free'")),
        _created_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )

    op.create_table(
        "users",
        _uuid_pk(),
        _org_fk(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("role", USER_ROLE, nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "invited_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        # Globally unique, not unique per organization: one user, one org.
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_org_id", "users", ["org_id"])

    op.create_table(
        "projects",
        _uuid_pk(),
        _org_fk(),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column(
            "sampling_config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        _created_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("org_id", "slug", name="uq_projects_org_id_slug"),
    )
    op.create_index("ix_projects_org_id", "projects", ["org_id"])

    op.create_table(
        "api_keys",
        _uuid_pk(),
        _org_fk(),
        _project_fk(),
        sa.Column("name", sa.String(200), nullable=False),
        # The receiver looks up by prefix and verifies the SHA-256 hash. Argon2
        # is deliberately absent: it is designed to be slow, which is fatal on a
        # path that verifies keys thousands of times per second.
        sa.Column("key_prefix", sa.String(12), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key_prefix", name="uq_api_keys_key_prefix"),
    )
    op.create_index("ix_api_keys_project_id", "api_keys", ["project_id"])

    op.create_table(
        "site_keys",
        _uuid_pk(),
        _org_fk(),
        _project_fk(),
        sa.Column("public_key", sa.String(64), nullable=False),
        sa.Column(
            "allowed_origins",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        _created_at(),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("public_key", name="uq_site_keys_public_key"),
    )
    op.create_index("ix_site_keys_project_id", "site_keys", ["project_id"])

    op.create_table(
        "checks",
        _uuid_pk(),
        _org_fk(),
        _project_fk(),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _created_at(),
    )
    op.create_index("ix_checks_enabled_org_id", "checks", ["enabled", "org_id"])

    op.create_table(
        "integrations",
        _uuid_pk(),
        _org_fk(),
        _project_fk(),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("config_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
    )
    op.create_index("ix_integrations_project_id", "integrations", ["project_id"])

    op.create_table(
        "deploy_events",
        _uuid_pk(),
        _org_fk(),
        _project_fk(),
        sa.Column("version", sa.String(200), nullable=False),
        sa.Column("service_name", sa.String(200), nullable=False),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        _created_at(),
    )
    op.create_index(
        "ix_deploy_events_org_id_project_id_deployed_at",
        "deploy_events",
        ["org_id", "project_id", "deployed_at"],
    )

    op.create_table(
        "usage_daily",
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("spans", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("logs", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("metric_points", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )

    op.create_table(
        "sessions",
        _uuid_pk(),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table(
        "audit_log",
        _uuid_pk(),
        _org_fk(),
        # Null when the system acted rather than a person.
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(200), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("ix_audit_log_org_id_at", "audit_log", ["org_id", "at"])


def downgrade() -> None:
    # Reverse creation order: children before the tables they reference.
    op.drop_table("audit_log")
    op.drop_table("sessions")
    op.drop_table("usage_daily")
    op.drop_table("deploy_events")
    op.drop_table("integrations")
    op.drop_table("checks")
    op.drop_table("site_keys")
    op.drop_table("api_keys")
    op.drop_table("projects")
    op.drop_table("users")
    op.drop_table("organizations")
    # Dropping a table does not drop the enum type it used.
    USER_ROLE.drop(op.get_bind(), checkfirst=False)
