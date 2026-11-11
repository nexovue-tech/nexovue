"""A case's tracker issues

F22 (`docs/features/case-management/`), plan T4.2.

**Why this change is required.** A person's click creates one GitHub issue for a case,
and the case and the issue then follow each other's open or closed status (FR35 to
FR39). Which issue belongs to which case, what the tracker last said, what a person's
act wants it to say, and the one comment owed with that, must be kept: this is the
`seen_state` and `wanted_state` the reconcile compares (design, *The comparison*). A
reference link (Step 6) is any other issue shown on a case, which never changes it.

**Affected store and schema.** Postgres, one new table, `finding_links`.

- **No address column** (D81): an address an outside system chose must never reach a
  colleague's click, and a check cannot read the connection row to hold one. The
  console builds it from `external_key`, which only a strict pattern admits: a GitHub
  `owner/name#number` or a Jira key.
- **One synced issue per case, and an issue synced on one case only**, by two partial
  unique indexes that leave `stopped` links out, so a disconnect frees each case's slot
  (FR32). The same issue may be a reference on many cases, once each.
- **`frozen_facts` exists only while `creating`** (checked): the numbers, enums and
  strict name the preview showed, so the issue written later is the preview's bytes
  (AC83). They hold no title, note or endpoint (`nexovue_platform.connections.IssueFacts`).
- `last_failure` is an enum, never a provider's text; `last_failure_field` a field key
  under a strict pattern, for Jira later.
- `idle_warning_at` marks the one idle-warning comment a link may carry in its case's
  life (BR10, AC96), beside `comment_written_at`, which orders the close before its
  comment so a crash leaves at most one (AC87).
- `gone_at` dates a 404 from a readable repository, so it is read again daily for a
  week and then left (design, row 9).
- A partial index on `(connection_id, next_sync_at)` is what each sync run reads first.

**Compatibility and backfill.** None: a new table, and no case has an issue yet.

**Rollback and recovery.** The downgrade drops the table. A link points at an issue
that still exists in the customer's tracker and carries nexovue's link back in its body,
so what is lost can be found again by hand; nexovue is pre-deployment and no link exists.

**Tenant, retention, and security impact.** `org_id` leads each composite key: the
database refuses a link to another organization's finding, connection or place (AC72).
Links cascade with their finding (`organizations/tables.py`) and so follow its retention;
a connection or place that goes leaves the link as text (`SET NULL` on that column
alone). No secret, and no tracker text: only status, ids and strict keys (BR9).

Revision ID: 0061
Revises: 0060
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies of `nexovue_platform.connections`' words, held equal to it by
# `test_connection_model.py`: a later change to them is a migration of its own.
_ROLES = "'synced', 'reference'"
_STATUSES = (
    "'creating', 'promoting', 'in_sync', 'waiting', 'needs_attention', 'refused', 'gone', "
    "'not_followed', 'stopped'"
)
_ISSUE_STATES = "'open', 'closed'"
_COMMENTS = "'resolved', 'false_alarm', 'new_normal', 'idle_warning'"
_FAILURES = (
    "'unreachable', 'timed_out', 'server_error', 'rate_limited', 'unauthorized', "
    "'unexpected_answer', 'repository_unreachable', 'moved_out_of_reach', 'reopen_blocked', "
    "'write_refused', 'public_repository'"
)
# A GitHub `owner/name#number`, or a Jira key (FR40); never an address.
_EXTERNAL_KEY = (
    r"^([A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}#[1-9][0-9]{0,9}"
    r"|[A-Z][A-Z0-9_]+-[0-9]+)$"
)
# The tracker's own numeric id for the issue.
_EXTERNAL_ID = r"^[0-9]{1,20}$"
_FIELD_KEY = r"^[a-zA-Z0-9_]+$"
_LIVE_SYNCED = "role = 'synced' AND sync_status <> 'stopped'"


def upgrade() -> None:
    op.create_table(
        "finding_links",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        # Always the case's root: a link made from a joined finding's page lands there.
        sa.Column("finding_id", _UUID, nullable=False),
        sa.Column("connection_id", _UUID, nullable=True),
        sa.Column("place_id", _UUID, nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("sync_status", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("external_key", sa.Text(), nullable=True),
        sa.Column("seen_state", sa.Text(), nullable=True),
        sa.Column("wanted_state", sa.Text(), nullable=True),
        sa.Column("comment_wanted", sa.Text(), nullable=True),
        sa.Column("comment_written_at", _TIMESTAMP, nullable=True),
        sa.Column("idle_warning_at", _TIMESTAMP, nullable=True),
        sa.Column("tracker_closed_at", _TIMESTAMP, nullable=True),
        sa.Column("frozen_facts", postgresql.JSONB(), nullable=True),
        sa.Column("first_attempt_at", _TIMESTAMP, nullable=True),
        sa.Column("next_sync_at", _TIMESTAMP, nullable=True),
        sa.Column("last_synced_at", _TIMESTAMP, nullable=True),
        sa.Column("gone_at", _TIMESTAMP, nullable=True),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_failure", sa.Text(), nullable=True),
        sa.Column("last_failure_field", sa.Text(), nullable=True),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "id", name="uq_finding_links_org_id_id"),
        # One issue is attached to one case at most once, whatever its role.
        sa.UniqueConstraint(
            "finding_id", "connection_id", "external_id", name="uq_finding_links_issue_per_case"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "finding_id"],
            ["findings.org_id", "findings.id"],
            ondelete="CASCADE",
            name="fk_finding_links_finding",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "connection_id"],
            ["connections.org_id", "connections.id"],
            ondelete="SET NULL (connection_id)",
            name="fk_finding_links_connection",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "place_id"],
            ["connection_repositories.org_id", "connection_repositories.id"],
            ondelete="SET NULL (place_id)",
            name="fk_finding_links_place",
        ),
        sa.CheckConstraint(f"role IN ({_ROLES})", name="role_is_known"),
        sa.CheckConstraint(f"sync_status IN ({_STATUSES})", name="status_is_known"),
        sa.CheckConstraint(
            f"(seen_state IS NULL OR seen_state IN ({_ISSUE_STATES})) "
            f"AND (wanted_state IS NULL OR wanted_state IN ({_ISSUE_STATES}))",
            name="issue_states_are_known",
        ),
        sa.CheckConstraint(
            f"comment_wanted IS NULL OR comment_wanted IN ({_COMMENTS})", name="comment_is_known"
        ),
        sa.CheckConstraint(
            f"last_failure IS NULL OR last_failure IN ({_FAILURES})", name="failure_is_known"
        ),
        sa.CheckConstraint(
            f"external_key IS NULL OR external_key ~ '{_EXTERNAL_KEY}'", name="key_is_strict"
        ),
        sa.CheckConstraint(
            f"external_id IS NULL OR external_id ~ '{_EXTERNAL_ID}'", name="external_id_is_an_id"
        ),
        sa.CheckConstraint(
            f"last_failure_field IS NULL OR last_failure_field ~ '{_FIELD_KEY}'",
            name="failure_field_is_a_key",
        ),
        # The preview's facts live exactly as long as the issue is being created (AC83).
        sa.CheckConstraint(
            "(sync_status = 'creating') = (frozen_facts IS NOT NULL)",
            name="facts_frozen_while_creating",
        ),
        # Only a synced link is created by nexovue (AC88's click route).
        sa.CheckConstraint(
            "sync_status <> 'creating' OR role = 'synced'", name="only_a_synced_link_is_created"
        ),
        # Every link names its issue, except one not created yet, refused before it was,
        # or stopped by a disconnect before its creation was known.
        sa.CheckConstraint(
            "sync_status IN ('creating', 'refused', 'stopped') OR external_key IS NOT NULL",
            name="a_link_names_its_issue",
        ),
        sa.CheckConstraint("failures >= 0", name="failures_count_up"),
    )
    # FR41: one synced issue per case, and one case per synced issue; a stopped link
    # (a disconnect, FR32) holds neither slot.
    op.create_index(
        "uq_finding_links_one_synced_per_case",
        "finding_links",
        ["finding_id"],
        unique=True,
        postgresql_where=sa.text(_LIVE_SYNCED),
    )
    op.create_index(
        "uq_finding_links_synced_once",
        "finding_links",
        ["connection_id", "external_id"],
        unique=True,
        postgresql_where=sa.text(_LIVE_SYNCED),
    )
    op.create_index(
        "ix_finding_links_due",
        "finding_links",
        ["connection_id", "next_sync_at"],
        postgresql_where=sa.text("next_sync_at IS NOT NULL"),
    )
    # The finding page reads its case's links.
    op.create_index("ix_finding_links_org_id_finding_id", "finding_links", ["org_id", "finding_id"])


def downgrade() -> None:
    op.drop_table("finding_links")
