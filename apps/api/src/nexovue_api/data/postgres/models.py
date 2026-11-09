"""The control-plane schema.


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
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

from nexovue_platform import slo
from nexovue_platform.alerts.model import (
    ATTEMPT_KINDS,
    ATTEMPT_OUTCOMES,
    CHANNEL_HEALTH,
    CHANNEL_KINDS,
    CONDITION_KINDS,
    EVALUATION_PHASES,
    NOTIFICATION_KINDS,
    NOTIFICATION_STATES,
    PROJECT_NOTIFICATION_KINDS,
    SEVERITIES,
    WITHHELD_REASONS,
)
from nexovue_platform.cases import CASE_EMAIL_KINDS, EVENT_ACTOR_KINDS, NOTE_MAX_LENGTH
from nexovue_platform.connections import (
    COMMENT_KINDS,
    CONNECTION_FAILURES,
    CONNECTION_KINDS,
    CONNECTION_STATES,
    ISSUE_STATES,
    LINK_FAILURES,
    LINK_ROLES,
    LINK_STATUSES,
)
from nexovue_platform.detection import (
    CASE_RESOLUTIONS,
    CASE_STATES,
    CLOSED_REASONS,
    DEFAULT_SENSITIVITY,
    DEFAULT_TIMEZONE,
    FINDING_EVENT_KINDS,
    FINDING_SCOPES,
    FINDING_STATES,
    INTAKE_REFUSAL_REASONS,
    SENSITIVITIES,
    SHIFT_REASONS,
    SIGNALS,
)
from nexovue_platform.evidence import WORDING_REASONS, WORDING_STATES
from nexovue_platform.releases import DEPLOY_STATES, OTHER_VERSION, START_BASES
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
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
    TimestampTz,
    UuidPrimaryKey,
    UuidType,
)

# `viewer` is what the non-technical owner persona gets when F26 lands in
# Phase 2. The columns exist now so nothing migrates then.
# SHA-256, hex.
SET_PASSWORD_TOKEN_HASH_LENGTH: Final = 64

# ADR-009. A region is added here, and in a migration, when it exists.
REGIONS: Final = ("india", "india-south")
REGION_LENGTH: Final = 32

# What a person may be *inside an organization*. These are the roles
# `auth/permissions.py` builds its table from.
USER_ROLES: Final = ("owner", "admin", "member", "viewer")
# nexovue's own operator, and deliberately not one of the above: it appears in
# no row of the permission table, so it grants nothing inside any organization.
# What it unlocks is the platform surface, gated separately and only while the
# session has been re-authenticated (`docs/changes/super-admin-in-the-console/`).
SUPER_ADMIN_ROLE: Final = "super_admin"
ALL_USER_ROLES: Final = (*USER_ROLES, SUPER_ADMIN_ROLE)
UserRole: Final = Enum(*ALL_USER_ROLES, name="user_role")

# Matches the production TTL on the ClickHouse tables. Per-project overrides
# regenerate that TTL expression rather than running scheduled deletes.
DEFAULT_RETENTION_DAYS: Final = 30

# The plaintext lookup half of `nvk_live_<22 chars>`: nine characters of scheme
# plus seven of the secret. Seven, not three: the prefix is unique, and three gave
# 238,328 values — a coin-flip collision at ~500 live keys, which would fail a real
# customer's signup because of someone else's key. Fifteen secret characters remain
# hidden, which is entropy enough that lengthening the prefix costs nothing.
API_KEY_PREFIX_LENGTH: Final = 16

# `nvt_live_` plus the first characters of the secret: what a customer reads in
# the token list, and what a secret scanner matches. The rest is never stored.
API_TOKEN_PREFIX_LENGTH: Final = 20

# `nvp_live_<22 chars>` in full. Stored whole and in the clear, because a site key
# is printed in the page that carries it — there is no secret half to withhold, so
# there is no prefix to split off.
SITE_KEY_LENGTH: Final = 31

# Long enough for an email address, the longest thing an actor label holds.
ACTOR_LABEL_LENGTH: Final = 320
SHA256_HEX_LENGTH: Final = 64

# RFC 5321 caps a path at 320 octets.
EMAIL_MAX_LENGTH: Final = 320
SLUG_MAX_LENGTH: Final = 63
NAME_MAX_LENGTH: Final = 200
SHORT_LABEL_LENGTH: Final = 64

# An IANA time zone name. The longest the database ships is
# `America/Argentina/ComodRivadavia` at 32 characters; doubled, because the cap
# is a guard against an absurd string reaching Postgres and not a spec limit.
TIMEZONE_NAME_LENGTH: Final = 64

# `service.name` is a resource attribute OTLP puts no length limit on. The meter
# truncates to this before writing, so an absurd name is stored as a service
# rather than failing the flush that carried it.
SERVICE_NAME_LENGTH: Final = 255
# The last characters of a deploy secret, shown so one can be told from another.
SECRET_HINT_LENGTH: Final = 8

# A receiver's hostname, which names it in `intake_refusals`; DNS allows 253.
RECEIVER_ID_LENGTH: Final = 255

# ISO 4217.
CURRENCY_CODE_LENGTH: Final = 3

# An S3 object key; the bucket's own limit is 1024 UTF-8 bytes. A bundler path
# inside a build output can be nearly as long, so the two share the cap.
STORAGE_KEY_LENGTH: Final = 1024
MINIFIED_PATH_LENGTH: Final = 1024

# The ECMA-426 debug id. The specification names it a string and states no
# length; generators emit a UUID or a 32-character hex digest.
DEBUG_ID_LENGTH: Final = 64

# An identifier a payment processor issued or accepted — a subscription
# reference, a delivery id. Opaque here: nothing parses one or branches on its
# shape, and the cap is the longest any processor under consideration allows
# (250 characters, from a gateway evaluated in 2026-09), rounded to the next byte.
PROCESSOR_REFERENCE_LENGTH: Final = 255

# What a subscription is doing, in this product's words rather than a
# processor's. `pending` is a checkout that was started and not yet authorised;
# every processor calls that something different and none of it belongs here.
# `lapsed`: a payment failed for good and its grace ran out. Unlike `cancelled`
# nobody ended it, so a late successful charge brings it back (migration 0038).
BILLING_SUBSCRIPTION_STATUSES: Final = ("pending", "active", "cancelled", "lapsed")
BillingSubscriptionStatus: Final = Enum(
    *BILLING_SUBSCRIPTION_STATUSES, name="billing_subscription_status"
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class Organization(Base):
    """The tenant. Billing, membership and isolation boundary; one per company."""

    __tablename__ = "organizations"

    id: Mapped[UuidPrimaryKey]
    slug: Mapped[str] = mapped_column(String(SLUG_MAX_LENGTH), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    # The tier this organization is on. A foreign key rather than
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
    # The ingest stop valve (P1-52). NULL is open; any value closes the door at
    # the receiver. Operational, not billing — a suspended organization may be
    # well inside its allowance, and only a person reopens it. The receiver
    # reads it on the same row as the allowance, so closing it costs the ingest
    # path no extra query and takes effect within RECEIVER_ALLOWANCE_CACHE_TTL.
    ingest_suspended_at: Mapped[OptionalTimestamp]
    # Why, in the operator's own words, for the person answering the customer
    # who asks. NULL exactly when the valve is open, enforced by a CHECK in 0018:
    # a suspension without a reason is a support conversation starting from
    # nothing. The receiver never reads it.
    ingest_suspended_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Where this organization's data lives (ADR-009). Set when it is created;
    # moving it is a per-customer migration, not an update of this column.
    region: Mapped[str] = mapped_column(
        String(REGION_LENGTH), nullable=False, server_default=text("'india'")
    )

    __table_args__ = (CheckConstraint(f"region IN ({_quoted(REGIONS)})", name="region_is_known"),)


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
    # An IANA name (`Asia/Kolkata`), never an offset: an offset stored in March
    # is wrong in November, and the name is what survives a daylight-saving
    # change. NULL means nobody has chosen one and the screen keeps rendering in
    # the browser's zone — which is what every screen does today, so NULL is the
    # honest state of every existing row rather than a default nobody picked.
    timezone: Mapped[str | None] = mapped_column(String(TIMEZONE_NAME_LENGTH), nullable=True)
    email_verified_at: Mapped[OptionalTimestamp]
    # Sessions created before this are refused (migration 0019). NULL means no
    # reset has happened. It is the whole of "a password reset ends every live
    # session": one write, no session list to walk, and this comparison rides a
    # user row every request already loads.
    sessions_valid_from: Mapped[OptionalTimestamp]
    # The TOTP shared secret, sealed by `auth/encryption.py` and authenticated
    # against this row's id — so a copy of somebody else's ciphertext will not
    # open here. Encrypted rather than hashed because verifying a code means
    # recomputing an HMAC with the same secret the phone holds; it is the one
    # credential in this schema that has to survive in a recoverable form.
    totp_secret: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # When the customer proved they could produce a code from that secret. **This
    # column, not the secret, is what switches the second factor on**: a secret
    # stored without a confirmed code would lock out the person who mistyped it
    # into their authenticator. NULL means the account signs in with a password
    # alone. The CHECK in migration 0025 keeps the pair honest.
    totp_confirmed_at: Mapped[OptionalTimestamp]
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    invited_at: Mapped[OptionalTimestamp]
    # The set-password link an invite (or an onboarded owner) carries, as its
    # SHA-256; the plaintext is only ever in the email. Both null once spent.
    set_password_token_hash: Mapped[str | None] = mapped_column(
        String(SET_PASSWORD_TOKEN_HASH_LENGTH), nullable=True
    )
    set_password_expires_at: Mapped[OptionalTimestamp]
    # Null when the invite email did not go, so the console can say so and offer
    # a resend rather than leave a person waiting for mail that was never sent.
    invite_sent_at: Mapped[OptionalTimestamp]
    # Removed from the organization. The row stays so history keeps the name;
    # the address is rewritten so it is free to sign up again (F26, BR5, BR6).
    removed_at: Mapped[OptionalTimestamp]
    # Failed sign-ins in a row, and the lock they earned (`auth/lockout.py`,
    # migration 0039). A full sign-in sets both back.
    failed_logins: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    locked_until: Mapped[OptionalTimestamp]
    created_at: Mapped[CreatedAt]

    __table_args__ = (
        Index("ix_users_org_id", "org_id"),
        # So other tables can key on (org_id, id) and the database refuses a
        # cross-tenant assignee, actor or author outright (F22, D34).
        UniqueConstraint("org_id", "id", name="uq_users_org_id_id"),
        CheckConstraint(
            "(totp_secret IS NULL) = (totp_confirmed_at IS NULL)",
            name="totp_secret_and_confirmation_agree",
        ),
        CheckConstraint(
            "(set_password_token_hash IS NULL) = (set_password_expires_at IS NULL)",
            name="set_password_link_and_expiry_agree",
        ),
        Index(
            "uq_users_set_password_token_hash",
            "set_password_token_hash",
            unique=True,
            postgresql_where=text("set_password_token_hash IS NOT NULL"),
        ),
    )


class UserRecoveryCode(Base):
    """One single-use way back in when the authenticator is gone.

    **No `org_id`, and that is a decision rather than an omission.** The rule at
    the top of this module puts the tenant on every tenant-scoped table so the
    filter is one column away. Nothing here is ever found by tenant: a code is
    looked up as "this signed-in person's own", from the user id the session
    names, and there is no query for an `org_id` predicate to protect. A column
    no statement reads would be a claim of protection that is not there — which
    is the mistake migration 0006 removed when `config_encrypted` turned out to
    hold plain JSON. Erasure still reaches these rows: the foreign key below
    cascades from `users`, which a tenant deletion removes directly.

    Argon2 in `code_hash`, for the reason `auth/recovery_codes.py` sets out: a
    code short enough to copy off paper is short enough to need a slow hash.

    `used_at` rather than a delete, so "one of your recovery codes was used on
    Tuesday" stays answerable.
    """

    __tablename__ = "user_recovery_codes"

    id: Mapped[UuidPrimaryKey]
    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[CreatedAt]
    used_at: Mapped[OptionalTimestamp]

    __table_args__ = (Index("ix_user_recovery_codes_user_id", "user_id"),)


class Plan(Base):
    """What a tier grants. Rows, not constants: these are the numbers most
    likely to change after contact with customers, and changing one should not
    need a deploy. Seeded by migration 0005."""

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
    # worker stamps on every row so a part can expire as a whole.
    retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text(str(DEFAULT_RETENTION_DAYS))
    )
    sampling_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Which default masking rules this project switches off, and what it adds.
    # Empty means the shipped defaults apply, which is the intended state for
    # most projects — masking is irreversible, so the shipped rules cover only
    # what could never be legitimately needed to debug.
    masking_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # The IANA zone detection reads local minutes in, and finding titles name (FR24).
    timezone: Mapped[str] = mapped_column(
        String(TIMEZONE_NAME_LENGTH), nullable=False, server_default=DEFAULT_TIMEZONE
    )
    detection_sensitivity: Mapped[str] = mapped_column(
        String(SHORT_LABEL_LENGTH), nullable=False, server_default=DEFAULT_SENSITIVITY
    )
    created_at: Mapped[CreatedAt]
    deleted_at: Mapped[OptionalTimestamp]

    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_projects_org_id_slug"),
        Index("ix_projects_org_id", "org_id"),
        CheckConstraint(
            f"detection_sensitivity IN ({_quoted(SENSITIVITIES)})",
            name="detection_sensitivity_is_known",
        ),
    )


class ApiKey(Base):
    """A *secret* ingest credential — the server-side kind, and the only kind this
    table holds. Carries both ids so the receiver's hot path is one cached lookup
    rather than a key → project → org chain.

    The kind is the table, not a column. Browser keys live in
    `site_keys`, so the receiver's server-door lookup — which reads this table and
    then compares a hash — cannot return a public key even if someone forgot a
    filter. `key_hash` therefore stays NOT NULL: nothing here lacks one.
    """

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
    """Door 3's *public*, non-secret browser key — the other kind of ingest
    credential, and the only kind this table holds.

    It ships inside a page anyone can read, so it is restricted by origin rather
    than by secrecy, and it is stored whole and in the clear. There is no
    `key_hash` column and that is the point: hashing a value printed in every
    page's source protects nothing, and it would cost the exact-match index that
    makes the browser door's lookup a single probe.

    A separate table from `api_keys` for the same reason: it makes "a public key
    is accepted where a secret one was expected" unreachable at the SQL level
    rather than dependent on a `WHERE kind = …` nobody forgets.
    """

    __tablename__ = "site_keys"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # Named for the same reason api_keys rows are: five keys nobody can tell
    # apart are five keys nobody dares revoke.
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    public_key: Mapped[str] = mapped_column(String(SITE_KEY_LENGTH), nullable=False, unique=True)
    # Abuse mitigation, not an authentication boundary: a caller that sends no
    # Origin header is not filtered by one. Empty means the browser door accepts
    # nothing, which is why creating one requires at least one origin.
    allowed_origins: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    last_used_at: Mapped[OptionalTimestamp]
    created_at: Mapped[CreatedAt]
    revoked_at: Mapped[OptionalTimestamp]

    __table_args__ = (Index("ix_site_keys_project_id", "project_id"),)


# The kinds a check can be. Mirrored by a CHECK constraint, so a row the
# scheduler cannot run is refused by the database rather than scheduled and lost.
CHECK_TYPES: Final = ("api", "multistep", "heartbeat", "tcp", "dns")

# The states a run can confirm. Paused and in-maintenance are read off the check
# and its windows, not stored: neither is something a run found.
CHECK_STATES: Final = ("up", "degraded", "down", "misconfigured")

MAINTENANCE_RECURRENCES: Final = ("none", "daily", "weekly")

CHECK_NAME_MAX_LENGTH: Final = 120
FILE_NAME_MAX_LENGTH: Final = 255
CONTENT_TYPE_MAX_LENGTH: Final = 255
RUN_ID_LENGTH: Final = 36


class Check(Base):
    """A synthetic check. Our own Beat scheduler reads this table, so a customer
    editing a check in the console needs no redeploy.

    `config` is the whole definition, validated against
    `nexovue_platform.checks.config` before it is written; `type` repeats its
    discriminator so the database can refuse a kind nothing runs."""

    __tablename__ = "checks"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(CHECK_NAME_MAX_LENGTH), nullable=False)
    type: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    # For a heartbeat, the period a call is expected within.
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # Probe locations this check runs from, each independently.
    locations: Mapped[list[str]] = mapped_column(
        ARRAY(String(SHORT_LABEL_LENGTH)), nullable=False, server_default=text("'{}'")
    )
    # Response times above which a passing run is degraded, and a run fails.
    degraded_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failing_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # How many times a failure is retried before it is believed.
    retries: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    # A heartbeat's URL token, hashed like an API key: shown once, never stored.
    heartbeat_token_hash: Mapped[str | None] = mapped_column(
        String(SHA256_HEX_LENGTH), nullable=True, unique=True
    )
    last_ping_at: Mapped[OptionalTimestamp]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[CreatedAt]

    __table_args__ = (
        CheckConstraint(f"type IN ({_quoted(CHECK_TYPES)})", name="type_is_known"),
        CheckConstraint("interval_seconds BETWEEN 60 AND 86400", name="interval_is_within_bounds"),
        CheckConstraint("retries BETWEEN 1 AND 3", name="retries_are_bounded"),
        CheckConstraint(
            "degraded_ms IS NULL OR failing_ms IS NULL OR degraded_ms < failing_ms",
            name="degraded_is_below_failing",
        ),
        # The scheduler's only query is "every enabled check"; it runs every tick.
        Index("ix_checks_enabled_org_id", "enabled", "org_id"),
        Index("ix_checks_project_id", "project_id"),
    )


class CheckState(Base):
    """A check's confirmed state, and the run that confirmed it. One row per check."""

    __tablename__ = "check_states"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    check_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("checks.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    state: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    since: Mapped[RequiredTimestamp]
    run_id: Mapped[str | None] = mapped_column(String(RUN_ID_LENGTH), nullable=True)
    location: Mapped[str | None] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=True)
    # The last confirmed run, whether or not it changed anything.
    last_run_at: Mapped[RequiredTimestamp]

    __table_args__ = (
        CheckConstraint(f"state IN ({_quoted(CHECK_STATES)})", name="state_is_known"),
    )


