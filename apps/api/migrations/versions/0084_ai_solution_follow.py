"""Run CI and following the pull request: what Step 8 stores

F5 (`docs/features/ai-solution/`), plan Step 8 (T8.1, T8.2).

**Why this change is required.** Step 8 adds **Run CI**, a person's click that pushes one
same-tree commit with no skip marker to `nexovue/suggestion-<id>`, and F5's own poller, which
follows a draft on GitHub until it is merged, closed or out of reach (design, *Run CI*,
*Jobs*; spec FR25, FR27, FR30, BR9). **Write access stays off in code** until the founder's
recorded go (`nexovue_platform/fixes/write_gate.py`, ADR-097; foundation item 33), so no row
can hold a pull request yet. 0083's columns do not suffice:

- Run CI must tell a retry from a second click, and must not refuse its own commit after a
  crash between storing it and moving the branch (FR25, AC28). So the commit is stored with
  the request it answers (`ci_commit_requested_at`) and the head it was built on
  (`ci_parent_sha`), and `ci_pushed_at` says the branch moved for that request;
- BR9 deletes code 7 days after a pull request was merged or closed, and FR30 says when it
  was merged, so the state needs a time (`pull_request_state_at`); FR30's "deployed at Y
  where F28 records that commit" needs the merge's commit (`pull_request_merge_commit_sha`);
- the poller visits the longest-unvisited rows first (`pull_request_polled_at`);
- `last_write_failure` gains Run CI's two refusals, `branch_changed` and
  `pull_request_not_open`.

**Affected store and schema.** Postgres: `finding_fixes` gains six nullable columns; one
check replaced and one added (`NOT VALID`, then validated). No index, no function, no grant:
`nexovue_insight` reads none of these columns.

**Compatibility and backfill.** None needed: every column is nullable, the list only widens,
and no row holds a pull request yet.

**Rollback and recovery.** Roll forward only, as 0083: a person's Run CI request and what
GitHub said of a pull request are records held nowhere else.

**Tenant, retention, and security impact.** No new key or table. Every column is the record
(commit shas, times), none is stored code, so none joins `clear_stored_code`. No secret.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0084"
down_revision: str | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMP = sa.DateTime(timezone=True)

# A frozen copy of `nexovue_platform.fixes.records.WRITE_FAILURES`, held equal by
# `test_fix_follow_storage.py`: a later change to it is a migration of its own.
_WRITE_FAILURES = (
    "'writes_off', 'changed_since_check', 'branch_not_ours', 'did_not_finish', "
    "'no_draft_support', 'drafts_not_allowed', 'public_not_allowed', 'no_write_permission', "
    "'needs_likely_cause', 'no_github_access', 'not_linked', 'access_unknown', "
    "'permission_missing', 'github_unavailable', 'consent_withdrawn', 'branch_changed', "
    "'pull_request_not_open'"
)
_SHA = "^[0-9a-f]{40}$"


def _replace_check(name: str, condition: str, *, exists: bool = True) -> None:
    if exists:
        op.execute(f"ALTER TABLE finding_fixes DROP CONSTRAINT {name}")
    op.execute(f"ALTER TABLE finding_fixes ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID")
    op.execute(f"ALTER TABLE finding_fixes VALIDATE CONSTRAINT {name}")


def upgrade() -> None:
    columns: tuple[sa.Column[Any], ...] = (
        # Run CI (FR25): the head the latest Run CI commit was built on, the request it
        # answers, and when the branch moved to it for that request.
        sa.Column("ci_parent_sha", sa.Text(), nullable=True),
        sa.Column("ci_commit_requested_at", _TIMESTAMP, nullable=True),
        sa.Column("ci_pushed_at", _TIMESTAMP, nullable=True),
        # FR27, FR30, BR9: when the stored pull request state last changed (GitHub's merge
        # or close time where it gives one), and the commit a merge made.
        sa.Column("pull_request_state_at", _TIMESTAMP, nullable=True),
        sa.Column("pull_request_merge_commit_sha", sa.Text(), nullable=True),
        # The poller's own clock: the least recently visited rows go first.
        sa.Column("pull_request_polled_at", _TIMESTAMP, nullable=True),
    )
    for column in columns:
        op.add_column("finding_fixes", column)
    _replace_check(
        "ck_finding_fixes_last_write_failure_is_known",
        f"last_write_failure IS NULL OR last_write_failure IN ({_WRITE_FAILURES})",
    )
    _replace_check(
        "ck_finding_fixes_followed_commits_are_shas",
        f"(ci_parent_sha IS NULL OR ci_parent_sha ~ '{_SHA}') "
        f"AND (pull_request_merge_commit_sha IS NULL OR pull_request_merge_commit_sha ~ '{_SHA}')",
        exists=False,
    )


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: a person's Run CI request and what GitHub said of a pull request "
        "are records held nowhere else. Going back past 0084 is a restore."
    )
