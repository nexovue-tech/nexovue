"""The backup and the restore keep F5's deletion promises (plan T5.4, T5.5; spec AC6a, AC32;
design, *Backups*; ADR-095).

Read from the scripts themselves, so a change that drops a table from the list, or moves
the emptying away from the restore, fails here, in the gate. The release checks that run
the scripts through Docker (`test_fix_restore_docker.py`) are run by hand per release.

- `backup.sh` dumps the pack, check and link attempt tables empty, and leaves them out of
  the manifest, so `verify.sh` never fails on rows a restore must not bring back (AC32).
- `restore-postgres.sh` runs the one deletion list over every row after `pg_restore`,
  ends work in flight and withdraws every consent, then says `nexovue setup` comes next
  (AC6a); `clear-stored-code.sql` is exactly what `statements.restore_sql()` writes.
- `nexovue restore` stops both F5 roles before it restores.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from nexovue_platform.fixes import statements

import nexovue_api

DEPLOY: Final = Path(nexovue_api.__file__).parents[4] / "deploy"
BACKUP: Final = DEPLOY / "backup"


def _text(name: str) -> str:
    return (BACKUP / name).read_text(encoding="utf-8")


def test_ac32_common_sh_names_exactly_the_tables_a_backup_never_holds() -> None:
    found = re.search(r"^POSTGRES_ROWS_NOT_KEPT=\(([^)]*)\)$", _text("common.sh"), re.MULTILINE)

    assert found is not None
    assert tuple(found.group(1).split()) == statements.ROWS_NOT_BACKED_UP


def test_ac32_the_dump_excludes_those_tables_rows() -> None:
    script = _text("backup.sh")

    assert 'for table in "${POSTGRES_ROWS_NOT_KEPT[@]}"; do' in script
    assert 'not_kept+=("--exclude-table-data=public.$table")' in script
    dump = script[script.index("pg_dump -U") : script.index('> "$snapshot/postgres.dump"')]
    assert '"${not_kept[@]}"' in dump


def test_ac32_the_manifest_does_not_count_those_tables() -> None:
    script = _text("backup.sh")
    manifest = script[script.index("# ---------- manifest ----------") :]

    assert '-v skip="$POSTGRES_WATERMARK_TABLE ${POSTGRES_ROWS_NOT_KEPT[*]}"' in manifest
    assert '!($2 in skipped) { print "pg." $2 "=" $3 }' in manifest


def test_ac6a_the_restore_sql_is_the_one_list_over_every_row() -> None:
    sql = _text("clear-stored-code.sql")

    assert sql == statements.restore_sql()
    for column in statements.STORED_CODE_COLUMNS:
        assert f"{column} = NULL" in sql
    for table in statements.ROWS_NOT_BACKED_UP:
        assert f"DELETE FROM {table};" in sql
    assert "withdrawn_reason = 'restored'" in sql


def test_ac6a_the_restore_empties_after_pg_restore_and_says_setup_comes_next() -> None:
    script = _text("restore-postgres.sh")
    restored = script.index("pg_restore -U")
    emptied = script.index('pg -c "$(cat "$here/clear-stored-code.sql")"')
    setup = script.index("next: run setup")

    assert restored < emptied < setup
    assert script.startswith("#!/usr/bin/env bash") and "set -euo pipefail" in script


def test_ac6a_nexovue_restore_stops_both_f5_roles_before_restoring() -> None:
    kit = (DEPLOY / "nexovue").read_text(encoding="utf-8")
    stopping = kit[kit.index("services_to_stop() {") : kit.index("# The Compose files")]
    restore = kit[kit.index("restore_snapshot() {") :]

    assert "scheduler-insight" in stopping and "scheduler-connections" in stopping
    assert restore.index("compose stop $(services_to_stop") < restore.index("restore-postgres.sh")