class CheckStateChange(Base):
    """One confirmed transition. What an alert will be raised from (F21)."""

    __tablename__ = "check_state_changes"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    check_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("checks.id", ondelete="CASCADE"), nullable=False
    )
    # Null for the first state a check ever reaches.
    from_state: Mapped[str | None] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=True)
    to_state: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    changed_at: Mapped[RequiredTimestamp]
    run_id: Mapped[str | None] = mapped_column(String(RUN_ID_LENGTH), nullable=True)
    location: Mapped[str | None] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=True)
    # Why, in the run's own words: the failing assertion or the error.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_check_state_changes_check_id_changed_at", "check_id", "changed_at"),
    )


class CheckSecret(Base):
    """A value checks use and nobody reads back (ADR-010).

    Sealed with the check-secret key, bound to its project and name, so a value
    copied into another row fails to open rather than becoming that row's secret."""

    __tablename__ = "check_secrets"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    sealed_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[CreatedAt]

    __table_args__ = (UniqueConstraint("project_id", "name"),)


class CheckVariable(Base):
    """A plain value any check in the project can use as `{{name}}`."""

    __tablename__ = "check_variables"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[CreatedAt]

    __table_args__ = (UniqueConstraint("project_id", "name"),)


class CheckFile(Base):
    """A file a check sends in a request. The bytes are in object storage.

    `check_id` is null while the file belongs to a form nobody has saved yet, or
    after a check stopped using it. A sweep removes the first kind a day on and the
    second an hour on, row first and object after — so an abandoned form leaves
    nothing behind, and no object is ever deleted before the change that freed it
    has committed."""

    __tablename__ = "check_files"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    check_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("checks.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(FILE_NAME_MAX_LENGTH), nullable=False)
    content_type: Mapped[str] = mapped_column(String(CONTENT_TYPE_MAX_LENGTH), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[CreatedAt]
    # When a check stopped using it; the sweep takes a detached file an hour on.
    detached_at: Mapped[OptionalTimestamp]

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="size_is_not_negative"),
        Index("ix_check_files_check_id", "check_id"),
        Index(
            "ix_check_files_unattached_created_at",
            "created_at",
            postgresql_where=text("check_id IS NULL"),
        ),
    )


class MaintenanceWindow(Base):
    """Planned work during which a failing check changes no state (FR36)."""

    __tablename__ = "maintenance_windows"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(CHECK_NAME_MAX_LENGTH), nullable=False)
    starts_at: Mapped[RequiredTimestamp]
    ends_at: Mapped[RequiredTimestamp]
    recurrence: Mapped[str] = mapped_column(
        String(SHORT_LABEL_LENGTH), nullable=False, server_default=text("'none'")
    )
    repeat_until: Mapped[OptionalTimestamp]
    # Every check in the project, including ones created after the window.
    applies_to_all: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[CreatedAt]

    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ends_after_it_starts"),
        CheckConstraint(
            f"recurrence IN ({_quoted(MAINTENANCE_RECURRENCES)})", name="recurrence_is_known"
        ),
        Index("ix_maintenance_windows_project_id", "project_id"),
    )


class MaintenanceWindowCheck(Base):
    """One check a window covers, when it does not cover them all."""

    __tablename__ = "maintenance_window_checks"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    window_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("maintenance_windows.id", ondelete="CASCADE"), nullable=False
    )
    check_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("checks.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("window_id", "check_id"),
        Index("ix_maintenance_window_checks_check_id", "check_id"),
    )


class Integration(Base):
    """A door 4b connector.

    Holds no credential. The customer grants a role our account may assume and
    we keep its ARN plus an ExternalId we generated — neither is secret, so
    there is nothing here to encrypt."""

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
    # was called `config_encrypted`, and the assume-role design removed the reason
    # it would ever need to: a role ARN and an ExternalId are not secrets.
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
    # Why the last poll failed, in AWS's own words. `status` alone cannot tell a
    # customer that their trust policy is missing our account.
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_sync_at: Mapped[OptionalTimestamp]
    created_at: Mapped[CreatedAt]

    __table_args__ = (Index("ix_integrations_project_id", "project_id"),)


