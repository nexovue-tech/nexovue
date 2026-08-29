"""The control-plane schema.

Shape and rationale: docs/architecture/tenancy-and-data-model.md.

Two rules run through every table here:

`org_id` is denormalized onto every tenant-scoped table, even where it is
reachable through `project_id`. That keeps the tenant filter one column away
everywhere, so the same rule — every query filters `org_id` — holds identically
on the Postgres and ClickHouse sides.

API keys hash with SHA-256, never argon2. Argon2 is designed to be slow, which
is right for a human password and catastrophic for a credential the receiver
verifies thousands of times per second; a high-entropy random key gains nothing
from stretching. Passwords, typed by humans, use argon2.
"""

import uuid
from datetime import date
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexovue_api.data.postgres.base import (
    Base,
    CreatedAt,
    OptionalTimestamp,
    RequiredTimestamp,
    UuidPrimaryKey,
    UuidType,
)

# `viewer` is what the non-technical owner persona gets when F26 lands in
# Phase 2. The columns exist now so nothing migrates then.
USER_ROLES: Final = ("owner", "admin", "member", "viewer")
UserRole: Final = Enum(*USER_ROLES, name="user_role")

# Matches the production TTL on the ClickHouse tables. Per-project overrides
# regenerate that TTL expression rather than running scheduled deletes.
DEFAULT_RETENTION_DAYS: Final = 30

# The plaintext lookup half of `nvk_live_<22 chars>`: nine characters of scheme
# plus seven of the secret. Seven, not three: the prefix is unique, and three gave
# 238,328 values — a coin-flip collision at ~500 live keys, which would fail a real
# customer's signup because of someone else's key. Fifteen secret characters remain
# hidden, which is entropy enough that lengthening the prefix costs nothing.
API_KEY_PREFIX_LENGTH: Final = 16

# Long enough for an email address, the longest thing an actor label holds.
ACTOR_LABEL_LENGTH: Final = 320
SHA256_HEX_LENGTH: Final = 64

# RFC 5321 caps a path at 320 octets.
EMAIL_MAX_LENGTH: Final = 320
SLUG_MAX_LENGTH: Final = 63
NAME_MAX_LENGTH: Final = 200
SHORT_LABEL_LENGTH: Final = 64


class Organization(Base):
    """The tenant. Billing, membership and isolation boundary; one per company."""

    __tablename__ = "organizations"

    id: Mapped[UuidPrimaryKey]
    slug: Mapped[str] = mapped_column(String(SLUG_MAX_LENGTH), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    # The tier this organization is on (ADR 0071). A foreign key rather than
    # free text, because a typo here would silently grant whichever retention
    # the resolver fell back to. What each tier grants lives in `plans`; what a
    # project actually gets is resolved against it, never taken from here.
    plan: Mapped[str] = mapped_column(
        String(SHORT_LABEL_LENGTH),
        ForeignKey("plans.slug", ondelete="RESTRICT"),
        nullable=False,
        server_default=text("'free'"),
    )
    created_at: Mapped[CreatedAt]
    # Soft delete: purging telemetry means dropping ClickHouse partitions and
    # cold-tier objects, which cannot happen inside this transaction.
    deleted_at: Mapped[OptionalTimestamp]


class User(Base):
    """A user belongs to exactly one organization, so email is globally unique.

    Someone genuinely working with two companies uses two emails — accepted
    deliberately rather than paying for a join table, an org switcher on every
    screen and org context on every session.
    """

    __tablename__ = "users"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(EMAIL_MAX_LENGTH), nullable=False, unique=True)
    # Null until an invited user sets one (F26, Phase 2).
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), nullable=True)
    role: Mapped[str] = mapped_column(UserRole, nullable=False)
    email_verified_at: Mapped[OptionalTimestamp]
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    invited_at: Mapped[OptionalTimestamp]
    created_at: Mapped[CreatedAt]

    __table_args__ = (Index("ix_users_org_id", "org_id"),)


class Plan(Base):
    """What a tier grants. Rows, not constants: these are the numbers most
    likely to change after contact with customers, and changing one should not
    need a deploy. Seeded from ADR 0071."""

    __tablename__ = "plans"

    slug: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), primary_key=True)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    # Spans and logs.
    raw_retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    # Metrics and rollups — small, and what answers "was it always this slow".
    derived_retention_days: Mapped[int] = mapped_column(Integer, nullable=False)


class Project(Base):
    """A project is an environment — production, staging.

    Retention, sampling, quotas and ingest keys belong here rather than on the
    organization, and keeping environments apart keeps staging noise out of
    production findings.
    """

    __tablename__ = "projects"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(SLUG_MAX_LENGTH), nullable=False)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    # What this project asks to keep. The plan grants a ceiling and this may sit
    # below it — staging rarely wants thirty days — but never above it. The
    # effective value is resolved in `plans.service`, and it is that value the
    # worker stamps on every row so a part can expire as a whole (ADR 0072).
    retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text(str(DEFAULT_RETENTION_DAYS))
    )
    sampling_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Which default masking rules this project switches off, and what it adds.
    # Empty means the defaults in ADR 0059 apply, which is the intended state for
    # most projects — masking is irreversible, so the shipped rules cover only
    # what could never be legitimately needed to debug.
    masking_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[CreatedAt]
    deleted_at: Mapped[OptionalTimestamp]

    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_projects_org_id_slug"),
        Index("ix_projects_org_id", "org_id"),
    )


