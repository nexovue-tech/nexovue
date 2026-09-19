"""Migration 0032 (people) against a real Postgres, in a database of its own.

What it proves: a set-password link and its expiry are stored together or not at
all; two users can never hold the same link; the audit index the audit screen
pages over exists; and the revision downgrades and upgrades cleanly.

A scratch database, as `test_alerts_migration_live.py` explains: a downgrade
drops columns, and the development database is shared. This suite fails rather
than skips when the stack is down; see tests/helpers/live.
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
BEFORE: Final = "0031"
PEOPLE: Final = "0032"
ORG: Final = "00000000-0000-7000-8000-000000000001"


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_people_migration_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
def scratch_dsn() -> Iterator[str]:
    name = f"nexovue_migration_{uuid.uuid4().hex[:12]}"

    async def run(statement: str) -> None:
        connection = await asyncpg.connect(POSTGRES_DSN)
        try:
            await connection.execute(statement)
        finally:
            await connection.close()

    asyncio.run(run(f'CREATE DATABASE "{name}"'))
    try:
        parts = urlsplit(POSTGRES_DSN)
        yield urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))
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
async def migrated(scratch_dsn: str) -> AsyncIterator[asyncpg.Connection]:
    _alembic(scratch_dsn, "upgrade", PEOPLE)
    connection = await asyncpg.connect(scratch_dsn)
    try:
        await connection.execute(
            f"INSERT INTO organizations (id, slug, name) VALUES ('{ORG}', 'acme', 'Acme')"
        )
        yield connection
    finally:
        await connection.close()


async def _user(connection: asyncpg.Connection, email: str, **columns: object) -> None:
    names = ["id", "org_id", "email", "role", *columns]
    values = [uuid.uuid4(), uuid.UUID(ORG), email, "member", *columns.values()]
    placeholders = ", ".join(f"${index}" for index in range(1, len(values) + 1))
    await connection.execute(
        f"INSERT INTO users ({', '.join(names)}) VALUES ({placeholders})", *values
    )


async def test_a_link_is_stored_with_its_expiry_or_not_at_all(
    migrated: asyncpg.Connection,
) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await _user(migrated, "half@example.test", set_password_token_hash="a" * 64)


async def test_two_people_can_never_hold_the_same_link(migrated: asyncpg.Connection) -> None:
    link = {"set_password_token_hash": "b" * 64, "set_password_expires_at": _later()}
    await _user(migrated, "one@example.test", **link)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _user(migrated, "two@example.test", **link)


async def test_many_people_hold_no_link_at_once(migrated: asyncpg.Connection) -> None:
    await _user(migrated, "active-1@example.test")
    await _user(migrated, "active-2@example.test")


async def test_the_audit_screen_pages_over_its_index(migrated: asyncpg.Connection) -> None:
    columns = await migrated.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_audit_log_org_id_at_id'"
    )
    assert columns is not None and "(org_id, at, id)" in columns


def test_the_revision_downgrades_and_upgrades_again(scratch_dsn: str) -> None:
    _alembic(scratch_dsn, "upgrade", PEOPLE)
    _alembic(scratch_dsn, "downgrade", BEFORE)
    _alembic(scratch_dsn, "upgrade", PEOPLE)


def _later() -> object:
    from datetime import UTC, datetime, timedelta

    return datetime.now(UTC) + timedelta(days=7)