class DeployEvent(Base):
    """Door 4a lands here, not in ClickHouse: volume is tiny and the insight
    engine joins these against findings and cases. F28's envelope (0057): each item
    signs its own id and time, says how the rollout went, and names the release it
    filed under and the secret that signed it."""

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
    # The sender's claim, bounded at the door (FR8); `created_at` is when it arrived.
    deployed_at: Mapped[RequiredTimestamp]
    source: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    # The sender's own id for this item, inside the signed body (D6), unique per
    # project: a captured body sent again stores nothing, whatever its headers say.
    delivery_id: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    # `metadata` is taken on a declarative class, so the attribute differs from
    # the column name. Masked before it is stored (FR9).
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    state: Mapped[str] = mapped_column(
        String(SHORT_LABEL_LENGTH), nullable=False, server_default=text("'finished'")
    )
    # Lower-case hex, 40 or 64 characters, checked at the door.
    commit_sha: Mapped[str | None] = mapped_column(String(SHA256_HEX_LENGTH), nullable=True)
    # https only, no credentials, query or fragment; rendered, never fetched.
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Kept when the release is evicted or the secret goes, so the deploy outlives both.
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("releases.id", ondelete="SET NULL"), nullable=True
    )
    webhook_secret_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("project_webhook_secrets.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[CreatedAt]

    __table_args__ = (
        Index("uq_deploy_events_project_id_delivery_id", "project_id", "delivery_id", unique=True),
        Index(
            "ix_deploy_events_org_id_project_id_deployed_at", "org_id", "project_id", "deployed_at"
        ),
        # The retention sweep's (design 3), which would otherwise scan the table.
        Index("ix_deploy_events_created_at", "created_at"),
        # A release's deploys, for recalculating it, and its eviction's SET NULL.
        Index("ix_deploy_events_release_id", "release_id"),
        # What one secret signed since a time (D37).
        Index("ix_deploy_events_webhook_secret_id_created_at", "webhook_secret_id", "created_at"),
        CheckConstraint(f"state IN ({_quoted(DEPLOY_STATES)})", name="state_is_known"),
    )


class ProjectWebhookSecret(Base):
    """A shared secret CI signs deploy notifications with.

    Its own table rather than derived from an ingest key, because they are
    different credentials with different lifetimes: revoking a key should not
    silently stop deploys arriving. Many rows per project, so a rotation can
    overlap — create the new one, update CI, revoke the old.

    Sealed (F28 D7): HMAC verification needs the value itself, so it is encrypted
    rather than hashed, under the credential key, bound to this project and this
    row's id (`deploy_webhook_owner`). Shown once, at creation; afterwards only its
    last characters, so a person can tell one from another.
    """

    __tablename__ = "project_webhook_secrets"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    sealed_secret: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_hint: Mapped[str] = mapped_column(String(SECRET_HINT_LENGTH), nullable=False)
    created_at: Mapped[CreatedAt]
    last_used_at: Mapped[OptionalTimestamp]
    revoked_at: Mapped[OptionalTimestamp]

    __table_args__ = (
        Index("ix_webhook_secrets_project_id_revoked_at", "project_id", "revoked_at"),
    )


class DeployIntakeState(Base):
    """What the Deploys section shows of a project's intake (F28 design 2, D22): when a
    deploy and a dry-run check last arrived, and the last refusal a signed sender
    earned. One small row per project. An unsigned refusal is never recorded, or an
    outsider could fill it."""

    __tablename__ = "deploy_intake_state"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    last_check_at: Mapped[OptionalTimestamp]
    last_deploy_at: Mapped[OptionalTimestamp]
    last_refusal_at: Mapped[OptionalTimestamp]
    # 1-based, as the refusal named it; null for a refusal of the whole body.
    last_refusal_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_refusal_field: Mapped[str | None] = mapped_column(
        String(SHORT_LABEL_LENGTH), nullable=True
    )
    last_refusal_secret_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("project_webhook_secrets.id", ondelete="SET NULL"), nullable=True
    )


class SourceMap(Base):
    """One uploaded source map, so a minified browser stacktrace can be read.

    Metadata only. A source map is routinely larger than the bundle it describes
    and there is one per build, so the bytes live in object storage and
    `storage_key` names them. A key rather than a URL: a URL would bake one
    deployment's endpoint into rows that outlive it.

    Two ways to find the map for a stack frame. `debug_id` is the ECMA-426 id a
    build stamps into both the bundle and its map, which makes the lookup exact;
    it is null whenever the customer's bundler does not emit one, which is still
    the common case. `release` plus `minified_path` is the fallback, and
    `release` joins logically to `spans.deployment_version` and
    `deploy_events.version` — a cross-store join by string, not a foreign key.

    Neither lookup is unique. A re-uploaded build must supersede rather than
    fail, and which of several matching rows wins is the service's decision to
    make from `uploaded_at`.
    """

    __tablename__ = "source_maps"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    release: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    debug_id: Mapped[str | None] = mapped_column(String(DEBUG_ID_LENGTH), nullable=True)
    minified_path: Mapped[str] = mapped_column(String(MINIFIED_PATH_LENGTH), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(STORAGE_KEY_LENGTH), nullable=False)
    # Zero would be a truncated upload recorded as a success, which symbolication
    # would report as "no source map" rather than as the failure it was.
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_at: Mapped[CreatedAt]

    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="size_is_positive"),
        Index(
            "ix_source_maps_project_id_debug_id",
            "project_id",
            "debug_id",
            postgresql_where=text("debug_id IS NOT NULL"),
        ),
        Index(
            "ix_source_maps_project_id_release_minified_path",
            "project_id",
            "release",
            "minified_path",
        ),
    )


class UsageDaily(Base):
    """Ingestion counters rolled up per day. The guardrail meter, not the bill:
    Pricing is per service, and these counters are what shows a customer
    the volume behind that — so nobody is surprised by either number.
    `browser_bytes` is a part of `bytes`, not a fifth unit beside it."""

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
    # The part of `bytes` that came through the browser door, which the plan
    # budgets on its own. Never more than `bytes`.
    browser_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    # Profile samples (F17): metered and shown, never billed while profiling is in
    # preview — they add nothing to `bytes` and name no service (spec D1).
    profile_samples: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )


class UsageDailyService(Base):
    """One service a project sent something for, on one day: the billing unit.

    A set, not a counter. Distinct services cannot be accumulated by adding, and
    the meter is at-least-once — inserting the same name twice leaves the same
    set, which is what makes a replay harmless here without any watermark at
    all. The billable quantity is `count(*)` over these rows.
    """

    __tablename__ = "usage_daily_services"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    service_name: Mapped[str] = mapped_column(String(SERVICE_NAME_LENGTH), primary_key=True)
    first_seen_at: Mapped[CreatedAt]


class UsageIngestOffset(Base):
    """How far the usage meter has counted each ingestion partition.

    Written by the worker, in the same transaction as the counters (the
    ClickHouse counters' problem, solved by a transaction Postgres has and
    ClickHouse does not). The control plane reads one thing from it:
    `counted_through_received_at`, which is how far the meter has reached in
    *receive* time and therefore which days are finished. A null means a
    partition nothing has metered yet, which is why no day can be called settled
    while one exists.
    """

    __tablename__ = "usage_ingest_offsets"

    topic: Mapped[str] = mapped_column(Text, primary_key=True)
    partition_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    counted_through: Mapped[int] = mapped_column(BigInteger, nullable=False)
    counted_through_received_at: Mapped[OptionalTimestamp]
    updated_at: Mapped[CreatedAt]


class PlanPrice(Base):
    """What a tier costs, from a day onwards.

    Its own table rather than a column on `plans` because a price is temporal:
    editing a column would restate what every past invoice was computed from,
    while a row appended with a later `effective_from` leaves history alone. The
    price in force on a day is the latest row not after it.

    The unit is fixed — one service monitored, with a ceiling on the
    service-count component — and did not fix the amounts: they are rows for a
    person to insert, not constants for us to invent. ~~Empty on purpose.~~ It
    has not been empty since 0015 seeded three tiers and 0016 restated `free`
    with its browser share; `test_plan_prices_live.py` now guards the convention
    that a repricing restates every column, which only means anything because
    rows exist.
    """

    __tablename__ = "plan_prices"

    plan_slug: Mapped[str] = mapped_column(
        String(SHORT_LABEL_LENGTH),
        ForeignKey("plans.slug", ondelete="RESTRICT"),
        primary_key=True,
    )
    # In the key, so one tier can be priced in more than one currency at a time.
    # It was a plain column and the key was `(plan_slug, effective_from)`, which
    # meant a plan had exactly one currency and every customer everywhere was
    # quoted it. An Indian customer paying an Indian seller is charged rupees.
    currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), primary_key=True)
    effective_from: Mapped[date] = mapped_column(Date, primary_key=True)
    # Minor units, and named as such: the yen's minor unit is the yen, so a name
    # saying "cents" invites a divide by a hundred that misquotes the bill.
    price_per_service_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # NULL is uncapped, which is not the same statement as a cap of zero.
    max_billed_services: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The fewest services this plan can be bought for. NULL is no minimum.
    min_committed_services: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The volume allowance, in ingested bytes per billed service per
    # calendar month. NULL is "no allowance recorded", not an allowance of zero.
    included_bytes_per_service: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # The share of that allowance the browser door may spend, as a whole
    # percentage; the server door has the rest. NULL is "no split
    # recorded": both doors spend the one allowance.
    browser_share_percent: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    created_at: Mapped[CreatedAt]


class BillingSubscription(Base):
    """One paid arrangement between an organization and a payment processor.

    **What is stored is deliberately the least that works.** The rule is the
    processor's customer reference and nothing else avoidable, which is the
    integrations' posture — hold no credential we can avoid — applied to money:
    no card number, no PAN, no last four, no expiry, no bank account, no billing
    address. `Checkout` returns a URL rather than a form precisely so none of
    that ever reaches this application, and a column here would be the quiet
    way it arrived anyway.

    Rows accumulate rather than being edited in place across arrangements: a
    customer who cancels and later resubscribes gets a second row, because the
    first one is what a past invoice was raised under.
    """

    __tablename__ = "billing_subscriptions"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # Which implementation of the port this arrangement lives at. The vendor's
    # name is the *value* here and never the column name — the port's rule, the
    # same one that keeps `PAYMENTS_DOMESTIC_*` neutral. Two processors is the
    # expected end state, so a row has to say which one it belongs to.
    processor: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    # The processor's own name for this subscription, opaque and never parsed.
    # It is what a later cancel call names, and what every webhook echoes back.
    subscription_ref: Mapped[str] = mapped_column(
        String(PROCESSOR_REFERENCE_LENGTH), nullable=False
    )
    # The processor's id for the paying customer, when it issues one. NULL until
    # the first event that carries it, and NULL forever at a processor that has
    # no customer object — which is not the same statement as "unknown customer".
    processor_customer_ref: Mapped[str | None] = mapped_column(
        String(PROCESSOR_REFERENCE_LENGTH), nullable=True
    )
    # Ours (`pro`, `scale`), never a processor's price id. A foreign key rather
    # than free text for `organizations.plan`'s reason: a typo would name a tier
    # that grants whatever the resolver fell back to.
    plan_slug: Mapped[str] = mapped_column(
        String(SHORT_LABEL_LENGTH), ForeignKey("plans.slug", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(BillingSubscriptionStatus, nullable=False)
    # How many services the customer said they would pay for, which is what the
    # mandate's authorised maximum was computed from — the customer's ceiling, on the
    # arrangement that carries it rather than on the tenant. Raising the ceiling
    # means a new mandate at a new maximum, so it is a new row and not an edit.
    services_committed: Mapped[int] = mapped_column(Integer, nullable=False)
    # What the mandate was signed in. A subscription keeps debiting the currency
    # it was authorised in — converting per charge would give a different amount
    # every month as the rate moved, which is a debit nobody expected and a
    # chargeback. Stored because the plan's price rows now hold several, so the
    # tier alone no longer says which one this arrangement used.
    currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=False)
    created_at: Mapped[CreatedAt]
    # When the mandate was authorised, and when it ended. Timestamps rather than
    # flags, because "since when" is the question an invoice dispute asks.
    activated_at: Mapped[OptionalTimestamp]
    cancelled_at: Mapped[OptionalTimestamp]
    # Set when the processor gives up on the payments: the paid tier holds until
    # then, and the scheduler's grace sweep marks the row `lapsed` after it.
    grace_until: Mapped[OptionalTimestamp]

    __table_args__ = (
        # One arrangement per reference per processor. Two processors may
        # legitimately mint the same string, which is why the pair is unique
        # rather than the reference alone.
        UniqueConstraint("processor", "subscription_ref"),
        Index("ix_billing_subscriptions_org_id_status", "org_id", "status"),
    )


class BillingWebhookDelivery(Base):
    """One billing event that arrived, was proved, and was acted on.

    **The row exists to make a replay a no-op**, which is migration 0007's
    lesson applied to money: a signed request is valid forever unless something
    records that it already arrived. The processor delivers at least once by
    design, so a duplicate is ordinary traffic rather than an attack, and both
    are answered by the same unique index.

    **It holds no body, no signature and no header.** A raw body is the thing the
    signature covers and the thing a log must never carry; the translated
    `kind` is everything downstream ever needed from it.
    """

    __tablename__ = "billing_webhook_deliveries"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    processor: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    # The processor's id for this delivery, unique per unique payload.
    delivery_id: Mapped[str] = mapped_column(String(PROCESSOR_REFERENCE_LENGTH), nullable=False)
    # The translated event, in our vocabulary — a `BillingEventKind` value. The
    # processor's own event name is deliberately not stored: it is the thing the
    # port exists to keep out of everything above it.
    kind: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    subscription_ref: Mapped[str] = mapped_column(
        String(PROCESSOR_REFERENCE_LENGTH), nullable=False
    )
    # What the customer was debited, when the delivery carried it.
    #
    # **Recorded rather than recomputed**, which is the whole reason for the
    # column. It was derived as `rate x services_committed` from whatever the
    # plan charges *now*, so a rate change, a commitment change, or a checkout
    # spanning midnight UTC quietly rewrote history — and provider tax was never
    # in it at all. A charge is a fact about a moment, and a fact that can be
    # recalculated differently later is not one.
    #
    # Null for every event that is not a charge, and for a charge whose payload
    # stated no amount: a missing figure is missing, never nought.
    amount_minor_units: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # ISO 4217, as `plan_prices.currency` holds it. Beside the number rather than
    # inferred from the organization, because an integer alone is not an amount.
    amount_currency: Mapped[str | None] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=True)
    received_at: Mapped[CreatedAt]

    __table_args__ = (
        # The whole replay defence. Unique across organizations rather than
        # within one, because the id is the processor's and its uniqueness is
        # too: scoping it per tenant would let the same delivery be replayed
        # under a different organization's name.
        UniqueConstraint("processor", "delivery_id"),
        # A blank id would make every later blank delivery its duplicate.
        CheckConstraint("delivery_id <> ''", name="a_delivery_names_its_event"),
        Index("ix_billing_webhook_deliveries_org_id_received_at", "org_id", "received_at"),
    )


