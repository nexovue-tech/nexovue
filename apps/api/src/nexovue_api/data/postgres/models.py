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
from datetime import date
from typing import Any, Final

from nexovue_platform.alerts.model import (
    ATTEMPT_OUTCOMES,
    CHANNEL_HEALTH,
    CHANNEL_KINDS,
    CONDITION_KINDS,
    EVALUATION_PHASES,
    NOTIFICATION_KINDS,
    NOTIFICATION_STATES,
    SEVERITIES,
)
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
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
BILLING_SUBSCRIPTION_STATUSES: Final = ("pending", "active", "cancelled")
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
    # session": Redis keys sessions by token fingerprint, so there is nothing to
    # enumerate, and this comparison rides a user row every request already loads.
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
    created_at: Mapped[CreatedAt]

    __table_args__ = (
        Index("ix_users_org_id", "org_id"),
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
    created_at: Mapped[CreatedAt]
    deleted_at: Mapped[OptionalTimestamp]

    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_projects_org_id_slug"),
        Index("ix_projects_org_id", "org_id"),
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
    # The sender's own id for this delivery. Unique per project under a partial
    # index, which is what makes a replayed request fail rather than store a
    # second deploy — GitHub's signature scheme carries no timestamp, so a
    # captured request stays valid forever without this.
    delivery_id: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), nullable=True)
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


class ProjectWebhookSecret(Base):
    """A shared secret CI signs deploy notifications with.

    Its own table rather than derived from an ingest key, because they are
    different credentials with different lifetimes: revoking a key should not
    silently stop deploys arriving. Many rows per project, so a rotation can
    overlap — create the new one, update CI, revoke the old.

    Stored as it will be used. HMAC verification has to recompute the digest CI
    computed, so there is no hashing here that would still allow that.
    """

    __tablename__ = "project_webhook_secrets"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    secret: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[CreatedAt]
    revoked_at: Mapped[OptionalTimestamp]

    __table_args__ = (
        Index("ix_webhook_secrets_project_id_revoked_at", "project_id", "revoked_at"),
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
        Index("ix_billing_webhook_deliveries_org_id_received_at", "org_id", "received_at"),
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

    __table_args__ = (
        CheckConstraint(f"severity IN ({_quoted(SEVERITIES)})", name="severity_is_known"),
        CheckConstraint(
            f"condition_kind IN ({_quoted(CONDITION_KINDS)})", name="condition_is_known"
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
    )


class Notification(Base):
    """A decision to tell one channel about one incident: the outbox (ADR-013)."""

    __tablename__ = "notifications"

    id: Mapped[UuidPrimaryKey]
    org_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(SHORT_LABEL_LENGTH), nullable=False)
    sequence: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
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

    __table_args__ = (
        UniqueConstraint("incident_id", "channel_id", "kind", "sequence"),
        CheckConstraint(f"kind IN ({_quoted(NOTIFICATION_KINDS)})", name="kind_is_known"),
        CheckConstraint(f"state IN ({_quoted(NOTIFICATION_STATES)})", name="state_is_known"),
        CheckConstraint(f"severity IN ({_quoted(SEVERITIES)})", name="severity_is_known"),
        Index(
            "ix_notifications_due",
            "deliver_after",
            postgresql_where=text("state IN ('pending', 'delivering')"),
        ),
        Index("ix_notifications_channel_id_state", "channel_id", "state"),
        Index("ix_notifications_batch_id", "batch_id"),
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

    __table_args__ = (
        CheckConstraint(f"outcome IN ({_quoted(ATTEMPT_OUTCOMES)})", name="outcome_is_known"),
        Index("ix_notification_attempts_channel_id_attempted_at", "channel_id", "attempted_at"),
        Index("ix_notification_attempts_batch_id", "batch_id"),
        Index("ix_notification_attempts_attempted_at", "attempted_at"),
    )


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


class StaffAccount(Base):
    """nexovue's own staff (F31, ADR-016) — never a row in `users`.

    No foreign key, role or flag connects the two. `totp_secret` is NOT NULL:
    the terminal command that creates a staff account enrols the second factor
    before the row exists, so there is no staff account without one.
    """

    __tablename__ = "staff_accounts"

    id: Mapped[UuidPrimaryKey]
    email: Mapped[str] = mapped_column(String(EMAIL_MAX_LENGTH), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Sealed with `CredentialBox` under the staff purpose, never stored in clear.
    totp_secret: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[CreatedAt]
    disabled_at: Mapped[OptionalTimestamp]


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
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True
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
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True
    )
    # Ended early by staff; NULL runs it to `ends_at`.
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
