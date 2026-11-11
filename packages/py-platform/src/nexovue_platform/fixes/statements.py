"""The one statement that deletes a suggestion's stored code (design, *What deletion
empties, the one list*; spec BR9).

Held as SQL text, as `jobs.py`'s `ENQUEUE` is, because this package may not import a
database driver (`test_cross_service_contracts.py`). Every trigger runs this one
statement: a refusal or failure, the time limits, a withdrawal, AI switched off, lost
GitHub access, and the restore.

**The columns and tables are Step 3's migration's** (plan T2.5, T3.1). Until the third
migration (Step 7) exists, `requester_note` and `approved_digest` are left out, and
T7.1 adds them, so this never names a column that is not there. The fact, state,
reason, counts, timestamps and pull request details stay.
"""

from __future__ import annotations

from typing import Final

# Source, or what points into it, on `finding_fixes`.
STORED_CODE_COLUMNS: Final = (
    "edits",
    "files_read",
    "path_candidates",
    "requested_paths",
    "withheld_refs",
    "files_digest",
)
# Tables whose every row for the suggestion is source: the pack, and each check's patch.
STORED_CODE_TABLES: Final = ("finding_fix_source_packs", "finding_fix_checks")

_EMPTIED: Final = ", ".join(f"{column} = NULL" for column in STORED_CODE_COLUMNS)
_DELETES: Final = ",\n".join(
    f"""{table}_deleted AS (
    DELETE FROM {table}
    WHERE org_id = CAST(:org_id AS uuid) AND fix_id = CAST(:fix_id AS uuid)
)"""
    for table in STORED_CODE_TABLES
)

# One statement, so a crash cannot leave the pack gone and `edits` kept. A second run
# keeps the first deletion's time and reason. Returns 1 when the suggestion exists.
CLEAR_STORED_CODE: Final = f"""
WITH {_DELETES},
cleared AS (
    UPDATE finding_fixes
    SET {_EMPTIED},
        code_deleted_at = COALESCE(code_deleted_at, now()),
        code_deleted_reason = COALESCE(code_deleted_reason, :reason)
    WHERE org_id = CAST(:org_id AS uuid) AND id = CAST(:fix_id AS uuid)
    RETURNING id
)
SELECT count(*) FROM cleared
"""