class AuditLogEntry(Base):
    """Who changed what.

    `actor_user_id` is null for actions the system took, and where the row must
    name nexovue rather than the account it acted on — a support view, which
    `actor_label` labels "nexovue support" for the customer reading it. `org_id`
    is null only for a refused sign-in against an address that matches no
    account, which belongs to no tenant.
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
    # How it changed, where that is more than the action says: a role's before
    # and after, the address an invite went to. Never a secret.
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    at: Mapped[CreatedAt]

    # `(at, id)` is the audit screen's page order; `at` alone ties within a
    # transaction.
    __table_args__ = (Index("ix_audit_log_org_id_at_id", "org_id", "at", "id"),)


# --------------------------------------------------------------------- alerts
# F21. The vocabulary is `nexovue_platform.alerts.model`, shared with the
# scheduler that evaluates rules and delivers notifications (ADR-012, ADR-013).

ALERT_NAME_MAX_LENGTH: Final = 120


class NotificationChannel(Base):
    """Where an organization's alerts go (FR1, D4).

    What may be shown is in `config`; what grants access to the destination — a
    webhook URL, a routing key, a signing secret — is `sealed_secret`, sealed with
    the check-secret key and bound to this row (ADR-010, amended).
    """

    __tablename__ = "notification_channels"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    name: Mapped[str] = mapped_column(String(ALERT_NAME_MAX_LENGTH), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    sealed_secret: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    quiet_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    muted_until: Mapped[OptionalTimestamp]
    muted_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    health: Mapped[str] = mapped_column(
        String(SHORT_LABEL_LENGTH), nullable=False, server_default=text("'healthy'")
    )
    failing_since: Mapped[OptionalTimestamp]
    last_delivered_at: Mapped[OptionalTimestamp]
    failing_reported_at: Mapped[OptionalTimestamp]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[CreatedAt]

    __table_args__ = (
        CheckConstraint(f"kind IN ({_quoted(CHANNEL_KINDS)})", name="kind_is_known"),
        CheckConstraint(f"health IN ({_quoted(CHANNEL_HEALTH)})", name="health_is_known"),
        Index("ix_notification_channels_org_id", "org_id"),
        # Exactly one owners channel per organization (FR2).
        Index(
            "uq_notification_channels_org_id_owners",
            "org_id",
            unique=True,
            postgresql_where=text("kind = 'owners'"),
        ),
    )


class AlertRule(Base):
    """A project's condition, and who hears when it fires (FR5)."""

    __tablename__ = "alert_rules"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(ALERT_NAME_MAX_LENGTH), nullable=False)
    condition_kind: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    # Validated by `nexovue_platform.alerts.model`, which the scheduler parses too.
    condition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    renotify_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    muted_until: Mapped[OptionalTimestamp]
    muted_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[CreatedAt]

    __table_args__ = (
        CheckConstraint(
            f"condition_kind IN ({_quoted(CONDITION_KINDS)})", name="condition_is_known"
        ),
        CheckConstraint(
            "renotify_minutes IS NULL OR renotify_minutes >= 30", name="renotify_is_bounded"
        ),
        Index(
            "ix_alert_rules_project_id_condition_kind",
            "project_id",
            "condition_kind",
            postgresql_where=text("enabled"),
        ),
    )


class AlertRuleChannel(Base):
    """One channel a rule sends to."""

    __tablename__ = "alert_rule_channels"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("alert_rules.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("notification_channels.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (Index("ix_alert_rule_channels_channel_id", "channel_id"),)


class AlertEvaluation(Base):
    """A threshold rule's position for one subject, so waits survive a restart."""

    __tablename__ = "alert_evaluations"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False
    )
    subject_key: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    phase_since: Mapped[RequiredTimestamp]
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    evaluated_at: Mapped[RequiredTimestamp]

    __table_args__ = (
        UniqueConstraint("rule_id", "subject_key"),
        CheckConstraint(f"phase IN ({_quoted(EVALUATION_PHASES)})", name="phase_is_known"),
    )


class Incident(Base):
    """One breach of one rule by one subject (FR9). At most one open per pair (BR1)."""

    __tablename__ = "incidents"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL, with the name kept: deleting a rule does not erase what it caught.
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True
    )
    rule_name: Mapped[str] = mapped_column(String(ALERT_NAME_MAX_LENGTH), nullable=False)
    condition_kind: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    subject_key: Mapped[str] = mapped_column(Text, nullable=False)
    subject_label: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    opened_at: Mapped[RequiredTimestamp]
    resolved_at: Mapped[OptionalTimestamp]
    is_event: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    flapping: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    muted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    # What a burn is about (F19, design *The link to findings*): typed, apart from its
    # identity, so links, withholding and PagerDuty compare columns, not JSON. Null for
    # every other kind.
    about_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    about_signal: Mapped[str | None] = mapped_column(Text, nullable=True)
    about_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    about_check_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)

    __table_args__ = (
        CheckConstraint(f"severity IN ({_quoted(SEVERITIES)})", name="severity_is_known"),
        CheckConstraint(
            f"condition_kind IN ({_quoted(CONDITION_KINDS)})", name="condition_is_known"
        ),
        CheckConstraint(
            f"about_signal IN ({_quoted(slo.ABOUT_SIGNALS)})", name="about_signal_is_known"
        ),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= opened_at", name="resolves_after_it_opens"
        ),
        Index(
            "uq_incidents_rule_id_subject_key_open",
            "rule_id",
            "subject_key",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
        Index("ix_incidents_project_id_opened_at", "project_id", "opened_at"),
        Index(
            "ix_incidents_project_id_open",
            "project_id",
            postgresql_where=text("resolved_at IS NULL"),
        ),
        Index("ix_incidents_rule_id_subject_key_opened_at", "rule_id", "subject_key", "opened_at"),
        # F19 FR13b, D58: one open "not being judged" notice per project, filed under no
        # rule. Limited to its own subjects, so deleting rules with open burns still works.
        Index(
            "uq_incidents_blind_open",
            "project_id",
            "subject_key",
            unique=True,
            postgresql_where=text(
                "rule_id IS NULL AND resolved_at IS NULL AND subject_key LIKE 'slo-project:%'"
            ),
        ),
    )


class Notification(Base):
    """A decision to tell one channel about one subject: the outbox (ADR-013).

    The subject is an incident, a finding (ADR-022), or for the project kinds —
    detection stopped and running again (D24) — the project alone.
    """

    __tablename__ = "notifications"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=True
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("findings.id", ondelete="CASCADE"), nullable=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    # Counts each repeat, joined signal or detection stop: a project's stops never end.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    severity: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    state: Mapped[str] = mapped_column(
        String(SHORT_LABEL_LENGTH), nullable=False, server_default=text("'pending'")
    )
    deliver_after: Mapped[CreatedAt]
    lease_until: Mapped[OptionalTimestamp]
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    outcome_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[CreatedAt]
    delivered_at: Mapped[OptionalTimestamp]
    # The PagerDuty alert this row went to, fixed at its subject's first claim on the
    # channel, so a finding and a linked incident share one alert (D29, D35).
    dedup_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # F19 (FR22, FR23, FR26): why a `dropped` row was withheld, the subject that had told
    # the channel the same news, and when it stopped being held. No foreign keys: the
    # withholding subject stays a fact of this row, and one that has gone withholds nothing.
    withheld_why: Mapped[str | None] = mapped_column(Text, nullable=True)
    withheld_by_incident_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    withheld_by_finding_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    released_at: Mapped[OptionalTimestamp]

    __table_args__ = (
        UniqueConstraint("incident_id", "channel_id", "kind", "sequence"),
        UniqueConstraint("finding_id", "channel_id", "kind", "sequence"),
        Index(
            "uq_notifications_project_id_channel_id_kind_sequence_project",
            "project_id",
            "channel_id",
            "kind",
            "sequence",
            unique=True,
            postgresql_where=text("incident_id IS NULL AND finding_id IS NULL"),
        ),
        CheckConstraint(
            "num_nonnulls(incident_id, finding_id) = "
            f"CASE WHEN kind IN ({_quoted(PROJECT_NOTIFICATION_KINDS)}) THEN 0 ELSE 1 END",
            name="names_one_subject",
        ),
        CheckConstraint(f"kind IN ({_quoted(NOTIFICATION_KINDS)})", name="kind_is_known"),
        CheckConstraint(f"state IN ({_quoted(NOTIFICATION_STATES)})", name="state_is_known"),
        CheckConstraint(f"severity IN ({_quoted(SEVERITIES)})", name="severity_is_known"),
        Index(
            "ix_notifications_due",
            "deliver_after",
            postgresql_where=text("state IN ('pending', 'delivering')"),
        ),
        Index("ix_notifications_channel_id_state", "channel_id", "state"),
        Index(
            "ix_notifications_channel_id_dedup_key",
            "channel_id",
            "dedup_key",
            postgresql_where=text("dedup_key IS NOT NULL"),
        ),
        Index("ix_notifications_batch_id", "batch_id"),
        CheckConstraint(
            f"withheld_why IN ({_quoted(WITHHELD_REASONS)})", name="withheld_why_is_known"
        ),
        Index(
            "ix_notifications_withheld_unreleased",
            "org_id",
            "channel_id",
            postgresql_where=text("withheld_why IS NOT NULL AND released_at IS NULL"),
        ),
    )


class NotificationAttempt(Base):
    """One try at delivering one batch: the delivery log (FR20), kept 30 days."""

    __tablename__ = "notification_attempts"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    attempted_at: Mapped[CreatedAt]
    outcome: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    http_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What it carried: an alert, or somebody pressing Send test.
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="notification")

    __table_args__ = (
        CheckConstraint(f"outcome IN ({_quoted(ATTEMPT_OUTCOMES)})", name="outcome_is_known"),
        CheckConstraint(f"kind IN ({_quoted(ATTEMPT_KINDS)})", name="kind_is_known"),
        Index("ix_notification_attempts_channel_id_attempted_at", "channel_id", "attempted_at"),
        Index("ix_notification_attempts_batch_id", "batch_id"),
        Index("ix_notification_attempts_attempted_at", "attempted_at"),
    )


# ----------------------------------------------------------- anomaly detection
# F6. The vocabulary is `nexovue_platform.detection`, shared with the scheduler
# that judges services and writes these rows (ADR-022, ADR-023).


