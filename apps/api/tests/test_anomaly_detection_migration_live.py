"""Migration 0041, with the keys and checks 0050 completes, against a real Postgres.

A database of its own for `test_alerts_migration_live.py`'s reason: these tests
downgrade, and the development database is shared.

What it proves: existing projects take UTC and normal sensitivity (C24); the
database refuses a second open finding for one service or one project, a closed
finding that does not say how, and a notification without exactly one subject —
the project kinds excepted (D24); `anomaly_finding` is a condition kind; 0041
refuses to downgrade, since it would drop findings and the rules about them; and
0050 validates 0041's checks, builds the outbox's keys again after a downgrade,
and recovers from a failed concurrent build.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from live import require_stack

import nexovue_api

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ALEMBIC_INI: Final = Path(nexovue_api.__file__).parents[2] / "alembic.ini"
BEFORE_DETECTION: Final = "0040"
DETECTION: Final = "0041"
# The last migration before 0050, and 0050: the outbox keys and 0041's checks validated.
BEFORE_KEYS: Final = "0049"
KEYS: Final = "0050"
FINDING_KEY: Final = "uq_notifications_finding_id_channel_id_kind_sequence"
PROJECT_KEY: Final = "uq_notifications_project_id_channel_id_kind_sequence_project"

ORG: Final = "00000000-0000-7000-8000-000000000001"
PROJECT: Final = "00000000-0000-7000-8000-00000000000a"


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_anomaly_detection_migration_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


def _with_database(dsn: str, database: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
def scratch_dsn() -> Iterator[str]:
    """A fresh, empty database, dropped afterwards whatever happened."""
    name = f"nexovue_migration_{uuid.uuid4().hex[:12]}"

    async def run(statement: str) -> None:
        connection = await asyncpg.connect(POSTGRES_DSN)
        try:
            await connection.execute(statement)
        finally:
            await connection.close()

    asyncio.run(run(f'CREATE DATABASE "{name}"'))
    try:
        yield _with_database(POSTGRES_DSN, name)
    finally:
        asyncio.run(run(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _run_alembic(dsn: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ | {
        "POSTGRES_DSN": dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *arguments],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _alembic(dsn: str, *arguments: str) -> None:
    completed = _run_alembic(dsn, *arguments)
    assert completed.returncode == 0, completed.stderr


@pytest.fixture
async def migrated(scratch_dsn: str) -> AsyncIterator[asyncpg.Connection]:
    """An organization with a project created before 0041, then 0041 to 0050."""
    _alembic(scratch_dsn, "upgrade", BEFORE_DETECTION)
    connection = await asyncpg.connect(scratch_dsn)
    try:
        await connection.execute(
            f"""
            INSERT INTO organizations (id, slug, name) VALUES ('{ORG}', 'acme', 'Acme');
            INSERT INTO projects (id, org_id, slug, name)
                VALUES ('{PROJECT}', '{ORG}', 'production', 'Production');
            INSERT INTO notification_channels (org_id, kind, name)
                VALUES ('{ORG}', 'owners', 'Organization owners');
            """
        )
        _alembic(scratch_dsn, "upgrade", KEYS)
        yield connection
    finally:
        await connection.close()


async def _finding(
    connection: asyncpg.Connection,
    *,
    scope: str = "service",
    service: str | None = "checkout",
    state: str = "open",
    closed_reason: str | None = None,
    closed: bool = False,
) -> uuid.UUID:
    finding_id: uuid.UUID = await connection.fetchval(
        f"""
        INSERT INTO findings (org_id, project_id, scope, service_name, severity, state,
                              closed_reason, title, started_at, closed_at)
        VALUES ('{ORG}', '{PROJECT}', $1, $2, 'warning', $3, $4, 'Checkout is slower',
                now() - interval '1 hour', CASE WHEN $5 THEN now() END)
        RETURNING id
        """,
        scope,
        service,
        state,
        closed_reason,
        closed,
    )
    return finding_id


async def _channel(connection: asyncpg.Connection) -> uuid.UUID:
    channel_id: uuid.UUID = await connection.fetchval(
        f"SELECT id FROM notification_channels WHERE org_id = '{ORG}' AND kind = 'owners'"
    )
    return channel_id


async def _incident(connection: asyncpg.Connection) -> uuid.UUID:
    incident_id: uuid.UUID = await connection.fetchval(
        f"""
        INSERT INTO incidents (org_id, project_id, rule_name, condition_kind, subject_key,
                               subject_label, severity, opened_at)
        VALUES ('{ORG}', '{PROJECT}', 'Slow checkout', 'service_latency_p95',
                'service:checkout', 'checkout', 'warning', now())
        RETURNING id
        """
    )
    return incident_id


async def _notify(
    connection: asyncpg.Connection,
    kind: str,
    *,
    incident_id: uuid.UUID | None = None,
    finding_id: uuid.UUID | None = None,
) -> None:
    await connection.execute(
        f"""
        INSERT INTO notifications (org_id, project_id, incident_id, finding_id, channel_id,
                                   kind, severity)
        VALUES ('{ORG}', '{PROJECT}', $1, $2, $3, $4, 'warning')
        """,
        incident_id,
        finding_id,
        await _channel(connection),
        kind,
    )


async def test_an_existing_project_is_judged_in_utc_at_normal_sensitivity(
    migrated: asyncpg.Connection,
) -> None:
    row = await migrated.fetchrow(
        f"SELECT timezone, detection_sensitivity FROM projects WHERE id = '{PROJECT}'"
    )
    assert row is not None
    assert (row["timezone"], row["detection_sensitivity"]) == ("UTC", "normal")


async def test_a_sensitivity_outside_the_vocabulary_is_refused(
    migrated: asyncpg.Connection,
) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await migrated.execute(
            f"UPDATE projects SET detection_sensitivity = 'paranoid' WHERE id = '{PROJECT}'"
        )


async def test_one_service_cannot_have_two_open_findings(migrated: asyncpg.Connection) -> None:
    # Closed findings for the same service are history, and any number may exist.
    await _finding(migrated, state="closed", closed_reason="recovered", closed=True)
    await _finding(migrated)
    await _finding(migrated, service="payments")

    with pytest.raises(asyncpg.UniqueViolationError):
        await _finding(migrated)


async def test_one_project_cannot_have_two_open_project_findings(
    migrated: asyncpg.Connection,
) -> None:
    await _finding(migrated, scope="project", service=None)

    with pytest.raises(asyncpg.UniqueViolationError):
        await _finding(migrated, scope="project", service=None)


@pytest.mark.parametrize(
    ("scope", "service", "state", "closed_reason", "closed"),
    [
        ("service", None, "open", None, False),
        ("project", "checkout", "open", None, False),
        ("service", "checkout", "closed", None, True),
        ("service", "checkout", "closed", "recovered", False),
        ("service", "checkout", "open", "recovered", False),
        ("service", "checkout", "closed", "fixed_itself", True),
    ],
    ids=[
        "a service finding names no service",
        "a project finding names a service",
        "closed without saying how",
        "closed without saying when",
        "open with a closing reason",
        "a reason outside the vocabulary",
    ],
)
async def test_a_finding_that_contradicts_itself_is_refused(
    migrated: asyncpg.Connection,
    scope: str,
    service: str | None,
    state: str,
    closed_reason: str | None,
    closed: bool,
) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await _finding(
            migrated,
            scope=scope,
            service=service,
            state=state,
            closed_reason=closed_reason,
            closed=closed,
        )


async def test_every_closed_reason_is_accepted(migrated: asyncpg.Connection) -> None:
    for reason in (
        "recovered",
        "false_alarm",
        "new_normal",
        "adopted",
        "service_gone",
        "could_not_be_judged",
    ):
        await _finding(migrated, state="closed", closed_reason=reason, closed=True)


async def test_a_notification_names_its_incident_or_its_finding(
    migrated: asyncpg.Connection,
) -> None:
    incident_id, finding_id = await _incident(migrated), await _finding(migrated)

    await _notify(migrated, "opened", incident_id=incident_id)
    await _notify(migrated, "joined", finding_id=finding_id)

    with pytest.raises(asyncpg.CheckViolationError):
        await _notify(migrated, "opened")
    with pytest.raises(asyncpg.CheckViolationError):
        await _notify(migrated, "opened", incident_id=incident_id, finding_id=finding_id)


async def test_detection_stopping_is_told_about_the_project_alone(
    migrated: asyncpg.Connection,
) -> None:
    await _notify(migrated, "stopped")
    await _notify(migrated, "running_again")

    with pytest.raises(asyncpg.CheckViolationError):
        await _notify(migrated, "stopped", finding_id=await _finding(migrated))


async def test_no_retry_tells_one_channel_twice_about_one_subject(
    migrated: asyncpg.Connection,
) -> None:
    finding_id = await _finding(migrated)
    await _notify(migrated, "opened", finding_id=finding_id)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _notify(migrated, "opened", finding_id=finding_id)

    await _notify(migrated, "stopped")
    with pytest.raises(asyncpg.UniqueViolationError):
        await _notify(migrated, "stopped")


async def test_deleting_a_finding_takes_its_links_and_notifications(
    migrated: asyncpg.Connection,
) -> None:
    incident_id, finding_id = await _incident(migrated), await _finding(migrated)
    await migrated.execute(
        f"INSERT INTO finding_incidents (org_id, finding_id, incident_id) VALUES ('{ORG}', $1, $2)",
        finding_id,
        incident_id,
    )
    await _notify(migrated, "opened", finding_id=finding_id)

    await migrated.execute("DELETE FROM findings WHERE id = $1", finding_id)

    assert await migrated.fetchval("SELECT count(*) FROM finding_incidents") == 0
    assert await migrated.fetchval("SELECT count(*) FROM notifications") == 0
    assert await migrated.fetchval("SELECT count(*) FROM incidents") == 1


async def test_a_rule_can_alert_on_anomaly_findings(migrated: asyncpg.Connection) -> None:
    await migrated.execute(
        f"""
        INSERT INTO alert_rules (org_id, project_id, name, condition_kind, condition)
        VALUES ('{ORG}', '{PROJECT}', 'Critical problems', 'anomaly_finding',
                '{{"kind": "anomaly_finding", "minimum_severity": "critical"}}')
        """
    )


async def test_every_check_0041_added_not_valid_is_validated(
    migrated: asyncpg.Connection,
) -> None:
    unvalidated = await migrated.fetchval(
        "SELECT count(*) FROM pg_constraint WHERE contype = 'c' AND NOT convalidated"
    )
    assert unvalidated == 0


async def test_0041_refuses_to_downgrade(scratch_dsn: str) -> None:
    _alembic(scratch_dsn, "upgrade", DETECTION)

    completed = _run_alembic(scratch_dsn, "downgrade", BEFORE_DETECTION)

    assert completed.returncode != 0
    assert "roll forward only" in completed.stderr
    connection = await asyncpg.connect(scratch_dsn)
    try:
        assert await connection.fetchval("SELECT to_regclass('findings') IS NOT NULL")
    finally:
        await connection.close()


async def _keys(connection: asyncpg.Connection) -> dict[str, bool]:
    """Each outbox key present, and whether its index is valid."""
    rows = await connection.fetch(
        "SELECT indexrelid::regclass::text AS name, indisvalid FROM pg_index "
        "WHERE indexrelid::regclass::text = ANY($1::text[])",
        [FINDING_KEY, PROJECT_KEY],
    )
    return {row["name"]: row["indisvalid"] for row in rows}


async def test_the_keys_downgrade_and_build_again(
    migrated: asyncpg.Connection, scratch_dsn: str
) -> None:
    _alembic(scratch_dsn, "downgrade", BEFORE_KEYS)
    assert await _keys(migrated) == {}

    _alembic(scratch_dsn, "upgrade", KEYS)
    assert await _keys(migrated) == {FINDING_KEY: True, PROJECT_KEY: True}
    await test_no_retry_tells_one_channel_twice_about_one_subject(migrated)


async def test_a_failed_concurrent_build_is_built_again(
    migrated: asyncpg.Connection, scratch_dsn: str
) -> None:
    _alembic(scratch_dsn, "downgrade", BEFORE_KEYS)
    finding_id = await _finding(migrated)
    await _notify(migrated, "opened", finding_id=finding_id)
    await _notify(migrated, "opened", finding_id=finding_id)
    # A concurrent build that fails leaves its index behind, invalid.
    with pytest.raises(asyncpg.UniqueViolationError):
        await migrated.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {FINDING_KEY} "
            "ON notifications (finding_id, channel_id, kind, sequence)"
        )
    assert await _keys(migrated) == {FINDING_KEY: False}
    await migrated.execute(
        "DELETE FROM notifications WHERE ctid = (SELECT max(ctid) FROM notifications)"
    )

    _alembic(scratch_dsn, "upgrade", KEYS)

    assert await _keys(migrated) == {FINDING_KEY: True, PROJECT_KEY: True}
    assert await migrated.fetchval(
        "SELECT count(*) FROM pg_constraint WHERE conname = $1", FINDING_KEY
    )
