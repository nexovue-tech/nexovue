"""`deploy/backup/backup.sh`'s manifest against the dump it describes.

docs/changes/backup-manifest-counts. The manifest is what `verify.sh` holds a restore to, so a
count in it has to be the number of rows the dump holds, not the number the live table held a
moment later. The insight-fence run 44912704513 recorded `pg.jobs=2` beside a dump holding one
job, because the scheduler, still running, queued one between the dump and the count; the restore
then "failed" with nothing lost.

A pass-through `docker` on PATH writes one row the instant `pg_dump` returns, which is that race
made certain. The dump is restored into a scratch database and counted there, so the oracle shares
no code with the script. The ClickHouse half backs up a near-empty database of the test's own: the
suite runs beside others that create and drop tables in `nexovue`, and a table gone between the
script listing it and counting it would fail this test for a reason it is not about.

Needs the dev stack's Postgres and ClickHouse (or the containers `POSTGRES_CONTAINER` and
`CLICKHOUSE_CONTAINER` name). This suite fails rather than skips when they are down; see
tests/helpers/live.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
from live import require_stack

import nexovue_api

BACKUP_DIR: Final = Path(nexovue_api.__file__).parents[4] / "deploy" / "backup"
BACKUP_SCRIPT: Final = BACKUP_DIR / "backup.sh"
PROJECT: Final = os.environ.get("NEXOVUE_PROJECT", "nexovue")
POSTGRES_CONTAINER: Final = os.environ.get("POSTGRES_CONTAINER", f"{PROJECT}-postgres-1")
CLICKHOUSE_CONTAINER: Final = os.environ.get("CLICKHOUSE_CONTAINER", f"{PROJECT}-clickhouse-1")
POSTGRES_USER: Final = os.environ.get("POSTGRES_USER", "nexovue")
POSTGRES_DB: Final = os.environ.get("POSTGRES_DB", "nexovue")
CLICKHOUSE_USER: Final = os.environ.get("CLICKHOUSE_USER", "nexovue")
CLICKHOUSE_PASSWORD: Final = os.environ.get("CLICKHOUSE_PASSWORD", "nexovue_dev")
# What backup.sh reads from ClickHouse by name besides the tables it lists: the schema version and
# the four watermark tables (deploy/backup/common.sh, KEEPERMAP_TABLES).
CLICKHOUSE_TABLES_READ_BY_NAME: Final = (
    "schema_migrations",
    "span_ingest_offsets",
    "span_written_offsets",
    "ingest_written_offsets",
    "span_derived_progress",
)
# Recorded on its own manifest line, never with the row counts (backup.sh).
WATERMARK_TABLE: Final = "usage_ingest_offsets"
REAL_DOCKER: Final = shutil.which("docker")


def _running(container: str) -> bool:
    if REAL_DOCKER is None:
        return False
    inspected = subprocess.run(
        [REAL_DOCKER, "inspect", "-f", "{{.State.Running}}", container],
        capture_output=True,
        text=True,
        check=False,
    )
    return inspected.stdout.strip() == "true"


require_stack(
    reachable=_running(POSTGRES_CONTAINER) and _running(CLICKHOUSE_CONTAINER),
    suite="apps/api/tests/test_backup_manifest_live.py",
    remedy=f"{POSTGRES_CONTAINER} and {CLICKHOUSE_CONTAINER} must be running — run `just up`",
)


def _psql(sql: str, database: str = POSTGRES_DB) -> str:
    assert REAL_DOCKER is not None
    result = subprocess.run(
        [
            *(REAL_DOCKER, "exec", POSTGRES_CONTAINER, "psql", "-U", POSTGRES_USER),
            *("-d", database, "-v", "ON_ERROR_STOP=1", "-tAc", sql),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# Runs the real command, then, once `pg_dump` has returned, writes a row the dump cannot hold.
WRITES_AFTER_THE_DUMP: Final = r"""#!/usr/bin/env bash
"$REAL_DOCKER" "$@"
status=$?
case " $* " in
  *" pg_dump "*)
    "$REAL_DOCKER" exec "$PROBE_CONTAINER" psql -U "$PROBE_USER" -d "$PROBE_DB" -qc \
        "INSERT INTO $PROBE_TABLE VALUES ('written after the dump')" >&2 ;;