class Finding(Base):
    """One detected problem with one service, or with a project (ADR-022).

    At most one open per service and one per project, held by two partial unique
    indexes because Postgres treats nulls as distinct.
    """

    __tablename__ = "findings"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    service_name: Mapped[str | None] = mapped_column(String(SERVICE_NAME_LENGTH), nullable=True)
    # The highest reached; it only rises (FR14).
    severity: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    state: Mapped[str] = mapped_column(
        String(SHORT_LABEL_LENGTH), nullable=False, server_default=text("'open'")
    )
    closed_reason: Mapped[str | None] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=True)
    muted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Opened a fifth time within an hour: said once as flapping, then not again (C7).
    flapping: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # SET NULL: deleting an organization deletes its users before its projects.
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    acknowledged_at: Mapped[OptionalTimestamp]
    # Dated apart from the person, so the mark still counts (FR26) after they go.
    false_alarm_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    false_alarm_at: Mapped[OptionalTimestamp]
    # Only an admin's mark returns the finding's minutes to the normal (D28).
    false_alarm_returns_minutes: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    feedback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # Each signal's normal, current and worst values and the comparison (AC40), as a
    # list with the leading signal first.
    signals: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    # A project finding's services, which its page lists after it closes (0046).
    services: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    started_at: Mapped[RequiredTimestamp]
    closed_at: Mapped[OptionalTimestamp]
    updated_at: Mapped[CreatedAt]

    # ---- the case (F22, BR1, BR2): whether people are working, apart from `state`.
    # Null means no case; there is no separate case record, by design.
    case_state: Mapped[str | None] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=True)
    case_resolution: Mapped[str | None] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=True)
    # Composite keys to `users (org_id, id)`: a cross-tenant assignee is refused
    # by the database itself (D34), and a deleted user clears only the column.
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    case_opened_at: Mapped[OptionalTimestamp]
    case_resolved_at: Mapped[OptionalTimestamp]
    # Who resolved it; null for the system or a tracker.
    case_resolved_by: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    # The last person's act, or entry to `investigating` by any route (D42, D74).
    case_activity_at: Mapped[OptionalTimestamp]
    # The first act beyond taking: a note, a link, a reassign, a reopen (FR49).
    case_worked_at: Mapped[OptionalTimestamp]
    # The assignee's last act, starting at the assignment (FR12's "has not acted").
    assignee_acted_at: Mapped[OptionalTimestamp]
    # When the 14-day idle warning started; null when none, reset by activity.
    case_warned_at: Mapped[OptionalTimestamp]
    # The case this finding joined (FR9); a finding holds a case or joins one.
    root_finding_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)

    __table_args__ = (
        CheckConstraint(f"scope IN ({_quoted(FINDING_SCOPES)})", name="scope_is_known"),
        CheckConstraint(f"severity IN ({_quoted(SEVERITIES)})", name="severity_is_known"),
        CheckConstraint(f"state IN ({_quoted(FINDING_STATES)})", name="state_is_known"),
        CheckConstraint(
            f"closed_reason IS NULL OR closed_reason IN ({_quoted(CLOSED_REASONS)})",
            name="closed_reason_is_known",
        ),
        CheckConstraint(
            "(scope = 'service') = (service_name IS NOT NULL)", name="service_matches_scope"
        ),
        CheckConstraint("(state = 'open') = (closed_reason IS NULL)", name="closed_says_how"),
        CheckConstraint("(state = 'open') = (closed_at IS NULL)", name="closed_says_when"),
        CheckConstraint(
            "closed_at IS NULL OR closed_at >= started_at", name="closes_after_it_starts"
        ),
        CheckConstraint(
            "acknowledged_by IS NULL OR acknowledged_at IS NOT NULL",
            name="acknowledgement_is_dated",
        ),
        CheckConstraint(
            "false_alarm_at IS NOT NULL "
            "OR (false_alarm_by IS NULL AND NOT false_alarm_returns_minutes)",
            name="false_alarm_is_dated",
        ),
        Index(
            "uq_findings_project_id_service_name_open",
            "project_id",
            "service_name",
            unique=True,
            postgresql_where=text("scope = 'service' AND state = 'open'"),
        ),
        Index(
            "uq_findings_project_id_open",
            "project_id",
            unique=True,
            postgresql_where=text("scope = 'project' AND state = 'open'"),
        ),
        Index(
            "ix_findings_org_id_project_id_state_started_at",
            "org_id",
            "project_id",
            "state",
            text("started_at DESC"),
        ),
        Index(
            "ix_findings_project_id_service_name_started_at",
            "project_id",
            "service_name",
            "started_at",
        ),
        # The expiry sweep's purge at the derived retention (security m4; migration 0048).
        Index(
            "ix_findings_closed_at",
            "closed_at",
            postgresql_where=text("state = 'closed'"),
        ),
        # ---- the case (F22, migration 0051).
        UniqueConstraint("org_id", "id", name="uq_findings_org_id_id"),
        UniqueConstraint("org_id", "project_id", "id", name="uq_findings_org_id_project_id_id"),
        ForeignKeyConstraint(
            ["org_id", "assignee_id"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (assignee_id)",
            name="fk_findings_assignee_id_users",
        ),
        ForeignKeyConstraint(
            ["org_id", "case_resolved_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (case_resolved_by)",
            name="fk_findings_case_resolved_by_users",
        ),
        # A root is a finding of the same organization and project (AC72).
        ForeignKeyConstraint(
            ["org_id", "project_id", "root_finding_id"],
            ["findings.org_id", "findings.project_id", "findings.id"],
            ondelete="SET NULL (root_finding_id)",
            name="fk_findings_root_finding_id_findings",
        ),
        CheckConstraint(
            f"case_state IS NULL OR case_state IN ({_quoted(CASE_STATES)})",
            name="case_state_is_known",
        ),
        CheckConstraint(
            f"case_resolution IS NULL OR case_resolution IN ({_quoted(CASE_RESOLUTIONS)})",
            name="case_resolution_is_known",
        ),
        CheckConstraint("(case_state IS NULL) = (case_opened_at IS NULL)", name="case_is_dated"),
        CheckConstraint(
            "(case_state IS NULL) = (case_activity_at IS NULL)", name="case_activity_is_dated"
        ),
        CheckConstraint(
            "(case_state IS NOT DISTINCT FROM 'resolved') = (case_resolved_at IS NOT NULL)",
            name="case_resolved_says_when",
        ),
        CheckConstraint(
            "(case_state IS NOT DISTINCT FROM 'resolved') = (case_resolution IS NOT NULL)",
            name="case_resolved_says_how",
        ),
        CheckConstraint(
            "assignee_id IS NULL OR case_state IS NOT NULL", name="assignee_needs_a_case"
        ),
        # A finding holds a case or joins one, never both (BR1, BR7).
        CheckConstraint(
            "case_state IS NULL OR root_finding_id IS NULL", name="holds_or_joins_never_both"
        ),
        CheckConstraint("root_finding_id IS DISTINCT FROM id", name="never_its_own_root"),
        # BR7, D21: one active case per service and one per project.
        Index(
            "uq_findings_project_service_active_case",
            "project_id",
            "service_name",
            unique=True,
            postgresql_where=text("scope = 'service' AND case_state = 'investigating'"),
        ),
        Index(
            "uq_findings_project_active_case",
            "project_id",
            unique=True,
            postgresql_where=text("scope = 'project' AND case_state = 'investigating'"),
        ),
        # The case filters read across the organization (FR22), the sweep reads
        # active cases, and the open-joined rank reads by root.
        Index(
            "ix_findings_org_id_assignee_id",
            "org_id",
            "assignee_id",
            postgresql_where=text("assignee_id IS NOT NULL"),
        ),
        Index(
            "ix_findings_org_id_case_resolved_at",
            "org_id",
            text("case_resolved_at DESC"),
            postgresql_where=text("case_state = 'resolved'"),
        ),
        Index(
            "ix_findings_org_id_active_case",
            "org_id",
            postgresql_where=text("case_state = 'investigating'"),
        ),
        Index(
            "ix_findings_org_id_root_finding_id",
            "org_id",
            "root_finding_id",
            postgresql_where=text("root_finding_id IS NOT NULL"),
        ),
    )


class FindingIncident(Base):
    """A finding linked to a threshold or *went silent* incident on its service (BR2)."""

    __tablename__ = "finding_incidents"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("findings.id", ondelete="CASCADE"), primary_key=True
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (Index("ix_finding_incidents_incident_id", "incident_id"),)


class FindingExplanation(Base):
    """One version of a finding's frozen evidence (F3, design *Data and migration impact*).

    A gather writes a new version only when what is known changed (FR43): identities and
    conclusions, never moved counts. `evidence` holds names and numbers and never a log
    body or error text (A47); `model_input` holds only what a model may be sent, and
    `aliases` the real names behind it, apart (A19). The wording columns stay `off`
    until AI wording exists (plan Step 5).
    """

    __tablename__ = "finding_explanations"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    read_at: Mapped[CreatedAt]
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    reads_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    aliases: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    likely_cause: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_input: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    wording: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    wording_state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'off'"))
    wording_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    wording_changed_at: Mapped[OptionalTimestamp]
    wording_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    wording_from_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # A person's last "Read the facts again" (FR41: once a minute per finding).
    read_again_at: Mapped[OptionalTimestamp]

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_finding_explanations_org_id_id"),
        UniqueConstraint("finding_id", "version", name="uq_finding_explanations_version"),
        ForeignKeyConstraint(
            ["org_id", "finding_id"],
            ["findings.org_id", "findings.id"],
            ondelete="CASCADE",
            name="fk_finding_explanations_finding",
        ),
        CheckConstraint("version >= 1", name="version_counts_from_one"),
        CheckConstraint("reads_failed >= 0", name="reads_failed_count_up"),
        CheckConstraint(
            f"wording_state IN ({_quoted(WORDING_STATES)})", name="wording_state_is_known"
        ),
        CheckConstraint(
            f"wording_reason IS NULL OR wording_reason IN ({_quoted(WORDING_REASONS)})",
            name="wording_reason_is_known",
        ),
        CheckConstraint("wording_generation >= 0", name="wording_generation_counts_up"),
    )


class EvidenceErrorGroupRead(Base):
    """The hour's "error groups not seen in the hour before", per project and minute (F3
    A62): read once in a wide outage, and holding no message text (A47)."""

    __tablename__ = "evidence_error_group_reads"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    read_minute: Mapped[datetime] = mapped_column(TimestampTz, primary_key=True)
    groups: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[CreatedAt]

    __table_args__ = (Index("ix_evidence_error_group_reads_created_at", "created_at"),)


class FindingEvent(Base):
    """The timeline (F22, FR16): what happened to one finding, append-only.

    `detail` is written only through `nexovue_platform.cases
    .finding_event_parameters`, which admits enums, ids and numbers and refuses
    free text (BR9, AC24). A person reads all of it; a token reads kinds and
    times alone (FR20).
    """

    __tablename__ = "finding_events"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # Where it happened: a joined finding's events merge into its root's timeline.
    finding_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    kind: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    at: Mapped[RequiredTimestamp]

    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "finding_id"],
            ["findings.org_id", "findings.id"],
            ondelete="CASCADE",
            name="fk_finding_events_finding",
        ),
        ForeignKeyConstraint(
            ["org_id", "actor_id"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (actor_id)",
            name="fk_finding_events_actor",
        ),
        CheckConstraint(f"kind IN ({_quoted(FINDING_EVENT_KINDS)})", name="kind_is_known"),
        CheckConstraint(
            f"actor_kind IN ({_quoted(EVENT_ACTOR_KINDS)})", name="actor_kind_is_known"
        ),
        Index(
            "ix_finding_events_org_id_finding_id_at",
            "org_id",
            "finding_id",
            text("at DESC"),
            text("id DESC"),
        ),
    )


class FindingNote(Base):
    """What a person wrote on a case (F22, FR14): the organization's working record
    (BR16). Shown as plain text, deletable by its author or an admin, never edited.
    A delete is a real DELETE: a pasted secret must actually go."""

    __tablename__ = "finding_notes"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # Always the root: a note written on a joined finding's page lands here (FR12).
    finding_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    # Kept when a member is scrubbed; the screen reads *Former member*.
    author_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[CreatedAt]

    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "finding_id"],
            ["findings.org_id", "findings.id"],
            ondelete="CASCADE",
            name="fk_finding_notes_finding",
        ),
        ForeignKeyConstraint(
            ["org_id", "author_id"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (author_id)",
            name="fk_finding_notes_author",
        ),
        CheckConstraint(f"length(body) <= {NOTE_MAX_LENGTH}", name="a_note_is_not_a_document"),
        Index(
            "ix_finding_notes_org_id_finding_id_created_at",
            "org_id",
            "finding_id",
            "created_at",
        ),
        # The personal-data export reads a person's own notes.
        Index("ix_finding_notes_org_id_author_id", "org_id", "author_id"),
    )


