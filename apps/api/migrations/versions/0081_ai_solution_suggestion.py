"""A suggestion from a repository's files: the model's and the rules' codes

F5 (`docs/features/ai-solution/`), plan Step 4 (T4.4, T4.5, T4.12, T4.13); ADR-091.

**Why this change is required.** Step 4 sends the listed files to the AI provider and
stores a checked suggestion, or why there is none (design, *Every sentence*). The row's
closed lists need Step 4's codes:

- `finding_fixes.reason` gains `not_in_these_files` (the model's own abstention),
  `edit_did_not_apply` (a misquote after its one retry), `withheld_K1` to `withheld_K10`
  (a rule refused the change; only the rule is stored, never the text), and
  `checks_refused_at_click` (a stored suggestion the rules refuse again: D18's tamper
  signal);
- `finding_fixes.retry_code` gains rung 3's three fixed retry codes (D51): an answer that
  failed the schema, a misquote, and an edit on a never-changed file;
- `finding_fix_checks.reason` gets the CHECK its closed list needs, now that Step 4's
  "Check my access" writes it;
- `fix_drop_pack`, the AI account's one way to delete the files it was sent, only while
  their suggestion is `proposing`, returning how many rows went (ADR-091). F3 gives the
  account no table-level `DELETE` anywhere, because any holder of one may `LOCK` the
  table (`insight_account.py`); a function keeps that true for the pack too. Its
  `EXECUTE` for the AI account is `setup`'s.

**Affected store and schema.** Postgres: three checks replaced or added (`NOT VALID`,
then validated), and one function. No table, column or index.

**Compatibility and backfill.** None needed: each list only widens, and
`finding_fix_checks` has no rows before Step 4 writes one.

**Rollback and recovery.** Roll forward only: a stored suggestion's reason is the record
of why a customer's code was withheld.

**Tenant, retention, and security impact.** No new data. The function binds the
organization and the suggestion, and deletes only a pack whose suggestion is `proposing`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0081"
down_revision: str | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of `nexovue_platform.fixes.records`' lists, held equal by
# `test_fix_suggestion_storage.py`: a later change to them is a migration of its own.
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
    "'withheld_K8', 'withheld_K9', 'withheld_K10', 'checks_refused_at_click'"
)
_RETRY_CODES = (
    "'general_shape', 'general_too_long', 'source_shape', 'source_misquote', 'source_never_changed'"
)
_CHECK_REASONS = (
    "'not_linked', 'no_github_access', 'access_unknown', 'github_unavailable', "
    "'permission_missing', 'consent_withdrawn', 'nexovue_error'"
)


# ADR-091: the AI account deletes a pack only through this, only while `proposing`.
_DROP_PACK_SIGNATURE = "fix_drop_pack(uuid, uuid)"
_DROP_PACK = """
CREATE FUNCTION fix_drop_pack(for_fix uuid, for_org uuid)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    dropped integer;
BEGIN
    DELETE FROM public.finding_fix_source_packs AS pack
    WHERE pack.fix_id = for_fix AND pack.org_id = for_org
      AND EXISTS (SELECT 1 FROM public.finding_fixes AS fix
                  WHERE fix.id = for_fix AND fix.org_id = for_org
                    AND fix.kind = 'source' AND fix.state = 'proposing');
    GET DIAGNOSTICS dropped = ROW_COUNT;
    RETURN dropped;
END
$$
"""


def _replace_check(table: str, name: str, condition: str, *, exists: bool = True) -> None:
    if exists:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID")
    op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def upgrade() -> None:
    _replace_check(
        "finding_fixes",
        "ck_finding_fixes_reason_is_known",
        f"reason IS NULL OR reason IN ({_FIX_REASONS})",
    )
    _replace_check(
        "finding_fixes",
        "ck_finding_fixes_retry_code_is_known",
        f"retry_code IS NULL OR retry_code IN ({_RETRY_CODES})",
    )
    _replace_check(
        "finding_fix_checks",
        "ck_finding_fix_checks_reason_is_known",
        f"reason IS NULL OR reason IN ({_CHECK_REASONS})",
        exists=False,
    )
    op.execute(_DROP_PACK)
    op.execute(f"REVOKE EXECUTE ON FUNCTION {_DROP_PACK_SIGNATURE} FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: a stored suggestion's reason is the record of why a customer's "
        "code was withheld. Going back past 0081 is a restore."
    )
