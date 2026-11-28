"""Tracker connections, and the places each one reaches

F22 (`docs/features/case-management/`), plan T3.4; follows F28's 0054 (releases).

**Why this change is required.** A case is linked to a GitHub issue through a
connection: on a self-hosted install, a private GitHub App the organization created
through GitHub's manifest flow (FR28, FD11). The App's private key and webhook secret
must be kept, sealed, and so must the one-hour manifest code between GitHub's
redirect and the job that converts it (AC74). The repositories the App reaches are
the places an issue may be created.

**Affected store and schema.** Postgres, two new tables.

- `connections`: one row per organization and kind, `kind` one of `github`, `jira`,
  `github_fix` (F5's App, which F22 ignores), so Jira later is an added adapter on the
  same table (foundation section 6, item 29). Three sealed columns under
  `CONNECTION_SECRET_KEY` (`sealed_credential`, `sealed_webhook_secret`,
  `sealed_manifest_code`), all in `keys.SEALED_COLUMNS`. A setup holds only the hash
  of its one-time `state`, its expiry and who started it. `state` is the spec's
  connection state; `last_failure` an enum and an HTTP status, never a provider's
  text. One live connection per organization and kind (D17), so a disconnected or
  abandoned row never blocks a new one (AC76).
- `connection_repositories`: what a connection reaches, a GitHub repository or a Jira
  project, by its strict key (never a free-text name) and whether it is private.
  Called a *place* in code.
- `UNIQUE (org_id, id)` on both, so later tables key on both and the database refuses
  a cross-tenant link (D34, AC72).
- `job_results` may hold a third kind, `connection_test`: what a connection test found,
  for the person who pressed it (FR30). The check is replaced `NOT VALID` and then
  validated, so the table's writers are not held while existing rows are read.

**Compatibility and backfill.** None: new tables, and no organization is connected.

**Rollback and recovery.** The downgrade drops both tables. What they hold can be made
again: a connection is set up afresh, and GitHub keeps the App. No case data lives
here; `finding_links` (Step 4) will key on these and changes that.

**Tenant, retention, and security impact.** Both carry `org_id` in a composite key.
`connections` is deleted directly with the organization, before `users`, whom a setup
names; `connection_repositories` cascades from it (`organizations/tables.py`). A
disconnected row must hold no sealed value (checked). No secret is ever in plain text.

Revision ID: 0055
Revises: 0054
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies of `nexovue_platform.connections`' words, held equal to it by
# `test_connection_model.py`: a later change to them is a migration of its own.
_KINDS = "'github', 'jira', 'github_fix'"
_STATES = "'setting_up', 'working', 'failing', 'needs_attention', 'disconnected'"
_FAILURES = (
    "'unreachable', 'timed_out', 'server_error', 'rate_limited', 'unauthorized', "
    "'app_not_installed', 'installation_ambiguous', 'no_repository', 'key_unreadable', "
    "'manifest_expired', 'manifest_refused', 'unexpected_answer'"
)
# A GitHub `owner/name`, or a Jira project key (FR40); never a free-text name.
_PLACE_KEY = r"^([A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}|[A-Z][A-Z0-9_]+)$"
_JIRA_SITE = r"^[a-z0-9][a-z0-9-]*\.atlassian\.net$"
_TRANSITION = r"^[0-9]+$"
# `job_results.kind`, before and after this revision (models.JOB_RESULT_KINDS).
_EARLIER_JOB_RESULT_KINDS = "'check_run', 'channel_test'"
_JOB_RESULT_KINDS = "'check_run', 'channel_test', 'connection_test'"


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("sealed_credential", sa.LargeBinary(), nullable=True),
        sa.Column("sealed_webhook_secret", sa.LargeBinary(), nullable=True),
        sa.Column("sealed_manifest_code", sa.LargeBinary(), nullable=True),
        # The one-time `state` of a GitHub setup, as its SHA-256; the raw value is
        # never stored.
        sa.Column("setup_state_hash", sa.LargeBinary(), nullable=True),
        sa.Column("setup_expires_at", _TIMESTAMP, nullable=True),
        sa.Column("setup_user_id", _UUID, nullable=True),
        sa.Column("github_app_id", sa.BigInteger(), nullable=True),
        sa.Column("github_app_slug", sa.Text(), nullable=True),
        sa.Column("github_installation_id", sa.BigInteger(), nullable=True),
        sa.Column("jira_site", sa.Text(), nullable=True),
        sa.Column("token_expires_on", sa.Date(), nullable=True),
        sa.Column("sync_lease_until", _TIMESTAMP, nullable=True),
        sa.Column("paused_until", _TIMESTAMP, nullable=True),
        sa.Column("last_ok_at", _TIMESTAMP, nullable=True),
        sa.Column("failing_since", _TIMESTAMP, nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_failure", sa.Text(), nullable=True),
        sa.Column("last_failure_status", sa.SmallInteger(), nullable=True),
        sa.Column("failure_told_at", _TIMESTAMP, nullable=True),
        sa.Column("expiry_told_at", _TIMESTAMP, nullable=True),
        sa.Column("repositories_read_at", _TIMESTAMP, nullable=True),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "id", name="uq_connections_org_id_id"),
        sa.ForeignKeyConstraint(
            ["org_id", "setup_user_id"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (setup_user_id)",
            name="fk_connections_setup_user",
        ),
        sa.CheckConstraint(f"kind IN ({_KINDS})", name="kind_is_known"),
        sa.CheckConstraint(f"state IN ({_STATES})", name="state_is_known"),
        sa.CheckConstraint(
            f"last_failure IS NULL OR last_failure IN ({_FAILURES})", name="failure_is_known"
        ),
        # FR32: disconnecting deletes the stored credentials in the same transaction.
        sa.CheckConstraint(
            "state <> 'disconnected' OR (sealed_credential IS NULL "
            "AND sealed_webhook_secret IS NULL AND sealed_manifest_code IS NULL "
            "AND setup_state_hash IS NULL)",
            name="disconnected_holds_no_secret",
        ),
        # A setup's `state` always has an expiry; the expiry outlives the hash, which is
        # cleared when the return is taken, because the conversion job still reads it.
        sa.CheckConstraint(
            "setup_state_hash IS NULL OR setup_expires_at IS NOT NULL",
            name="setup_state_expires",
        ),
        sa.CheckConstraint(
            f"jira_site IS NULL OR jira_site ~ '{_JIRA_SITE}'", name="jira_site_is_atlassian"
        ),
        sa.CheckConstraint("consecutive_failures >= 0", name="failures_count_up"),
    )
    # D17: one live connection per organization and kind; a disconnected row, and an
    # abandoned setup the API marks disconnected, never blocks a new one (AC76).
    op.create_index(
        "uq_connections_org_id_kind_live",
        "connections",
        ["org_id", "kind"],
        unique=True,
        postgresql_where=sa.text("state <> 'disconnected'"),
    )
    op.create_index(
        "uq_connections_github_installation",
        "connections",
        ["github_app_id", "github_installation_id"],
        unique=True,
    )

    op.create_table(
        "connection_repositories",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("connection_id", _UUID, nullable=False),
        # The provider's own id: GitHub's repository id, or Jira's project id.
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("is_private", sa.Boolean(), nullable=False),
        sa.Column("public_allowed_by", _UUID, nullable=True),
        sa.Column("public_allowed_at", _TIMESTAMP, nullable=True),
        sa.Column("public_told_at", _TIMESTAMP, nullable=True),
        sa.Column("jira_issue_type_id", sa.Text(), nullable=True),
        sa.Column("jira_done_transition", sa.Text(), nullable=True),
        sa.Column("jira_reopen_transition", sa.Text(), nullable=True),
        sa.Column("is_reachable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "id", name="uq_connection_repositories_org_id_id"),
        sa.UniqueConstraint(
            "connection_id", "external_id", name="uq_connection_repositories_external_id"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "connection_id"],
            ["connections.org_id", "connections.id"],
            ondelete="CASCADE",
            name="fk_connection_repositories_connection",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "public_allowed_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (public_allowed_by)",
            name="fk_connection_repositories_public_allowed_by",
        ),
        sa.CheckConstraint(f"key ~ '{_PLACE_KEY}'", name="key_is_strict"),
        sa.CheckConstraint(
            f"(jira_done_transition IS NULL OR jira_done_transition ~ '{_TRANSITION}') "
            f"AND (jira_reopen_transition IS NULL OR jira_reopen_transition ~ '{_TRANSITION}')",
            name="transitions_are_ids",
        ),
    )

    op.execute("ALTER TABLE job_results DROP CONSTRAINT ck_job_results_kind_is_known")
    op.execute(
        f"ALTER TABLE job_results ADD CONSTRAINT ck_job_results_kind_is_known "
        f"CHECK (kind IN ({_JOB_RESULT_KINDS})) NOT VALID"
    )
    op.execute("ALTER TABLE job_results VALIDATE CONSTRAINT ck_job_results_kind_is_known")


def downgrade() -> None:
    op.execute("DELETE FROM job_results WHERE kind = 'connection_test'")
    op.execute("ALTER TABLE job_results DROP CONSTRAINT ck_job_results_kind_is_known")
    op.execute(
        f"ALTER TABLE job_results ADD CONSTRAINT ck_job_results_kind_is_known "
        f"CHECK (kind IN ({_EARLIER_JOB_RESULT_KINDS}))"
    )
    op.drop_table("connection_repositories")
    op.drop_table("connections")
