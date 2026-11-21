"""F5's backup and restore through the real scripts and Docker (plan T5.4, T5.5; spec AC6a,
AC32). **A release check, run by hand per release against real volumes, never in the gate**
(`-m release_check`; `pyproject.toml` leaves it out of every other run).

    POSTGRES_CONTAINER=… CLICKHOUSE_CONTAINER=… uv run pytest -m release_check \\
        apps/api/tests/test_fix_restore_docker.py

It writes a written suggestion holding a marker, a pack, a check run with a patch, a link
attempt and a consent into the stack's own database, takes a snapshot with `backup.sh`,
restores it with `restore-postgres.sh`, and runs `verify.sh`:

- AC32: the dump holds no row of the pack, check or attempt tables, and `verify.sh` exits 0
  with those tables empty after the restore.
- AC6a: every column on the one deletion list is empty, the marker is nowhere in the row,
  work in flight is `failed`, every consent is withdrawn, and the script says
  `nexovue setup` comes next. The `nexovue restore` half (AC6 holding with no manual step)
  is the operator's, through `nexovue restore` on an installed kit.

**It replaces the stack's database with the snapshot it takes:** run it on a stack of its
own, never a shared one. The live guard fails loudly when the containers are absent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Final

import pytest
from live import require_stack

import nexovue_api

pytestmark = pytest.mark.release_check

BACKUP: Final = Path(nexovue_api.__file__).parents[4] / "deploy" / "backup"
PROJECT: Final = os.environ.get("NEXOVUE_PROJECT", "nexovue")
POSTGRES_CONTAINER: Final = os.environ.get("POSTGRES_CONTAINER", f"{PROJECT}-postgres-1")
CLICKHOUSE_CONTAINER: Final = os.environ.get("CLICKHOUSE_CONTAINER", f"{PROJECT}-clickhouse-1")
POSTGRES_USER: Final = os.environ.get("POSTGRES_USER", "nexovue")
POSTGRES_DB: Final = os.environ.get("POSTGRES_DB", "nexovue")
DOCKER: Final = shutil.which("docker")
MARKER: Final = f"MARKER-{uuid.uuid4().hex[:8]}-release-check"
# A bounded wait for each script: a hung restore fails the check, never stalls it.
SCRIPT_TIMEOUT_SECONDS: Final = 600


def _running(container: str) -> bool:
    if DOCKER is None:
        return False
    found = subprocess.run(
        [DOCKER, "inspect", "-f", "{{.State.Running}}", container],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return found.stdout.strip() == "true"


def _require_stack() -> None:
    """Called by the test, never at collection: the gate deselects this suite, so a guard
    at module level would fail every other run. Not skipped when absent (`live`)."""
    require_stack(
        reachable=_running(POSTGRES_CONTAINER) and _running(CLICKHOUSE_CONTAINER),
        suite="apps/api/tests/test_fix_restore_docker.py",
        remedy=f"{POSTGRES_CONTAINER} and {CLICKHOUSE_CONTAINER} must be running",
    )


def _psql(sql: str) -> str:
    assert DOCKER is not None
    done = subprocess.run(
        [
            DOCKER,
            "exec",
            "-i",
            POSTGRES_CONTAINER,
            "psql",
            "-U",
            POSTGRES_USER,
            "-d",
            POSTGRES_DB,
            "-v",
            "ON_ERROR_STOP=1",
            "-tA",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return done.stdout.strip()


def _script(name: str, *arguments: str, backups: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BACKUP / name), *arguments],
        capture_output=True,
        text=True,
        check=True,
        timeout=SCRIPT_TIMEOUT_SECONDS,
        env={**os.environ, "NEXOVUE_BACKUP_DIR": str(backups)},
    )


def _seed() -> tuple[str, str]:
    """One organization's suggestion rows, by SQL against the stack's own data: the first
    finding and repository there. Returns the written and the in-flight suggestion ids."""
    org, project, finding = _psql(
        "SELECT org_id || ',' || project_id || ',' || id FROM findings ORDER BY id LIMIT 1"
    ).split(",")
    repository = _psql(
        f"SELECT id FROM connection_repositories WHERE org_id = '{org}' ORDER BY id LIMIT 1"
    )
    user = _psql(f"SELECT id FROM users WHERE org_id = '{org}' ORDER BY id LIMIT 1")
    written, flying = str(uuid.uuid4()), str(uuid.uuid4())
    _psql(
        f"""
        INSERT INTO fix_source_consents (org_id, connection_repository_id, provider_base_url,
            provider_format, model, folder_scope, require_github_access, consented_at)
        VALUES ('{org}', '{repository}', 'https://provider.example/v1', 'openai_chat',
            'model-a', 'release-check-{MARKER}', false, now());
        INSERT INTO finding_fixes (id, org_id, project_id, finding_id, kind, state,
            connection_repository_id, folder, files_read, edits, requested_by, finished_at)
        VALUES ('{written}', '{org}', '{project}', '{finding}', 'source', 'proposed',
            '{repository}', 'src', '{{"files": [{{"path": "{MARKER}"}}]}}',
            '[{{"old_text": "{MARKER}"}}]', '{user}', now());
        INSERT INTO finding_fixes (id, org_id, project_id, finding_id, kind, state,
            connection_repository_id, folder, requested_by)
        SELECT '{flying}', '{org}', '{project}', id, 'source', 'proposing', '{repository}',
            'src', '{user}' FROM findings WHERE org_id = '{org}' AND id <> '{finding}'
        ORDER BY id LIMIT 1;
        INSERT INTO finding_fix_source_packs (fix_id, org_id, files, frames, bytes, expires_at)
        SELECT '{flying}', '{org}', '[{{"content": "{MARKER}"}}]', '[]', 1,
            now() + interval '1 hour'
        WHERE EXISTS (SELECT 1 FROM finding_fixes WHERE id = '{flying}');
        INSERT INTO finding_fix_checks (org_id, fix_id, requested_by, purpose, status, patch,
            expires_at)
        VALUES ('{org}', '{written}', '{user}', 'patch', 'unchanged', '+{MARKER}',
            now() + interval '15 minutes');
        INSERT INTO github_link_attempts (org_id, user_id, session_hash, state_hash, expires_at)
        VALUES ('{org}', '{user}', md5(random()::text) || md5(random()::text),
            md5(random()::text) || md5(random()::text), now() + interval '10 minutes');
        """
    )
    return written, flying


def test_ac32_and_ac6a_a_restore_brings_back_no_code_and_every_consent_withdrawn(
    tmp_path: Path,
) -> None:
    _require_stack()
    written, flying = _seed()

    _script("backup.sh", backups=tmp_path)
    [snapshot] = [each.name for each in tmp_path.iterdir() if each.is_dir()]
    manifest = (tmp_path / snapshot / "manifest.txt").read_text()
    for table in ("finding_fix_source_packs", "finding_fix_checks", "github_link_attempts"):
        assert f"pg.{table}=" not in manifest
    restored = _script("restore-postgres.sh", snapshot, backups=tmp_path)
    _script("verify.sh", snapshot, backups=tmp_path)

    assert "next: run setup" in restored.stdout
    for table in ("finding_fix_source_packs", "finding_fix_checks", "github_link_attempts"):
        assert _psql(f"SELECT count(*) FROM {table}") == "0"
    row = _psql(f"SELECT row_to_json(f)::text FROM finding_fixes AS f WHERE id = '{written}'")
    assert MARKER not in row and '"code_deleted_reason":"restored"' in row
    if _psql(f"SELECT count(*) FROM finding_fixes WHERE id = '{flying}'") == "1":
        assert _psql(f"SELECT state FROM finding_fixes WHERE id = '{flying}'") == "failed"
    assert _psql("SELECT count(*) FROM fix_source_consents WHERE withdrawn_at IS NULL") == "0"