esac
exit $status
"""


def _clickhouse(sql: str) -> None:
    assert REAL_DOCKER is not None
    subprocess.run(
        [
            *(REAL_DOCKER, "exec", CLICKHOUSE_CONTAINER, "clickhouse-client"),
            *("--user", CLICKHOUSE_USER, "--password", CLICKHOUSE_PASSWORD, "--query", sql),
        ],
        capture_output=True,
        check=True,
    )


@pytest.fixture
def clickhouse_database() -> Iterator[str]:
    name = f"backup_manifest_check_{uuid.uuid4().hex[:12]}"
    _clickhouse(f"CREATE DATABASE {name}")
    try:
        for table in CLICKHOUSE_TABLES_READ_BY_NAME:
            _clickhouse(f"CREATE TABLE {name}.{table} (version UInt64) ENGINE = Memory")
        yield name
    finally:
        _clickhouse(f"DROP DATABASE IF EXISTS {name} SYNC")


@pytest.fixture
def probe_table() -> Iterator[str]:
    """A table of the test's own, holding one row before the backup starts."""
    name = f"backup_manifest_probe_{uuid.uuid4().hex[:12]}"
    _psql(f"CREATE TABLE {name} (note text NOT NULL)")
    try:
        _psql(f"INSERT INTO {name} VALUES ('written before the dump')")
        yield name
    finally:
        _psql(f"DROP TABLE IF EXISTS {name}")


@pytest.fixture
def scratch_database() -> Iterator[str]:
    name = f"backup_manifest_check_{uuid.uuid4().hex[:12]}"
    _psql(f"CREATE DATABASE {name}", database="postgres")
    try:
        yield name
    finally:
        _psql(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)", database="postgres")


def _backup_with_a_write_after_the_dump(
    tmp_path: Path, probe_table: str, clickhouse_database: str
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(WRITES_AFTER_THE_DUMP)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    backups = tmp_path / "backups"
    result = subprocess.run(
        [str(BACKUP_SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "REAL_DOCKER": str(REAL_DOCKER),
            "PROBE_CONTAINER": POSTGRES_CONTAINER,
            "PROBE_USER": POSTGRES_USER,
            "PROBE_DB": POSTGRES_DB,
            "PROBE_TABLE": probe_table,
            "POSTGRES_CONTAINER": POSTGRES_CONTAINER,
            "CLICKHOUSE_CONTAINER": CLICKHOUSE_CONTAINER,
            "CLICKHOUSE_DB": clickhouse_database,
            "NEXOVUE_BACKUP_DIR": str(backups),
        },
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr
    (snapshot,) = backups.iterdir()
    return snapshot


def _manifest(snapshot: Path) -> dict[str, str]:
    lines = (snapshot / "manifest.txt").read_text().splitlines()
    pairs = (line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
    return dict(pairs)


def _rows_not_kept() -> frozenset[str]:
    """The tables backup.sh dumps empty and leaves out of the manifest, read from the list it
    reads (`POSTGRES_ROWS_NOT_KEPT` in common.sh), so this test never keeps a copy of it."""
    listed = subprocess.run(
        [
            *("bash", "-c", 'source "$1" && printf \'%s\\n\' "${POSTGRES_ROWS_NOT_KEPT[@]}"'),
            *("common.sh", str(BACKUP_DIR / "common.sh")),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    tables = frozenset(listed.stdout.split())
    assert tables, "common.sh names the tables a backup never holds"
    return tables


def _restored_counts(snapshot: Path, database: str) -> dict[str, str]:
    """Every public table's row count once the dump is restored into `database`."""
    assert REAL_DOCKER is not None
    with (snapshot / "postgres.dump").open("rb") as dump:
        subprocess.run(
            [
                *(REAL_DOCKER, "exec", "-i", POSTGRES_CONTAINER, "pg_restore", "-U", POSTGRES_USER),
                *("-d", database, "--no-owner", "--no-privileges", "--single-transaction"),
            ],
            stdin=dump,
            capture_output=True,
            check=True,
        )
    tables = _psql(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename", database
    ).splitlines()
    return {table: _psql(f'SELECT count(*) FROM "{table}"', database) for table in tables}


def test_each_postgres_count_is_the_rows_the_dump_holds(
    tmp_path: Path, probe_table: str, scratch_database: str, clickhouse_database: str
) -> None:
    snapshot = _backup_with_a_write_after_the_dump(tmp_path, probe_table, clickhouse_database)

    manifest = _manifest(snapshot)
    restored = _restored_counts(snapshot, scratch_database)

    assert _psql(f"SELECT count(*) FROM {probe_table}") == "2", "the write after the dump landed"
    assert restored[probe_table] == "1", "the dump holds the row written before it, only"
    assert manifest[f"pg.{probe_table}"] == "1", "counted from the dump, not from the live table"
    not_kept = _rows_not_kept()
    assert {table: restored[table] for table in not_kept} == dict.fromkeys(not_kept, "0"), (
        "dumped empty: a backup never holds a customer's code or a sealed code (AC32)"
    )
    recorded = {
        key[len("pg.") :]: value for key, value in manifest.items() if key.startswith("pg.")
    }
    assert recorded == {
        table: count
        for table, count in restored.items()
        if table != WATERMARK_TABLE and table not in not_kept
    }
    assert manifest[f"watermark.pg.{WATERMARK_TABLE}"] == restored[WATERMARK_TABLE]
    assert manifest["alembic_revision"] == _psql(
        "SELECT version_num FROM alembic_version", scratch_database
    )
