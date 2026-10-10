"""redis goes: what Redis held, in logged Postgres tables

Readiness group 2b (`docs/changes/redis-goes/`), ADR-020.

**Why this change is required.** Redis held sessions, one-time links, the
second factor's short-lived state, support tickets, job results, the alert
sweeps' status, the auth tier's counters and — as Celery's broker — every
scheduled job. It was configured to evict under memory pressure, so the store
that decided who was signed in could forget them, and a second stateful service
had to be run, backed up and secured for state small enough to live here.

Every table is **logged**. An `UNLOGGED` table is emptied after a crash and is
not replicated, so a failover would sign everybody out and drop queued work —
the property this change removes Redis to get rid of.

Postgres has no TTL, so every short-lived row carries `expires_at`: every read
filters on it, and the scheduler's expiry sweep deletes what has lapsed. A
missed sweep costs disk, never a wrong answer.

- `sessions`: one row per session, keyed by the SHA-256 of the token, as the
  Redis key was. `(user_id, expires_at)` is the per-user list the Redis sorted
  set kept by hand. Support impersonation is four columns that are all set or
  all empty.
- `one_time_tokens`: verify-email, reset-password and change-email links.
  `user_id` is a column rather than inside a value, so a user's outstanding
  links can be revoked — Redis could not list them.
- `view_as_tickets`: the 60-second pass that opens a support view.
- `totp_enrolments`, `totp_challenges`, `totp_spent_codes`: an unconfirmed
  secret (one per user), a half-finished sign-in with its attempt budget (one
  per sign-in, so two browsers do not overwrite each other), and a code already
  used, keyed on the code's hash as the Redis key was.
- `job_results`: what a Run now or a send test found, collected by the API.
- `alert_evaluation_status`: when each alert sweep last finished, and why it
  skipped if it did. Three rows, rewritten each minute; nothing expires.
- `rate_limit_windows`: the auth tier's exact fixed-window counter. The key is
  a fingerprint, never an address.
- `jobs`: the queue that replaces Celery's broker.

**Affected store and schema.** Postgres only: ten tables.

**Compatibility and backfill.** All start empty. Nothing is carried over from
Redis: nexovue is pre-deployment, so the one cost is that every development
session signs out once. Migration 0024 dropped an unused `sessions` table of a
different shape; this one is the first that is written.

**Rollback and recovery.** `downgrade` drops all ten. Everyone is signed out,
outstanding links and tickets stop working, and queued jobs are lost — beat
re-derives its schedule from Postgres, so only work queued at that moment goes.

**Tenant, retention, and security impact.** Tables carrying `org_id` cascade
with the organization; tables keyed by a person cascade with `users`. `jobs`
carries a nullable `org_id` so erasing an organization removes its queued work
(a sweep has none). Every token, ticket, challenge and code is stored as a
SHA-256 hash; no credential is stored in the clear. `alert_evaluation_status`
and `rate_limit_windows` hold no tenant.

Revision ID: 0037
Revises: 0036
Created: 2026-10-10
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)
_FINGERPRINT = sa.String(64)

# Frozen here so a purpose added in code without a migration is refused by the
# CHECK rather than stored.
_TOKEN_PURPOSES = "'verify_email', 'reset_password', 'change_email'"
_RESULT_KINDS = "'check_run', 'channel_test'"


def _user(*, primary_key: bool = False) -> sa.Column[Any]:
    return sa.Column(
        "user_id",
        _UUID,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=primary_key,
    )


def _organization(*, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(
        "org_id",
        _UUID,
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=nullable,
    )


def _expires_at() -> sa.Column[Any]:
    return sa.Column("expires_at", _TIMESTAMP, nullable=False)


def _created_at() -> sa.Column[Any]:
    return sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("token_hash", _FINGERPRINT, primary_key=True),
        _user(),
        _organization(),
        _created_at(),
        sa.Column("last_seen_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        _expires_at(),
        sa.Column("client_label", sa.Text(), nullable=True),
        sa.Column("client_network", sa.Text(), nullable=True),
        sa.Column("admin_until", _TIMESTAMP, nullable=True),
        sa.Column("impersonation_id", _UUID, nullable=True),
        sa.Column("impersonation_operator", sa.Text(), nullable=True),
        sa.Column("impersonation_reason", sa.Text(), nullable=True),
        sa.Column("impersonation_grant_id", _UUID, nullable=True),
        # The view ends at `impersonation_until`; `expires_at` is already capped to
        # it at creation, and the column stays so the identity can say when.
        sa.Column("impersonation_until", _TIMESTAMP, nullable=True),
        sa.CheckConstraint(
            "num_nulls(impersonation_id, impersonation_operator, impersonation_reason,"
            " impersonation_grant_id, impersonation_until) IN (0, 5)",
            name="impersonation_is_whole_or_absent",
        ),
    )
    op.create_index("ix_sessions_user_id_expires_at", "sessions", ["user_id", "expires_at"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table(
        "one_time_tokens",
        sa.Column("token_hash", _FINGERPRINT, primary_key=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        _user(),
        # The address a change-of-address link moves the account to, bound to the
        # link rather than to the user row — see `auth/tokens.py`.
        sa.Column("new_email", sa.Text(), nullable=True),
        _created_at(),
        _expires_at(),
        sa.CheckConstraint(f"purpose IN ({_TOKEN_PURPOSES})", name="purpose_is_known"),
        sa.CheckConstraint(
            "(purpose = 'change_email') = (new_email IS NOT NULL)",
            name="only_a_change_of_address_names_an_address",
        ),
    )
    op.create_index("ix_one_time_tokens_user_id", "one_time_tokens", ["user_id"])
    op.create_index("ix_one_time_tokens_expires_at", "one_time_tokens", ["expires_at"])

    op.create_table(
        "view_as_tickets",
        sa.Column("token_hash", _FINGERPRINT, primary_key=True),
        _user(),
        _organization(),
        sa.Column("operator_label", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("grant_id", _UUID, nullable=False),
        sa.Column("impersonation_id", _UUID, nullable=False),
        sa.Column("until", _TIMESTAMP, nullable=False),
        _expires_at(),
    )
    op.create_index("ix_view_as_tickets_expires_at", "view_as_tickets", ["expires_at"])

    op.create_table(
        "totp_enrolments",
        _user(primary_key=True),
        sa.Column("sealed_secret", sa.LargeBinary(), nullable=False),
        _expires_at(),
    )
    op.create_index("ix_totp_enrolments_expires_at", "totp_enrolments", ["expires_at"])

    op.create_table(
        "totp_challenges",
        sa.Column("token_hash", _FINGERPRINT, primary_key=True),
        _user(),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        _expires_at(),
        sa.CheckConstraint("attempts >= 0", name="attempts_do_not_go_negative"),
    )
    op.create_index("ix_totp_challenges_expires_at", "totp_challenges", ["expires_at"])

    op.create_table(
        "totp_spent_codes",
        _user(primary_key=True),
        sa.Column("code_hash", _FINGERPRINT, primary_key=True),
        _expires_at(),
    )
    op.create_index("ix_totp_spent_codes_expires_at", "totp_spent_codes", ["expires_at"])

    op.create_table(
        "job_results",
        sa.Column("request_id", _UUID, primary_key=True),
        _organization(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        _expires_at(),
        sa.CheckConstraint(f"kind IN ({_RESULT_KINDS})", name="kind_is_known"),
    )
    op.create_index("ix_job_results_expires_at", "job_results", ["expires_at"])

    op.create_table(
        "alert_evaluation_status",
        sa.Column("evaluation", sa.Text(), primary_key=True),
        sa.Column("finished_at", _TIMESTAMP, nullable=False),
        sa.Column("skipped", sa.Text(), nullable=True),
    )

    op.create_table(
        "rate_limit_windows",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("window_start", _TIMESTAMP, primary_key=True),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.CheckConstraint("hits > 0", name="a_window_counts_something"),
    )
    op.create_index("ix_rate_limit_windows_window_start", "rate_limit_windows", ["window_start"])

    op.create_table(
        "jobs",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("queue", sa.Text(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("kwargs", postgresql.JSONB(), nullable=False),
        _organization(nullable=True),
        sa.Column("run_after", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        # Past this the job is dropped unrun: a probe due every minute is worthless
        # five minutes late, which is what Celery's `expires` said.
        sa.Column("expires_at", _TIMESTAMP, nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("soft_limit_seconds", sa.Integer(), nullable=False),
        sa.Column("hard_limit_seconds", sa.Integer(), nullable=False),
        sa.Column("lease_until", _TIMESTAMP, nullable=True),
        sa.Column("leased_by", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        _created_at(),
        sa.CheckConstraint("attempts >= 0", name="attempts_do_not_go_negative"),
        sa.CheckConstraint("max_attempts >= 1", name="a_job_runs_at_least_once"),
        sa.CheckConstraint(
            "soft_limit_seconds > 0 AND hard_limit_seconds > soft_limit_seconds",
            name="the_hard_limit_follows_the_soft",
        ),
    )
    op.create_index("ix_jobs_queue_run_after", "jobs", ["queue", "run_after"])
    op.create_index("ix_jobs_org_id", "jobs", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_org_id", table_name="jobs")
    op.drop_index("ix_jobs_queue_run_after", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_rate_limit_windows_window_start", table_name="rate_limit_windows")
    op.drop_table("rate_limit_windows")
    op.drop_table("alert_evaluation_status")
    op.drop_index("ix_job_results_expires_at", table_name="job_results")
    op.drop_table("job_results")
    op.drop_index("ix_totp_spent_codes_expires_at", table_name="totp_spent_codes")
    op.drop_table("totp_spent_codes")
    op.drop_index("ix_totp_challenges_expires_at", table_name="totp_challenges")
    op.drop_table("totp_challenges")
    op.drop_index("ix_totp_enrolments_expires_at", table_name="totp_enrolments")
    op.drop_table("totp_enrolments")
    op.drop_index("ix_view_as_tickets_expires_at", table_name="view_as_tickets")
    op.drop_table("view_as_tickets")
    op.drop_index("ix_one_time_tokens_expires_at", table_name="one_time_tokens")
    op.drop_index("ix_one_time_tokens_user_id", table_name="one_time_tokens")
    op.drop_table("one_time_tokens")
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_index("ix_sessions_user_id_expires_at", table_name="sessions")
    op.drop_table("sessions")
