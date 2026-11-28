"""The draft pull request: what the write path stores, and one draft per finding

F5 (`docs/features/ai-solution/`), plan Step 7 (T7.1); ADR-100, ADR-101.

**Why this change is required.** Step 7 lets a person with write access open a suggestion
as a draft pull request, through a job on `scheduler-connections` (design, *The draft
click*). **Write access stays off in code** until the founder's recorded go
(`nexovue_platform/fixes/write_gate.py`, ADR-097; foundation item 33), so nothing here is
written by the shipped product until then; the columns exist so the path can be built and
tested against the simulated GitHub. The third migration the design names:

- `finding_fixes` gains the columns written only by the write path: who approved, when,
  the digest they approved and their note (FR22, FR24a); who asked for CI and when (Step
  8); the pull request's number, address, branch, state and the commits Nexovue made; and
  the last write failure, a fixed code;
- the states `approved` and `opened`;
- **one draft Nexovue can still see per finding** (BR8): a unique index over `approved`
  rows and rows holding a branch whose pull request is open or not yet known, so a second
  draft is refused by the store from the moment before `POST /pulls`;
- `fix_repository_settings`: an owner's per-repository "Allow draft pull requests" and
  "Run CI when a draft opens" (BR14, D45), and when GitHub last refused a draft;
- `finding_fix_checks.reason` gains a draft check's refusals, and `writes_off`;
- the timeline's `fix_pull_request_opened`.

**Affected store and schema.** Postgres: one table, fourteen columns, two foreign keys,
five checks added or replaced (`NOT VALID`, then validated), one unique index built
concurrently. No function and no grant: `nexovue_insight` reads none of these columns
(design, *Grants*), and its row policy admits neither new state.

**Compatibility and backfill.** None needed: every new column is nullable, every list only
widens, and no row is `approved` or holds a branch yet.

**Rollback and recovery.** Roll forward only: an owner's per-repository choice and a
person's approval are recorded acts held nowhere else.

**Tenant, retention, and security impact.** Composite keys hold the tenant: the settings to
`connection_repositories (org_id, id)`, the people to `users (org_id, id)`. The requester's
note and the approved digest are on the one deletion list (`clear_stored_code`); the pull
request's number, address, branch and commits are the record and stay. No secret.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0083"
down_revision: str | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies of `nexovue_platform.fixes.records`' lists, held equal by
# `test_fix_write_storage.py`: a later change to them is a migration of its own.
_FIX_STATES = (
    "'gathering', 'gathered', 'proposing', 'proposed', 'refused', 'failed', 'stale', "
    "'approved', 'opened'"
)
_CHECK_REASONS = (
    "'not_linked', 'no_github_access', 'access_unknown', 'github_unavailable', "
    "'permission_missing', 'consent_withdrawn', 'nexovue_error', 'rules_changed', "
    "'mapping_changed', 'cause_changed', 'edit_no_longer_applies', "
    "'checks_refused_at_click', 'writes_off', 'drafts_not_allowed', 'public_not_allowed', "
    "'no_write_permission', 'no_draft_support', 'needs_likely_cause'"
)
_PULL_REQUEST_STATES = "'open', 'merged', 'closed', 'not_opened', 'untracked'"
_WRITE_FAILURES = (
    "'writes_off', 'changed_since_check', 'branch_not_ours', 'did_not_finish', "
    "'no_draft_support', 'drafts_not_allowed', 'public_not_allowed', 'no_write_permission', "
    "'needs_likely_cause', 'no_github_access', 'not_linked', 'access_unknown', "
    "'permission_missing', 'github_unavailable', 'consent_withdrawn'"
)
# F22's timeline vocabulary with F5's kinds, held equal by `test_alert_vocabulary.py`.
_FINDING_EVENT_KINDS = (
    "'finding_opened', 'severity_raised', 'signal_joined', 'finding_closed', "
    "'flapping_started', 'finding_joined_case', 'finding_left_case', 'case_started', "
    "'acknowledged', 'assigned', 'unassigned', 'case_resolved', 'case_reopened', "
    "'false_alarm', 'new_normal', 'note_added', 'note_deleted', 'link_added', "
    "'link_removed', 'link_promoted', 'issue_created', 'issue_closed', 'issue_reopened', "
    "'issue_gone', 'issue_moved', 'tracker_write_failed', 'explained', 'fix_proposed', "
    "'fix_pull_request_opened'"
)
# FR24a's note, and the one branch a suggestion writes (FR23).
_REQUESTER_NOTE_MAX_CHARACTERS = 200
_BRANCH_PREFIX = "nexovue/suggestion-"
_SHA = "^[0-9a-f]{40}$"
# BR8: one draft Nexovue can still see. `untracked` and `not_opened` free it; a
# withdrawal does not.
_ONE_DRAFT = (
    "state = 'approved' OR (pull_request_branch IS NOT NULL "
    "AND (pull_request_state IS NULL OR pull_request_state = 'open'))"
)


def _replace_check(table: str, name: str, condition: str, *, exists: bool = True) -> None:
    if exists:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID")
    op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def _person_key(column: str) -> None:
    name = f"fk_finding_fixes_{column}"
    op.execute(
        f"ALTER TABLE finding_fixes ADD CONSTRAINT {name} FOREIGN KEY (org_id, {column}) "
        f"REFERENCES users (org_id, id) ON DELETE SET NULL ({column}) NOT VALID"
    )
    op.execute(f"ALTER TABLE finding_fixes VALIDATE CONSTRAINT {name}")


def _upgrade_finding_fixes() -> None:
    columns: tuple[sa.Column[Any], ...] = (
        sa.Column("approved_by", _UUID, nullable=True),
        sa.Column("approved_at", _TIMESTAMP, nullable=True),
        # The digest the person's own check reached; the job must reach it too (FR22).
        sa.Column("approved_digest", sa.Text(), nullable=True),
        # FR24a: the requester's one line, under FR16's prose checks; on the deletion list.
        sa.Column("requester_note", sa.Text(), nullable=True),
        # FR24a: the requester agreed that the note may go into a public repository's body.
        sa.Column(
            "requester_note_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("ci_requested_by", _UUID, nullable=True),
        sa.Column("ci_requested_at", _TIMESTAMP, nullable=True),
        sa.Column("pull_request_number", sa.Integer(), nullable=True),
        sa.Column("pull_request_url", sa.Text(), nullable=True),
        sa.Column("pull_request_branch", sa.Text(), nullable=True),
        sa.Column("pull_request_state", sa.Text(), nullable=True),
        sa.Column("pull_request_commit_sha", sa.Text(), nullable=True),
        sa.Column("ci_commit_sha", sa.Text(), nullable=True),
        sa.Column("last_write_failure", sa.Text(), nullable=True),
    )
    for column in columns:
        op.add_column("finding_fixes", column)
    _person_key("approved_by")
    _person_key("ci_requested_by")
    table = "finding_fixes"
    _replace_check(table, "ck_finding_fixes_state_is_known", f"state IN ({_FIX_STATES})")
    checks = {
        "ck_finding_fixes_pull_request_state_is_known": (
            f"pull_request_state IS NULL OR pull_request_state IN ({_PULL_REQUEST_STATES})"
        ),
        "ck_finding_fixes_last_write_failure_is_known": (
            f"last_write_failure IS NULL OR last_write_failure IN ({_WRITE_FAILURES})"
        ),
        "ck_finding_fixes_requester_note_is_short": (
            f"requester_note IS NULL OR length(requester_note) <= {_REQUESTER_NOTE_MAX_CHARACTERS}"
        ),
        # FR23: the one branch this suggestion may write, and nothing else.
        "ck_finding_fixes_branch_is_its_own": (
            f"pull_request_branch IS NULL OR pull_request_branch = '{_BRANCH_PREFIX}' || id::text"
        ),
        "ck_finding_fixes_commits_are_shas": (
            f"(pull_request_commit_sha IS NULL OR pull_request_commit_sha ~ '{_SHA}') "
            f"AND (ci_commit_sha IS NULL OR ci_commit_sha ~ '{_SHA}')"
        ),
        "ck_finding_fixes_approved_names_when": (
            "state <> 'approved' OR (approved_at IS NOT NULL AND approved_digest IS NOT NULL)"
        ),
        "ck_finding_fixes_only_a_source_row_is_written": (
            "kind = 'source' OR (approved_at IS NULL AND pull_request_branch IS NULL)"
        ),
    }
    for name, condition in checks.items():
        _replace_check(table, name, condition, exists=False)


def _one_draft_per_finding() -> None:
    """BR8 in the store, built concurrently so writes continue (`test_migration_rules.py`)."""
    with op.get_context().autocommit_block():
        # An earlier failed build leaves an invalid index of this name; build it again.
        op.drop_index(
            "uq_finding_fixes_one_draft",
            "finding_fixes",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.create_index(
            "uq_finding_fixes_one_draft",
            "finding_fixes",
            ["finding_id"],
            unique=True,
            postgresql_where=sa.text(_ONE_DRAFT),
            postgresql_concurrently=True,
        )


def _create_repository_settings() -> None:
    op.create_table(
        "fix_repository_settings",
        sa.Column(
            "org_id",
            _UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("connection_repository_id", _UUID, nullable=False),
        sa.Column("drafts_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ci_on_open", sa.Boolean(), nullable=False, server_default=sa.false()),
        # Learned from GitHub's refusal of a draft; ignored after 24 hours (AC27a).
        sa.Column("draft_refused_at", _TIMESTAMP, nullable=True),
        sa.Column("set_by", _UUID, nullable=True),
        sa.Column("set_at", _TIMESTAMP, nullable=False),
        sa.PrimaryKeyConstraint("org_id", "connection_repository_id"),
        sa.ForeignKeyConstraint(
            ["org_id", "connection_repository_id"],
            ["connection_repositories.org_id", "connection_repositories.id"],
            ondelete="CASCADE",
            name="fk_fix_repository_settings_repository",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "set_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (set_by)",
            name="fk_fix_repository_settings_set_by",
        ),
    )


def upgrade() -> None:
    _upgrade_finding_fixes()
    _replace_check(
        "finding_fix_checks",
        "ck_finding_fix_checks_reason_is_known",
        f"reason IS NULL OR reason IN ({_CHECK_REASONS})",
    )
    _replace_check(
        "finding_events",
        "ck_finding_events_kind_is_known",
        f"kind IN ({_FINDING_EVENT_KINDS})",
    )
    _create_repository_settings()
    _one_draft_per_finding()


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: an owner's per-repository choice and a person's approval are "
        "recorded acts held nowhere else. Going back past 0083 is a restore."
    )
