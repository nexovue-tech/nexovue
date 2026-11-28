"""The one statement that deletes a suggestion's stored code (design, *What deletion
empties, the one list*; spec BR9).

Held as SQL text, as `jobs.py`'s `ENQUEUE` is, because this package may not import a
database driver (`test_cross_service_contracts.py`). Every trigger runs this one
statement: a refusal or failure, the time limits, a withdrawal, AI switched off, lost
GitHub access, and the restore.

**The columns and tables are Step 3's migration's** (plan T2.5, T3.1), and the third
migration's (Step 7, T7.1, 0083) `requester_note` and `approved_digest`. The fact, state,
reason, counts, timestamps, the verdict and pull request details stay.

**The restore** (Step 5, T5.5, ADR-095) runs the same list over every row, by `psql`, from
`deploy/backup/clear-stored-code.sql`, which `restore_sql()` writes and
`test_fix_backup_scripts.py` holds equal; and the backup leaves out the rows of the tables
that hold only code or a sealed code (`ROWS_NOT_BACKED_UP`).
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
    # Step 7 (0083): the person's own words, and the digest that names the changed content.
    "requester_note",
    "approved_digest",
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


# The backup's dump leaves these tables' rows out, and its manifest does not count them
# (design, *Backups*; AC32): the pack's whole files, each check's patch, a link attempt's
# sealed code. `deploy/backup/common.sh` holds the same list.
ROWS_NOT_BACKED_UP: Final = (*STORED_CODE_TABLES, "github_link_attempts")
# In-flight states, and the reason a restore ends them with: their jobs are gone, and every
# consent is withdrawn in the same transaction.
_IN_FLIGHT: Final = "'gathering', 'gathered', 'proposing'"
_RESTORE_HEADER: Final = """\
-- F5 (spec BR9, AC6a; ADR-095): a restore brings back code that was deleted after its
-- snapshot, so every copy goes again, work in flight ends, and every consent is withdrawn
-- for an owner to agree again. Written by `nexovue_platform.fixes.statements.restore_sql`;
-- `test_fix_backup_scripts.py` holds this file equal to it. `restore-postgres.sh` runs it
-- with `psql -c`, as one transaction.
"""


def restore_sql() -> str:
    """The one deletion list over every row, for `restore-postgres.sh` (no driver here)."""
    stored = " OR ".join(f"{column} IS NOT NULL" for column in STORED_CODE_COLUMNS)
    deletes = "".join(f"DELETE FROM {table};\n" for table in ROWS_NOT_BACKED_UP)
    return (
        _RESTORE_HEADER
        + "UPDATE finding_fixes\n"
        + "SET state = 'failed', reason = 'consent_ended_in_flight', state_changed_at = now(),\n"
        + "    finished_at = now(), path_candidates = NULL, requested_paths = NULL\n"
        + f"WHERE state IN ({_IN_FLIGHT});\n"
        # A draft's job is gone too: back to `proposed`, as the sweep does (Step 7). A
        # branch it stored keeps the one-draft hold until the poller settles it.
        + "UPDATE finding_fixes\n"
        + "SET state = 'proposed', state_changed_at = now(),\n"
        + "    last_write_failure = 'did_not_finish'\n"
        + "WHERE state = 'approved';\n"
        + deletes
        + "UPDATE finding_fixes\n"
        + f"SET {_EMPTIED},\n"
        + "    code_deleted_at = COALESCE(code_deleted_at, now()),\n"
        + "    code_deleted_reason = COALESCE(code_deleted_reason, 'restored')\n"
        + f"WHERE kind = 'source' AND ({stored});\n"
        + "UPDATE fix_source_consents SET withdrawn_at = now(), withdrawn_reason = 'restored'\n"
        + "WHERE withdrawn_at IS NULL;\n"
    )