class FindingEmailOwed(Base):
    """One owed case email (F22, D90): the sweep deletes the row, commits, then
    sends, so a crash loses an email and can never repeat one. A row per email,
    because one column would lose a second email owed before the sweep runs. It
    holds no name, address or text."""

    __tablename__ = "finding_emails_owed"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    kind: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    at: Mapped[CreatedAt]

    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "finding_id"],
            ["findings.org_id", "findings.id"],
            ondelete="CASCADE",
            name="fk_finding_emails_owed_finding",
        ),
        CheckConstraint(f"kind IN ({_quoted(CASE_EMAIL_KINDS)})", name="kind_is_known"),
        Index("ix_finding_emails_owed_at", "at"),
    )


class Connection(Base):
    """A tracker connection (F22, FR28): one live row per organization and kind. On a
    self-hosted install a GitHub connection is the organization's own private App,
    whose key and webhook secret are sealed under `CONNECTION_SECRET_KEY` and opened
    only by `scheduler-connections` (FD10). Every sealed column is in
    `keys.SEALED_COLUMNS`. `github_fix` is F5's App, which F22 ignores."""

    __tablename__ = "connections"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    sealed_credential: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    sealed_webhook_secret: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # GitHub's one-hour manifest code, between the redirect and its conversion (AC74).
    sealed_manifest_code: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # SHA-256 of a setup's one-time `state`, in hex like every other token hash; the raw
    # value is never stored. Not binary: every binary column holds a sealed value (0062).
    setup_state_hash: Mapped[str | None] = mapped_column(String(SHA256_HEX_LENGTH), nullable=True)
    setup_expires_at: Mapped[OptionalTimestamp]
    setup_user_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    github_app_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    github_app_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_installation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    jira_site: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    sync_lease_until: Mapped[OptionalTimestamp]
    paused_until: Mapped[OptionalTimestamp]
    last_ok_at: Mapped[OptionalTimestamp]
    failing_since: Mapped[OptionalTimestamp]
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    last_failure: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_failure_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    failure_told_at: Mapped[OptionalTimestamp]
    expiry_told_at: Mapped[OptionalTimestamp]
    repositories_read_at: Mapped[OptionalTimestamp]
    created_at: Mapped[CreatedAt]

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_connections_org_id_id"),
        ForeignKeyConstraint(
            ["org_id", "setup_user_id"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (setup_user_id)",
            name="fk_connections_setup_user",
        ),
        CheckConstraint(f"kind IN ({_quoted(CONNECTION_KINDS)})", name="kind_is_known"),
        CheckConstraint(f"state IN ({_quoted(CONNECTION_STATES)})", name="state_is_known"),
        CheckConstraint(
            f"last_failure IS NULL OR last_failure IN ({_quoted(CONNECTION_FAILURES)})",
            name="failure_is_known",
        ),
        Index(
            "uq_connections_org_id_kind_live",
            "org_id",
            "kind",
            unique=True,
            postgresql_where=text("state <> 'disconnected'"),
        ),
        Index(
            "uq_connections_github_installation",
            "github_app_id",
            "github_installation_id",
            unique=True,
        ),
    )


class ConnectionRepository(Base):
    """What a connection reaches: a GitHub repository or a Jira project, by its strict
    key, never its free-text name. A *place* in code (F22, design *Connections*)."""

    __tablename__ = "connection_repositories"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False)
    public_allowed_by: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    public_allowed_at: Mapped[OptionalTimestamp]
    public_told_at: Mapped[OptionalTimestamp]
    jira_issue_type_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    jira_done_transition: Mapped[str | None] = mapped_column(Text, nullable=True)
    jira_reopen_transition: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_reachable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    created_at: Mapped[CreatedAt]

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_connection_repositories_org_id_id"),
        UniqueConstraint(
            "connection_id", "external_id", name="uq_connection_repositories_external_id"
        ),
        ForeignKeyConstraint(
            ["org_id", "connection_id"],
            ["connections.org_id", "connections.id"],
            ondelete="CASCADE",
            name="fk_connection_repositories_connection",
        ),
        ForeignKeyConstraint(
            ["org_id", "public_allowed_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (public_allowed_by)",
            name="fk_connection_repositories_public_allowed_by",
        ),
    )


class FindingLink(Base):
    """A case's tracker issue (F22, FR35 to FR41): the one synced issue whose status
    follows the case, or a reference shown beside it. No address is stored (D81): the
    console builds it from `external_key`, which a strict pattern holds. `seen_state`
    and `wanted_state` are what the reconcile compares (design, *The comparison*)."""

    __tablename__ = "finding_links"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # Always the case's root.
    finding_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    place_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    sync_status: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    seen_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    wanted_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment_wanted: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment_written_at: Mapped[OptionalTimestamp]
    # The one idle-warning comment this link may carry in its case's life (BR10).
    idle_warning_at: Mapped[OptionalTimestamp]
    # Closed in the tracker while the case signal was open: a hold (D73).
    tracker_closed_at: Mapped[OptionalTimestamp]
    # The preview's facts, while the issue is being created (AC83). `None` is SQL NULL,
    # not JSON `null`, or the check that facts live only while creating would refuse it.
    frozen_facts: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    first_attempt_at: Mapped[OptionalTimestamp]
    next_sync_at: Mapped[OptionalTimestamp]
    last_synced_at: Mapped[OptionalTimestamp]
    gone_at: Mapped[OptionalTimestamp]
    failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    last_failure: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_failure_field: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[CreatedAt]

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_finding_links_org_id_id"),
        UniqueConstraint(
            "finding_id", "connection_id", "external_id", name="uq_finding_links_issue_per_case"
        ),
        ForeignKeyConstraint(
            ["org_id", "finding_id"],
            ["findings.org_id", "findings.id"],
            ondelete="CASCADE",
            name="fk_finding_links_finding",
        ),
        ForeignKeyConstraint(
            ["org_id", "connection_id"],
            ["connections.org_id", "connections.id"],
            ondelete="SET NULL (connection_id)",
            name="fk_finding_links_connection",
        ),
        ForeignKeyConstraint(
            ["org_id", "place_id"],
            ["connection_repositories.org_id", "connection_repositories.id"],
            ondelete="SET NULL (place_id)",
            name="fk_finding_links_place",
        ),
        CheckConstraint(f"role IN ({_quoted(LINK_ROLES)})", name="role_is_known"),
        CheckConstraint(f"sync_status IN ({_quoted(LINK_STATUSES)})", name="status_is_known"),
        CheckConstraint(
            f"(seen_state IS NULL OR seen_state IN ({_quoted(ISSUE_STATES)})) "
            f"AND (wanted_state IS NULL OR wanted_state IN ({_quoted(ISSUE_STATES)}))",
            name="issue_states_are_known",
        ),
        CheckConstraint(
            f"comment_wanted IS NULL OR comment_wanted IN ({_quoted(COMMENT_KINDS)})",
            name="comment_is_known",
        ),
        CheckConstraint(
            f"last_failure IS NULL OR last_failure IN ({_quoted(LINK_FAILURES)})",
            name="failure_is_known",
        ),
        CheckConstraint(
            "(sync_status = 'creating') = (frozen_facts IS NOT NULL)",
            name="facts_frozen_while_creating",
        ),
        CheckConstraint(
            "sync_status <> 'creating' OR role = 'synced'", name="only_a_synced_link_is_created"
        ),
        CheckConstraint(
            "sync_status IN ('creating', 'refused', 'stopped') OR external_key IS NOT NULL",
            name="a_link_names_its_issue",
        ),
        CheckConstraint("failures >= 0", name="failures_count_up"),
        Index(
            "uq_finding_links_one_synced_per_case",
            "finding_id",
            unique=True,
            postgresql_where=text("role = 'synced' AND sync_status <> 'stopped'"),
        ),
        Index(
            "uq_finding_links_synced_once",
            "connection_id",
            "external_id",
            unique=True,
            postgresql_where=text("role = 'synced' AND sync_status <> 'stopped'"),
        ),
        Index(
            "ix_finding_links_due",
            "connection_id",
            "next_sync_at",
            postgresql_where=text("next_sync_at IS NOT NULL"),
        ),
        Index("ix_finding_links_org_id_finding_id", "org_id", "finding_id"),
    )


# `org_id` leads the key of every table below that is upserted, so no conflict
# can reach another tenant's row (design, *Findings and their state*).


class DetectionState(Base):
    """Where one service's judging stands. Created when the service is first seen
    (C24); `last_judged_minute` moves by compare-and-set."""

    __tablename__ = "detection_state"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    service_name: Mapped[str] = mapped_column(String(SERVICE_NAME_LENGTH), primary_key=True)
    # Null until the service's first minute is judged.
    last_judged_minute: Mapped[OptionalTimestamp]
    # Each signal's phase, clear-minute count, false-alarm hold and learning date.
    signals: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    created_at: Mapped[CreatedAt]


class DetectionProjectRun(Base):
    """When a project's detection job last completed, and since when it has been
    expected to: its freshness (design 2.1, item 2; D34).

    Apart from `projects` because it is written every minute for every project.
    """

    __tablename__ = "detection_project_runs"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    # None until the project's first run completes.
    last_completed_at: Mapped[OptionalTimestamp]
    # When the stop sweep began expecting the project judged, in its current spell of
    # traffic or open finding; None while detection does not work on it (D34).
    expected_since: Mapped[OptionalTimestamp]
    # Set when the owners are told detection stopped; cleared when told it runs again (D24).
    stopped_reported_at: Mapped[OptionalTimestamp]
    # The earliest minute a service of the project is losing traffic from, as of its
    # last judged minute: its vote for the install-wide guard (design 2.1, item 1).
    traffic_lost_since: Mapped[OptionalTimestamp]


class DetectionBaseline(Base):
    """The normal one service's signal is judged by at one minute, from the hourly job.

    The minute is absolute, so the two UTC minutes of a fall-back hour each have one.
    """

    __tablename__ = "detection_baselines"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    service_name: Mapped[str] = mapped_column(String(SERVICE_NAME_LENGTH), primary_key=True)
    signal: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), primary_key=True)
    minute: Mapped[datetime] = mapped_column(TimestampTz, primary_key=True)
    # The mode, days, median, spread, burst profile and L.
    baseline: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[CreatedAt]

    __table_args__ = (CheckConstraint(f"signal IN ({_quoted(SIGNALS)})", name="signal_is_known"),)


class BaselineShift(Base):
    """A signal's normal moved by a ratio from a minute: new normal or adoption (FR21)."""

    __tablename__ = "baseline_shifts"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    service_name: Mapped[str] = mapped_column(String(SERVICE_NAME_LENGTH), nullable=False)
    signal: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    from_minute: Mapped[RequiredTimestamp]
    ratio: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    created_at: Mapped[CreatedAt]

    __table_args__ = (
        CheckConstraint(f"signal IN ({_quoted(SIGNALS)})", name="signal_is_known"),
        CheckConstraint(f"reason IN ({_quoted(SHIFT_REASONS)})", name="reason_is_known"),
        CheckConstraint("ratio > 0", name="ratio_is_positive"),
        Index(
            "ix_baseline_shifts_project_id_service_name_signal_from_minute",
            "project_id",
            "service_name",
            "signal",
            "from_minute",
        ),
    )


class ServiceDetectionSettings(Base):
    """A service's mute (admins, BR9) and snooze (members, D25). Per-service
    sensitivity is deferred (D32)."""

    __tablename__ = "service_detection_settings"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    service_name: Mapped[str] = mapped_column(String(SERVICE_NAME_LENGTH), primary_key=True)
    muted_until: Mapped[OptionalTimestamp]
    muted_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    snoozed_until: Mapped[OptionalTimestamp]
    snoozed_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class IntakeRefusal(Base):
    """Telemetry a receiver refused in one minute for lack of a store (C5).

    Install-wide, with no `org_id`: a broker failure refuses every tenant alike,
    and the detection sweep asks only whether a minute is complete.
    """

    __tablename__ = "intake_refusals"

    minute: Mapped[datetime] = mapped_column(TimestampTz, primary_key=True)
    receiver_id: Mapped[str] = mapped_column(String(RECEIVER_ID_LENGTH), primary_key=True)
    reason: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint(f"reason IN ({_quoted(INTAKE_REFUSAL_REASONS)})", name="reason_is_known"),
        CheckConstraint("count > 0", name="count_is_positive"),
    )


