-- F5 (spec BR9, AC6a; ADR-095): a restore brings back code that was deleted after its
-- snapshot, so every copy goes again, work in flight ends, and every consent is withdrawn
-- for an owner to agree again. Written by `nexovue_platform.fixes.statements.restore_sql`;
-- `test_fix_backup_scripts.py` holds this file equal to it. `restore-postgres.sh` runs it
-- with `psql -c`, as one transaction.
UPDATE finding_fixes
SET state = 'failed', reason = 'consent_ended_in_flight', state_changed_at = now(),
    finished_at = now(), path_candidates = NULL, requested_paths = NULL
WHERE state IN ('gathering', 'gathered', 'proposing');
DELETE FROM finding_fix_source_packs;
DELETE FROM finding_fix_checks;
DELETE FROM github_link_attempts;
UPDATE finding_fixes
SET edits = NULL, files_read = NULL, path_candidates = NULL, requested_paths = NULL, withheld_refs = NULL, files_digest = NULL,
    code_deleted_at = COALESCE(code_deleted_at, now()),
    code_deleted_reason = COALESCE(code_deleted_reason, 'restored')
WHERE kind = 'source' AND (edits IS NOT NULL OR files_read IS NOT NULL OR path_candidates IS NOT NULL OR requested_paths IS NOT NULL OR withheld_refs IS NOT NULL OR files_digest IS NOT NULL);
UPDATE fix_source_consents SET withdrawn_at = now(), withdrawn_reason = 'restored'
WHERE withdrawn_at IS NULL;