class ApiKey(Base):
    """An ingest credential. Carries both ids so the receiver's hot path is one
    cached lookup rather than a key → project → org chain."""

    __tablename__ = "api_keys"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    # Plaintext, and the only part ever stored in the clear: it is what the
    # lookup indexes. The secret half is shown once and never persisted.
    key_prefix: Mapped[str] = mapped_column(
        String(API_KEY_PREFIX_LENGTH), nullable=False, unique=True
    )
    key_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), nullable=False)
    last_used_at: Mapped[OptionalTimestamp]
    created_at: Mapped[CreatedAt]
    # Revoked rather than deleted: multiple live keys per project mean rotation
    # never needs downtime, and an audit needs to see what was withdrawn.
    revoked_at: Mapped[OptionalTimestamp]

    __table_args__ = (Index("ix_api_keys_project_id", "project_id"),)


class SiteKey(Base):
    """Door 3's public, non-secret browser key. Origin-restricted rather than
    secret, because it ships inside a page anyone can read."""

    __tablename__ = "site_keys"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    public_key: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False, unique=True)
    allowed_origins: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    created_at: Mapped[CreatedAt]
    revoked_at: Mapped[OptionalTimestamp]

    __table_args__ = (Index("ix_site_keys_project_id", "project_id"),)


class Check(Base):
    """A door 2 probe definition. Our own Beat scheduler reads this table, so a
    customer editing a check in the UI needs no redeploy."""

    __tablename__ = "checks"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[CreatedAt]

    # The scheduler's only query is "every enabled check"; it runs every tick.
    __table_args__ = (Index("ix_checks_enabled_org_id", "enabled", "org_id"),)


class Integration(Base):
    """A door 4b connector.

    Holds no credential. The customer grants a role our account may assume and
    we keep its ARN plus an ExternalId we generated — neither is secret, so
    there is nothing here to encrypt (ADR 0076)."""

    __tablename__ = "integrations"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    # Plain JSON, and named for what it is. It held no ciphertext even when it
    # was called `config_encrypted`, and ADR 0076 removed the reason it would
    # ever need to: a role ARN and an ExternalId are not secrets.
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Ours to generate, never the customer's to choose. AWS requires it to be
    # unguessable, which is the whole of its value: it is what stops someone
    # else persuading us to assume a role on their behalf.
    external_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("300")
    )
    status: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    last_sync_at: Mapped[OptionalTimestamp]
    created_at: Mapped[CreatedAt]

    __table_args__ = (Index("ix_integrations_project_id", "project_id"),)


class DeployEvent(Base):
    """Door 4a lands here, not in ClickHouse: volume is tiny and the insight
    engine joins these against findings and cases."""

    __tablename__ = "deploy_events"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # Joins to spans.deployment_version — a logical join across two stores, not
    # a foreign key.
    version: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    service_name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    deployed_at: Mapped[RequiredTimestamp]
    source: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    # `metadata` is taken on a declarative class, so the attribute differs from
    # the column name.
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[CreatedAt]

    __table_args__ = (
        Index(
            "ix_deploy_events_org_id_project_id_deployed_at", "org_id", "project_id", "deployed_at"
        ),
    )


class UsageDaily(Base):
    """Ingestion counters rolled up per day. Feeds the in-product cost view, so
    a customer is never surprised by a bill."""

    __tablename__ = "usage_daily"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    spans: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    logs: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    metric_points: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))


class Session(Base):
    """A browser session. Kept server-side so revoking one is immediate."""

    __tablename__ = "sessions"

    id: Mapped[UuidPrimaryKey]
    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[RequiredTimestamp]
    created_at: Mapped[CreatedAt]

    __table_args__ = (
        Index("ix_sessions_user_id", "user_id"),
        # Sweeping expired rows must not scan the table.
        Index("ix_sessions_expires_at", "expires_at"),
    )


class AuditLogEntry(Base):
    """Who changed what.

    `actor_user_id` is null for actions the system took, and for actors who are
    not rows in `users` — our own staff during an impersonated session, who
    `actor_label` names instead. `org_id` is null only for a refused sign-in
    against an address that matches no account, which belongs to no tenant.
    """

    __tablename__ = "audit_log"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Names an actor who is not a row in `users`. Never an address belonging to
    # someone who is not ours: see `audit.service`.
    actor_label: Mapped[str | None] = mapped_column(String(ACTOR_LABEL_LENGTH), nullable=True)
    # The impersonation session an entry was written under, if any (F31).
    impersonation_context: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    action: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    target: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    at: Mapped[CreatedAt]

    __table_args__ = (Index("ix_audit_log_org_id_at", "org_id", "at"),)