class DetectionEvent(Base):
    """What detection did on its own that an operator must see, such as a held
    minute judged after 30 minutes (design 2.1, item 1). Install-wide, with no
    `org_id`: it describes nexovue's pipeline, not a tenant's data."""

    __tablename__ = "detection_events"

    id: Mapped[UuidPrimaryKey]
    at: Mapped[CreatedAt]
    kind: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )

    __table_args__ = (Index("ix_detection_events_at", "at"),)


class DetectionSweep(Base):
    """When the detection sweep last ran, and the newest minute it has handed to
    the project jobs: what *detection stalled* reads (F6, T7.1). One row at most,
    install-wide, with no `org_id`: it describes nexovue's pipeline."""

    __tablename__ = "detection_sweep"

    one_row: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default=text("true"))
    swept_at: Mapped[RequiredTimestamp]
    # None until a sweep has settled a minute; never moves backwards.
    settled_minute: Mapped[OptionalTimestamp]

    __table_args__ = (CheckConstraint("one_row", name="holds_one_row"),)


class ServiceLastSeen(Base):
    """When a service last sent anything (F21, *service went silent*). Written by the
    Go usage meter with `GREATEST`, so a late or replayed flush never moves it back."""

    __tablename__ = "service_last_seen"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    service_name: Mapped[str] = mapped_column(String(SERVICE_NAME_LENGTH), primary_key=True)
    last_seen_at: Mapped[RequiredTimestamp]


class ErrorGroupSeen(Base):
    """An error group a project has had. Inserting one is how a group is found new (AC8)."""

    __tablename__ = "error_groups_seen"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen_at: Mapped[RequiredTimestamp]


class ErrorGroupWatermark(Base):
    """How far each project's new-error sweep has read, and when it finished its
    baseline — empty while a history larger than one read is still being taken."""

    __tablename__ = "error_group_watermarks"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    received_through: Mapped[RequiredTimestamp]
    baselined_at: Mapped[OptionalTimestamp]


class WebsitePayment(Base):
    """The payment the website took before this organization existed (ADR-008, step 8).

    One per organization: after it, the product's own billing owns renewals.
    """

    __tablename__ = "website_payments"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    razorpay_payment_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # Who recorded it. A label on the record, not a dependency: the row stays
    # when the person goes.
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    recorded_at: Mapped[CreatedAt]

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="payment_is_positive"),
        UniqueConstraint("org_id", name="uq_website_payments_org_id"),
    )


class PlanGrant(Base):
    """A complimentary plan until a date (F31, FR27).

    The plan in force is an active grant if there is one, else the paid plan.
    Nothing rewrites `organizations.plan`, so a grant ends by its own date.
    """

    __tablename__ = "plan_grants"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    plan_slug: Mapped[str] = mapped_column(
        String(SHORT_LABEL_LENGTH), ForeignKey("plans.slug", ondelete="RESTRICT"), nullable=False
    )
    # The services the allowance is sized on while the grant is in force.
    services: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[CreatedAt]
    ends_at: Mapped[RequiredTimestamp]
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # Who granted it, for the same reason `recorded_by` above names one.
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Ended early by nexovue; NULL runs it to `ends_at`.
    ended_at: Mapped[OptionalTimestamp]

    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="grant_ends_after_it_starts"),
        CheckConstraint("services > 0", name="grant_covers_a_service"),
        Index("ix_plan_grants_org_id_ends_at", "org_id", "ends_at"),
    )


class SupportAccessGrant(Base):
    """An owner's consent, for a time, to be viewed as by nexovue support (BR14)."""

    __tablename__ = "support_access_grants"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[CreatedAt]
    expires_at: Mapped[RequiredTimestamp]
    withdrawn_at: Mapped[OptionalTimestamp]

    __table_args__ = (
        CheckConstraint("expires_at > granted_at", name="access_expires_after_it_starts"),
        Index("ix_support_access_grants_org_id_expires_at", "org_id", "expires_at"),
    )


class ApiToken(Base):
    """An organization's read credential for its own data (F36, FR13, ADR-017).

    The organization's, not a person's: `created_by` is a label for the list and
    goes to NULL when that person is removed, so a teammate leaving cannot stop
    a pipeline (BR9). `project_id` NULL reads every project; set, it reads that
    one and answers a question about another exactly as a project that does not
    exist does (BR11).

    Stored as ingest keys are — SHA-256, never argon2 — because this is verified
    on every request, and a 190-bit generated secret has nothing for stretching
    to protect. `token_prefix` is the plaintext head: it is what a customer sees
    in the list to tell two tokens apart, and what a leaked-secret scanner
    matches on.
    """

    __tablename__ = "api_tokens"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    role: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False, default="read")
    token_prefix: Mapped[str] = mapped_column(
        String(API_TOKEN_PREFIX_LENGTH), nullable=False, unique=True
    )
    token_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), nullable=False, unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[CreatedAt]
    # Every token ends; the customer chooses when.
    expires_at: Mapped[RequiredTimestamp]
    revoked_at: Mapped[OptionalTimestamp]
    last_used_at: Mapped[OptionalTimestamp]

    __table_args__ = (
        CheckConstraint("role IN ('read')", name="token_role_is_known"),
        CheckConstraint("expires_at > created_at", name="token_expires_after_it_is_made"),
        Index("ix_api_tokens_org_id", "org_id"),
    )


class ApiTokenUsageDaily(Base):
    """What one token spent on one day (F36, AC18).

    Counted in each API process and added here once a minute, so showing a
    customer what their automation costs them is not a Postgres write per request. It
    carries no `org_id`: the token it names does, and the row dies with it.
    """

    __tablename__ = "api_token_usage_daily"

    token_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("api_tokens.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    requests: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (CheckConstraint("requests >= 0", name="token_requests_do_not_go_backwards"),)


# What Redis held until migration 0037 (ADR-020). Every short-lived row carries
# `expires_at`: reads filter on it, and the scheduler's expiry sweep deletes it.
# SHA-256, hex, of a token, ticket, challenge or code — never the value itself.
TOKEN_FINGERPRINT_LENGTH: Final = 64
ONE_TIME_TOKEN_PURPOSES: Final = ("verify_email", "reset_password", "change_email")
JOB_RESULT_KINDS: Final = ("check_run", "channel_test", "connection_test")


class SessionRecord(Base):
    """A signed-in browser, read on every authenticated request (`auth/sessions.py`).

    Keyed by the token's fingerprint, so a dump of this table is not a set of
    usable credentials. Revoking every session at once is still
    `users.sessions_valid_from`.
    """

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(TOKEN_FINGERPRINT_LENGTH), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[CreatedAt]
    last_seen_at: Mapped[datetime] = mapped_column(
        TimestampTz, nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[RequiredTimestamp]
    client_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_network: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_until: Mapped[OptionalTimestamp]
    impersonation_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    impersonation_operator: Mapped[str | None] = mapped_column(Text, nullable=True)
    impersonation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    impersonation_grant_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    impersonation_until: Mapped[OptionalTimestamp]

    __table_args__ = (
        CheckConstraint(
            "num_nulls(impersonation_id, impersonation_operator, impersonation_reason,"
            " impersonation_grant_id, impersonation_until) IN (0, 5)",
            name="impersonation_is_whole_or_absent",
        ),
        Index("ix_sessions_user_id_expires_at", "user_id", "expires_at"),
        Index("ix_sessions_expires_at", "expires_at"),
    )


class OneTimeToken(Base):
    """A mailed link, spent once (`auth/tokens.py`)."""

    __tablename__ = "one_time_tokens"

    token_hash: Mapped[str] = mapped_column(String(TOKEN_FINGERPRINT_LENGTH), primary_key=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    new_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[CreatedAt]
    expires_at: Mapped[RequiredTimestamp]

    __table_args__ = (
        CheckConstraint(
            f"purpose IN ({', '.join(repr(p) for p in ONE_TIME_TOKEN_PURPOSES)})",
            name="purpose_is_known",
        ),
        CheckConstraint(
            "(purpose = 'change_email') = (new_email IS NOT NULL)",
            name="only_a_change_of_address_names_an_address",
        ),
        Index("ix_one_time_tokens_user_id", "user_id"),
        Index("ix_one_time_tokens_expires_at", "expires_at"),
    )


class ViewAsTicketRecord(Base):
    """The single-use pass that opens a support view (`support/tickets.py`)."""

    __tablename__ = "view_as_tickets"

    token_hash: Mapped[str] = mapped_column(String(TOKEN_FINGERPRINT_LENGTH), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    operator_label: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    grant_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    impersonation_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    until: Mapped[RequiredTimestamp]
    expires_at: Mapped[RequiredTimestamp]

    __table_args__ = (Index("ix_view_as_tickets_expires_at", "expires_at"),)


class TotpEnrolment(Base):
    """A shown but unconfirmed authenticator secret, sealed (`auth/totp.py`)."""

    __tablename__ = "totp_enrolments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    sealed_secret: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[RequiredTimestamp]

    __table_args__ = (Index("ix_totp_enrolments_expires_at", "expires_at"),)


class TotpChallenge(Base):
    """A sign-in past its password and not yet its code, with its attempt budget."""

    __tablename__ = "totp_challenges"

    token_hash: Mapped[str] = mapped_column(String(TOKEN_FINGERPRINT_LENGTH), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    expires_at: Mapped[RequiredTimestamp]

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_do_not_go_negative"),
        Index("ix_totp_challenges_expires_at", "expires_at"),
    )


class TotpSpentCode(Base):
    """A code already used, refused until it could no longer be valid."""

    __tablename__ = "totp_spent_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    code_hash: Mapped[str] = mapped_column(String(TOKEN_FINGERPRINT_LENGTH), primary_key=True)
    expires_at: Mapped[RequiredTimestamp]

    __table_args__ = (Index("ix_totp_spent_codes_expires_at", "expires_at"),)


class JobResult(Base):
    """What a Run now or a send test found, until the API collects it."""

    __tablename__ = "job_results"

    request_id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[RequiredTimestamp]

    __table_args__ = (
        CheckConstraint(
            f"kind IN ({', '.join(repr(k) for k in JOB_RESULT_KINDS)})", name="kind_is_known"
        ),
        Index("ix_job_results_expires_at", "expires_at"),
    )


class AlertEvaluationStatus(Base):
    """When each alert sweep last finished, and why it skipped if it did."""

    __tablename__ = "alert_evaluation_status"

    evaluation: Mapped[str] = mapped_column(Text, primary_key=True)
    finished_at: Mapped[RequiredTimestamp]
    skipped: Mapped[str | None] = mapped_column(Text, nullable=True)


class RateLimitWindow(Base):
    """The auth tier's exact count for one key in one fixed window.

    The key is a fingerprint, so an address being guessed at is not stored.
    """

    __tablename__ = "rate_limit_windows"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    window_start: Mapped[datetime] = mapped_column(TimestampTz, primary_key=True)
    hits: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("hits > 0", name="a_window_counts_something"),
        Index("ix_rate_limit_windows_window_start", "window_start"),
    )


class Job(Base):
    """One unit of scheduled or requested work — the queue that replaced Celery.

    Claimed with `FOR UPDATE SKIP LOCKED` under a lease; a worker that dies
    leaves its lease to lapse and the job is claimed again. `org_id` is NULL
    for a sweep, and set where the work is one organization's, so erasing it
    removes its queued work.
    """

    __tablename__ = "jobs"

    id: Mapped[UuidPrimaryKey]
    queue: Mapped[str] = mapped_column(Text, nullable=False)
    task: Mapped[str] = mapped_column(Text, nullable=False)
    kwargs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    run_after: Mapped[datetime] = mapped_column(
        TimestampTz, nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[OptionalTimestamp]
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    soft_limit_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    hard_limit_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_until: Mapped[OptionalTimestamp]
    leased_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[CreatedAt]

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_do_not_go_negative"),
        CheckConstraint("max_attempts >= 1", name="a_job_runs_at_least_once"),
        CheckConstraint(
            "soft_limit_seconds > 0 AND hard_limit_seconds > soft_limit_seconds",
            name="the_hard_limit_follows_the_soft",
        ),
        Index("ix_jobs_queue_run_after", "queue", "run_after"),
        Index("ix_jobs_org_id", "org_id"),
    )


class SloTarget(Base):
    """A reliability target (F19): one thing measured over one window (BR1).

    Soft-deleted for 30 days, so a deletion can be undone and still shows on the
    list (FR31). The name and description are free text a person typed: shown on
    console pages only, never copied into an incident or a notification (FR33)."""

    __tablename__ = "slo_targets"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    service_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    endpoint_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    check_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("checks.id", ondelete="SET NULL"), nullable=True
    )
    objective_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    window_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    boundary_index: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    asked_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    left_out_endpoints: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    upstream_sampling: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    definition_key: Mapped[str] = mapped_column(Text, nullable=False)
    # A change of what is counted raises the first; the history read catches the
    # second up, and runs again if they still differ at its end (AC64).
    definition_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    history_read_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    history_read_through: Mapped[date | None] = mapped_column(Date, nullable=True)
    first_event_at: Mapped[OptionalTimestamp]
    exhausted_warned_at: Mapped[OptionalTimestamp]
    exhausted_armed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[CreatedAt]
    deleted_at: Mapped[OptionalTimestamp]
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_slo_targets_org_id_id"),
        ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (created_by)",
            name="fk_slo_targets_created_by",
        ),
        ForeignKeyConstraint(
            ["org_id", "deleted_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (deleted_by)",
            name="fk_slo_targets_deleted_by",
        ),
        Index(
            "uq_slo_targets_live_name",
            "org_id",
            "project_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_slo_targets_live_definition",
            "org_id",
            "project_id",
            "definition_key",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_slo_targets_org_id_project_id_created_at", "org_id", "project_id", "created_at"),
    )


class SloStatus(Base):
    """Where a target stood at the last minute the job worked it out (F19, D26)."""

    __tablename__ = "slo_status"

    slo_id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    evaluated_minute: Mapped[RequiredTimestamp]
    tiers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    last_event_minute: Mapped[OptionalTimestamp]
    burning_since: Mapped[OptionalTimestamp]
    last_held_minute: Mapped[OptionalTimestamp]
    unjudged_since: Mapped[OptionalTimestamp]
    longest_quiet_7d: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "slo_id"],
            ["slo_targets.org_id", "slo_targets.id"],
            ondelete="CASCADE",
            name="fk_slo_status_target",
        ),
        Index(
            "ix_slo_status_org_id_project_id_evaluated_minute",
            "org_id",
            "project_id",
            "evaluated_minute",
        ),
    )


