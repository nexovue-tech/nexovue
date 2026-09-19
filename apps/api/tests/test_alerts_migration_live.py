"""Migration 0028 against a real Postgres, in a database of its own.

**Why a database of its own.** A downgrade drops tables, and the development
database is shared with every other live suite and with the running stack. This
creates a scratch database, migrates it from nothing, and drops it afterwards,
so the round trip is tested without reaching anything another suite reads.

What it proves: the backfill gives every live organization its owners channel
and every live project its default rule (FR2, FR8) and skips deleted ones; the
two database-level guarantees the design rests on hold (one owners channel per
organization; one open incident per rule and subject, BR1); and the revision
downgrades and upgrades again cleanly.

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
BEFORE_ALERTS: Final = "0027"
ALERTS: Final = "0028"
TELEMETRY_STATE: Final = "0029"


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_alerts_migration_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


def _with_database(dsn: str, database: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
def scratch_dsn() -> Iterator[str]:
    """A fresh, empty database, dropped afterwards whatever happened."""
    name = f"nexovue_migration_{uuid.uuid4().hex[:12]}"
    admin = POSTGRES_DSN

    async def run(statement: str) -> None:
        connection = await asyncpg.connect(admin)
        try:
            await connection.execute(statement)
        finally:
            await connection.close()

    asyncio.run(run(f'CREATE DATABASE "{name}"'))
    try:
        yield _with_database(POSTGRES_DSN, name)
    finally:
        asyncio.run(run(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _alembic(dsn: str, *arguments: str) -> None:
    environment = os.environ | {
        "POSTGRES_DSN": dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    }
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *arguments],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.fixture
async def migrated_to_alerts(scratch_dsn: str) -> AsyncIterator[asyncpg.Connection]:
    """Two live organizations, one deleted, and projects of each kind, then 0028."""
    _alembic(scratch_dsn, "upgrade", BEFORE_ALERTS)
    connection = await asyncpg.connect(scratch_dsn)
    try:
        await connection.execute(
            """
            INSERT INTO organizations (id, slug, name, deleted_at) VALUES
                ('00000000-0000-7000-8000-000000000001', 'live-a', 'Live A', NULL),
                ('00000000-0000-7000-8000-000000000002', 'live-b', 'Live B', NULL),
                ('00000000-0000-7000-8000-000000000003', 'gone', 'Gone', now());
            INSERT INTO projects (id, org_id, slug, name, deleted_at) VALUES
                ('00000000-0000-7000-8000-00000000000a',
                 '00000000-0000-7000-8000-000000000001', 'production', 'Production', NULL),
                ('00000000-0000-7000-8000-00000000000b',
                 '00000000-0000-7000-8000-000000000001', 'staging', 'Staging', NULL),
                ('00000000-0000-7000-8000-00000000000c',
                 '00000000-0000-7000-8000-000000000001', 'old', 'Old', now()),
                ('00000000-0000-7000-8000-00000000000d',
                 '00000000-0000-7000-8000-000000000003', 'production', 'Production', NULL);
            """
        )
        _alembic(scratch_dsn, "upgrade", ALERTS)
        yield connection
    finally:
        await connection.close()


async def test_every_live_organization_gets_one_owners_channel(
    migrated_to_alerts: asyncpg.Connection,
) -> None:
    rows = await migrated_to_alerts.fetch(
        "SELECT org_id::text, kind, name FROM notification_channels ORDER BY org_id"
    )
    assert [(row["org_id"][-1], row["kind"], row["name"]) for row in rows] == [
        ("1", "owners", "Organization owners"),
        ("2", "owners", "Organization owners"),
    ]


async def test_every_live_project_gets_the_default_rule_sending_to_its_owners(
    migrated_to_alerts: asyncpg.Connection,
) -> None:
    rows = await migrated_to_alerts.fetch(
        """
        SELECT alert_rules.project_id::text AS project_id, alert_rules.name,
               alert_rules.condition_kind, alert_rules.condition, alert_rules.is_default,
               notification_channels.kind AS channel_kind,
               notification_channels.org_id = alert_rules.org_id AS same_organization
        FROM alert_rules
        JOIN alert_rule_channels ON alert_rule_channels.rule_id = alert_rules.id
        JOIN notification_channels ON notification_channels.id = alert_rule_channels.channel_id
        ORDER BY alert_rules.project_id
        """
    )
    # Production and staging of the live organization; not its deleted project,
    # and nothing for the deleted organization.
    assert [row["project_id"][-1] for row in rows] == ["a", "b"]
    for row in rows:
        assert row["name"] == "Any check down"
        assert row["condition_kind"] == "check_state"
        assert '"down": "critical"' in row["condition"]
        assert row["is_default"] is True
        assert row["channel_kind"] == "owners"
        assert row["same_organization"] is True


async def test_an_organization_cannot_have_two_owners_channels(
    migrated_to_alerts: asyncpg.Connection,
) -> None:
    with pytest.raises(asyncpg.UniqueViolationError):
        await migrated_to_alerts.execute(
            """
            INSERT INTO notification_channels (org_id, kind, name)
            VALUES ('00000000-0000-7000-8000-000000000001', 'owners', 'Second')
            """
        )


async def test_one_breach_cannot_have_two_open_incidents(
    migrated_to_alerts: asyncpg.Connection,
) -> None:
    rule_id = await migrated_to_alerts.fetchval(
        "SELECT id FROM alert_rules WHERE project_id = '00000000-0000-7000-8000-00000000000a'"
    )
    insert = """
        INSERT INTO incidents (org_id, project_id, rule_id, rule_name, condition_kind,
                               subject_key, subject_label, severity, opened_at, resolved_at)
        VALUES ('00000000-0000-7000-8000-000000000001', '00000000-0000-7000-8000-00000000000a',
                $1, 'Any check down', 'check_state', 'check:1', 'Checkout', 'critical',
                now(), $2)
    """
    # Resolved incidents for the same subject are history, and any number may exist.
    await migrated_to_alerts.execute(insert, rule_id, None)
    await migrated_to_alerts.execute(
        "UPDATE incidents SET resolved_at = now() WHERE rule_id = $1", rule_id
    )
    await migrated_to_alerts.execute(insert, rule_id, None)

    with pytest.raises(asyncpg.UniqueViolationError):
        await migrated_to_alerts.execute(insert, rule_id, None)


async def test_the_revision_downgrades_and_upgrades_again(
    migrated_to_alerts: asyncpg.Connection, scratch_dsn: str
) -> None:
    _alembic(scratch_dsn, "downgrade", BEFORE_ALERTS)
    remaining = await migrated_to_alerts.fetchval(
        "SELECT count(*) FROM information_schema.tables WHERE table_name IN "
        "('notification_channels', 'alert_rules', 'alert_rule_channels', 'alert_evaluations', "
        "'incidents', 'notifications', 'notification_attempts')"
    )
    assert remaining == 0

    _alembic(scratch_dsn, "upgrade", ALERTS)
    channels = await migrated_to_alerts.fetchval("SELECT count(*) FROM notification_channels")
    assert channels == 2


async def test_the_telemetry_state_revision_upgrades_and_downgrades(scratch_dsn: str) -> None:
    """0029, round trip."""
    _alembic(scratch_dsn, "upgrade", TELEMETRY_STATE)
    connection = await asyncpg.connect(scratch_dsn)
    try:
        tables = {
            row["table_name"]
            for row in await connection.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_name IN "
                "('service_last_seen', 'error_groups_seen', 'error_group_watermarks')"
            )
        }
        assert tables == {"service_last_seen", "error_groups_seen", "error_group_watermarks"}
        _alembic(scratch_dsn, "downgrade", ALERTS)
        remaining = await connection.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_name IN "
            "('service_last_seen', 'error_groups_seen', 'error_group_watermarks')"
        )
        assert remaining == 0
    finally:
        await connection.close()
