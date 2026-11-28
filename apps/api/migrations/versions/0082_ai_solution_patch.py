"""A suggestion's patch, a person's verdict, and taking stored code back

F5 (`docs/features/ai-solution/`), plan Step 5 (T5.1 to T5.3); ADR-094.

**Why this change is required.** Step 5 serves a written suggestion as a patch, rebuilt by
a person's own check run, asks "Was this suggestion useful?", and deletes stored code at
every end the design names (spec BR9, FR29):

- `finding_fixes` gains `verdict`, `verdict_by` and `verdict_at` (FR29, UR11; design,
  `finding_fixes`): one answer per suggestion, the last person's, changing nothing else;
- `finding_fixes.reason` and `code_deleted_reason` gain `project_deleted`: deleting a
  project ends its suggestions still being prepared and deletes their stored code
  (ADR-094);
- `finding_fix_checks.reason` gains the four out-of-date codes and
  `checks_refused_at_click`, which a `patch` check now stores (design, *The check run*,
  step 5);
- F22's `connection_repositories` gains `unreachable_since`, so BR9's "GitHub access gone,
  72 hours" row has a clock: set when a refresh first finds the repository unreachable,
  cleared when it answers again.

**Affected store and schema.** Postgres: four columns, one foreign key, four checks
replaced or added (`NOT VALID`, then validated). No table, index or function.

**Compatibility and backfill.** None needed: every new column is nullable, every list
only widens, and no row holds a verdict yet. A repository unreachable now gets its clock
from its next refresh, so its 72 hours start then: the safe side, since nothing is
deleted early.

**Rollback and recovery.** Roll forward only: a verdict is a person's recorded answer,
held nowhere else.

**Tenant, retention, and security impact.** The verdict's person is a composite key to
`users` in the same organization, set null only if the person's row goes (member removal
keeps it). A verdict is personal data: the person's export lists it. No source, no secret.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0082"
down_revision: str | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies of `nexovue_platform.fixes.records`' lists, held equal by
# `test_fix_patch_storage.py`: a later change to them is a migration of its own.
_FIX_REASONS = (
    "'evidence_too_thin', 'needs_a_decision', 'not_a_code_problem', 'general_withheld_G3', "
    "'general_withheld_G4', 'general_withheld_G5', 'general_withheld_G6', "
    "'general_withheld_G7', 'general_withheld_G8', 'general_withheld_G9', 'ai_off', "
    "'project_opted_out', 'consent_ended_in_flight', 'limit_reached', 'provider_refused', "
    "'provider_too_long', 'provider_unavailable', 'invalid_output', 'timed_out', "
    "'nexovue_error', 'no_usable_file', 'stack_aged_out', 'no_stack', "
    "'repository_too_large', 'cancelled', 'lapsed', 'github_unavailable', "
    "'permission_missing', 'not_linked', 'no_github_access', 'access_unknown', "
    "'rules_changed', 'mapping_changed', 'cause_changed', 'edit_no_longer_applies', "
    "'not_in_these_files', 'edit_did_not_apply', 'withheld_K1', 'withheld_K2', "
    "'withheld_K3', 'withheld_K4', 'withheld_K5', 'withheld_K6', 'withheld_K7', "
    "'withheld_K8', 'withheld_K9', 'withheld_K10', 'checks_refused_at_click', "
    "'project_deleted'"
)
_CODE_DELETED_REASONS = (
    "'ended_short', 'pull_request_done', 'aged_out', 'consent_withdrawn', 'disconnected', "
    "'ai_off', 'access_gone', 'restored', 'project_deleted'"
)
_CHECK_REASONS = (
    "'not_linked', 'no_github_access', 'access_unknown', 'github_unavailable', "
    "'permission_missing', 'consent_withdrawn', 'nexovue_error', 'rules_changed', "
    "'mapping_changed', 'cause_changed', 'edit_no_longer_applies', 'checks_refused_at_click'"
)
_VERDICTS = "'merge_as_is', 'useful_start', 'wrong'"


def _replace_check(table: str, name: str, condition: str, *, exists: bool = True) -> None:
    if exists:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID")
    op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def upgrade() -> None:
    op.add_column("finding_fixes", sa.Column("verdict", sa.Text(), nullable=True))
    op.add_column("finding_fixes", sa.Column("verdict_by", _UUID, nullable=True))
    op.add_column("finding_fixes", sa.Column("verdict_at", _TIMESTAMP, nullable=True))
    op.execute(
        "ALTER TABLE finding_fixes ADD CONSTRAINT fk_finding_fixes_verdict_by "
        "FOREIGN KEY (org_id, verdict_by) REFERENCES users (org_id, id) "
        "ON DELETE SET NULL (verdict_by) NOT VALID"
    )
    op.execute("ALTER TABLE finding_fixes VALIDATE CONSTRAINT fk_finding_fixes_verdict_by")
    _replace_check(
        "finding_fixes",
        "ck_finding_fixes_verdict_is_known",
        f"(verdict IS NULL) = (verdict_at IS NULL) AND (verdict IS NULL OR verdict IN "
        f"({_VERDICTS}))",
        exists=False,
    )
    _replace_check(
        "finding_fixes",
        "ck_finding_fixes_reason_is_known",
        f"reason IS NULL OR reason IN ({_FIX_REASONS})",
    )
    _replace_check(
        "finding_fixes",
        "ck_finding_fixes_code_deleted_reason_is_known",
        "(code_deleted_at IS NULL) = (code_deleted_reason IS NULL) AND "
        f"(code_deleted_reason IS NULL OR code_deleted_reason IN ({_CODE_DELETED_REASONS}))",
    )
    _replace_check(
        "finding_fix_checks",
        "ck_finding_fix_checks_reason_is_known",
        f"reason IS NULL OR reason IN ({_CHECK_REASONS})",
    )
    op.add_column(
        "connection_repositories", sa.Column("unreachable_since", _TIMESTAMP, nullable=True)
    )


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: a verdict is a person's recorded answer, held nowhere else. Going "
        "back past 0082 is a restore."
    )