class SloDailyCount(Base):
    """One target's local day (F19, D2): rewritten until 7 days old, then final."""

    __tablename__ = "slo_daily_counts"

    org_id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True)
    slo_id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    good: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    failures: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quiet_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    lost_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    excluded_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    quiet_leading_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quiet_trailing_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    longest_quiet_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    expected_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    missed_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    maintenance_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loss_known: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    final: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    computed_at: Mapped[RequiredTimestamp]
    changed_at: Mapped[OptionalTimestamp]
    changed_why: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "slo_id"],
            ["slo_targets.org_id", "slo_targets.id"],
            ondelete="CASCADE",
            name="fk_slo_daily_counts_target",
        ),
    )


class SloTargetChange(Base):
    """One change to a target, for its history (F19, FR30). Kept when the target is
    purged, so a past report stays true (D46); it goes with the project."""

    __tablename__ = "slo_target_changes"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    slo_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    at: Mapped[CreatedAt]
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    what: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "actor_user_id"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (actor_user_id)",
            name="fk_slo_target_changes_actor",
        ),
        Index("ix_slo_target_changes_org_id_slo_id_at", "org_id", "slo_id", "at"),
        Index("ix_slo_target_changes_org_id_actor_user_id", "org_id", "actor_user_id"),
    )


class SloDeletedMonth(Base):
    """A purged target's month result (F19, FR31, D46): written before its daily figures
    go, so a past month still counts it. It goes with the project."""

    __tablename__ = "slo_deleted_months"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    slo_id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    objective_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    figure_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    good: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    quiet_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    longest_quiet_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    lost_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    missed_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    maintenance_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    days_held: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    days_in_month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    loss_known: Mapped[bool] = mapped_column(Boolean, nullable=False)
    upstream_sampling: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[RequiredTimestamp]

    __table_args__ = (
        Index("ix_slo_deleted_months_org_id_project_id_month", "org_id", "project_id", "month"),
    )


class SloExclusion(Base):
    """A stretch of time a person left out of one target's figure, with a reason (F19,
    FR29). Removed by a mark, never deleted, so its history stays (BR6, BR7). The reason
    is free text: it never enters an incident or a notification (FR33)."""

    __tablename__ = "slo_exclusions"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    slo_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    starts_at: Mapped[RequiredTimestamp]
    ends_at: Mapped[RequiredTimestamp]
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    created_at: Mapped[CreatedAt]
    removed_at: Mapped[OptionalTimestamp]
    removed_by: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    # Null while the days it touches wait for the scheduler's recount (FR29).
    counted_at: Mapped[OptionalTimestamp]
    removal_not_recounted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "slo_id"],
            ["slo_targets.org_id", "slo_targets.id"],
            ondelete="CASCADE",
            name="fk_slo_exclusions_target",
        ),
        ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (created_by)",
            name="fk_slo_exclusions_created_by",
        ),
        ForeignKeyConstraint(
            ["org_id", "removed_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (removed_by)",
            name="fk_slo_exclusions_removed_by",
        ),
        CheckConstraint("ends_at > starts_at", name="ends_after_it_starts"),
        CheckConstraint(
            f"length(reason) BETWEEN {slo.EXCLUSION_REASON_SHORTEST} "
            f"AND {slo.EXCLUSION_REASON_LONGEST}",
            name="a_reason_is_given",
        ),
        CheckConstraint(
            "removed_at IS NULL OR removed_at >= created_at", name="removed_after_created"
        ),
        Index("ix_slo_exclusions_org_id_slo_id_starts_at", "org_id", "slo_id", "starts_at"),
    )


class Release(Base):
    """One version of one service in a project, from either door: counted in the version
    rollup, or reported by a sender (F28, design 2, D2). Stored rather than derived, so
    its first-seen time and id outlive the rollup's 35 days. Written by the scheduler's
    sync (`releases/sync.py`) and, from Step 2, the deploy webhook."""

    __tablename__ = "releases"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    service_name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    version: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    first_seen_at: Mapped[OptionalTimestamp]
    last_seen_at: Mapped[OptionalTimestamp]
    first_reported_at: Mapped[OptionalTimestamp]
    last_reported_at: Mapped[OptionalTimestamp]
    report_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_state: Mapped[str | None] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=True)
    # The latest arrival of a report or a count, never a sender's claim: retention reads it.
    last_activity_at: Mapped[datetime] = mapped_column(
        TimestampTz, nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_releases_org_id_id"),
        UniqueConstraint(
            "project_id", "service_name", "version", name="uq_releases_project_service_version"
        ),
        CheckConstraint(f"version NOT IN ('', '{OTHER_VERSION}')", name="version_is_a_release"),
        CheckConstraint("service_name <> ''", name="service_is_named"),
        CheckConstraint(
            "first_seen_at IS NOT NULL OR first_reported_at IS NOT NULL", name="seen_or_reported"
        ),
        CheckConstraint("report_count >= 0", name="report_count_is_not_negative"),
        CheckConstraint(f"last_state IN ({_quoted(DEPLOY_STATES)})", name="last_state_is_known"),
        Index("ix_releases_last_activity_at", "last_activity_at"),
    )


class ReleasePeriod(Base):
    """One stretch a release served (F28, D15, FR19). A past period only ever gains an
    end; a version that comes back opens a new one."""

    __tablename__ = "release_periods"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    release_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    service_name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    # The first counted minute once known, else the reported time (FR42).
    started_at: Mapped[RequiredTimestamp]
    start_basis: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    ended_at: Mapped[OptionalTimestamp]
    previous_version: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), nullable=True)
    already_running: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_return: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    last_reading: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    mixed_environments: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "release_id"],
            ["releases.org_id", "releases.id"],
            ondelete="CASCADE",
            name="fk_release_periods_release",
        ),
        UniqueConstraint(
            "org_id", "release_id", "started_at", name="uq_release_periods_release_start"
        ),
        CheckConstraint(f"start_basis IN ({_quoted(START_BASES)})", name="start_basis_is_known"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ended_after_started"),
        CheckConstraint(
            f"previous_version NOT IN ('', '{OTHER_VERSION}')",
            name="previous_version_is_a_release",
        ),
        CheckConstraint(
            "NOT (already_running AND is_return)", name="a_return_was_not_already_running"
        ),
        Index(
            "uq_release_periods_one_open",
            "org_id",
            "release_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index(
            "ix_release_periods_org_id_project_id_started_at",
            "org_id",
            "project_id",
            text("started_at DESC"),
            "id",
        ),
        Index(
            "ix_release_periods_org_id_project_id_service_name_started_at",
            "org_id",
            "project_id",
            "service_name",
            text("started_at DESC"),
        ),
        Index(
            "ix_release_periods_open",
            "org_id",
            "project_id",
            postgresql_where=text("ended_at IS NULL"),
        ),
    )


class ReleaseSyncState(Base):
    """How far the release sync has read a project's version rollup (F28, design 3,
    step 10): the last minute read, never moved backwards."""

    __tablename__ = "release_sync_state"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    synced_through: Mapped[RequiredTimestamp]


# F20. What a dashboard may hold is checked by `dashboards/definition.py`, the one way
# a definition enters; these are the bounds the database also holds.
DASHBOARD_TITLE_MAX_LENGTH: Final = 120
DASHBOARD_DESCRIPTION_MAX_LENGTH: Final = 500


class Dashboard(Base):
    """A saved dashboard (F20, BR1): one checked JSON definition, owned by the organization
    and shown against whichever project the reader names.

    Soft-deleted for 30 days (FR51). `panel_count`, `service_names` and `uses_picker` are
    written at save from the compiled definition, so lists never read the JSON (AC96).
    `created_by` and `updated_by` survive a member's removal, which scrubs the person
    rather than deleting the row, so the owner reads "Former member" (FR38)."""

    __tablename__ = "dashboards"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # The optimistic lock: a save names the version it was based on (FR19).
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    # Written by a batch read at most hourly, never by a support view (FR37).
    last_viewed_at: Mapped[OptionalTimestamp]
    panel_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    service_names: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    uses_picker: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    panels_not_reading: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[CreatedAt]
    deleted_at: Mapped[OptionalTimestamp]

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_dashboards_org_id_id"),
        ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (created_by)",
            name="fk_dashboards_created_by",
        ),
        ForeignKeyConstraint(
            ["org_id", "updated_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (updated_by)",
            name="fk_dashboards_updated_by",
        ),
        CheckConstraint("jsonb_typeof(definition) = 'object'", name="definition_is_an_object"),
        CheckConstraint(
            f"char_length(title) BETWEEN 1 AND {DASHBOARD_TITLE_MAX_LENGTH}",
            name="title_length",
        ),
        CheckConstraint(
            f"char_length(description) <= {DASHBOARD_DESCRIPTION_MAX_LENGTH}",
            name="description_length",
        ),
        # BR12: no two live dashboards share a title, ignoring case.
        Index(
            "uq_dashboards_live_title",
            "org_id",
            text("lower(title)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_dashboards_org_id_last_viewed_at", "org_id", "last_viewed_at"),
        Index("ix_dashboards_service_names", "service_names", postgresql_using="gin"),
    )


class DashboardVersion(Base):
    """A version a save replaced (F20, FR51): who saved it and when, so "Restore this
    version" is an ordinary save. Trimmed only past the newest 20 and older than 30 days."""

    __tablename__ = "dashboard_versions"

    dashboard_id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    saved_by: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    saved_at: Mapped[RequiredTimestamp]

    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "dashboard_id"],
            ["dashboards.org_id", "dashboards.id"],
            ondelete="CASCADE",
            name="fk_dashboard_versions_dashboard",
        ),
        ForeignKeyConstraint(
            ["org_id", "saved_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (saved_by)",
            name="fk_dashboard_versions_saved_by",
        ),
        CheckConstraint("jsonb_typeof(definition) = 'object'", name="definition_is_an_object"),
    )
