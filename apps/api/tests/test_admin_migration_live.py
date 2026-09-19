"""Migration 0033 (admin) against a real Postgres, in a database of its own.

What it proves: every organization has a region, and only a known one; a staff
account cannot exist without its second factor (AC26); an organization records
one website payment; a plan grant ends after it starts; and the revision
downgrades and upgrades cleanly.

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
BEFORE: Final = "0032"
ADMIN: Final = "0033"
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
    suite="apps/api/tests/test_admin_migration_live.py",
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
    _alembic(scratch_dsn, "upgrade", ADMIN)
    connection = await asyncpg.connect(scratch_dsn)
    try:
        await connection.execute(
            f"INSERT INTO organizations (id, slug, name) VALUES ('{ORG}', 'acme', 'Acme')"
        )
        yield connection
    finally:
        await connection.close()


async def test_an_organization_is_in_india_unless_told_otherwise(
    migrated: asyncpg.Connection,
) -> None:
    assert (
        await migrated.fetchval(f"SELECT region FROM organizations WHERE id = '{ORG}'") == "india"
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await migrated.execute(f"UPDATE organizations SET region = 'mars' WHERE id = '{ORG}'")


async def test_no_staff_account_exists_without_its_second_factor(
    migrated: asyncpg.Connection,
) -> None:
    with pytest.raises(asyncpg.NotNullViolationError):
        await migrated.execute(
            "INSERT INTO staff_accounts (email, name, password_hash) "
            "VALUES ('raj@nexovue.test', 'Raj', 'x')"
        )


async def test_an_organization_records_one_website_payment(migrated: asyncpg.Connection) -> None:
    insert = (
        "INSERT INTO website_payments (org_id, razorpay_payment_id, amount_minor, currency) "
        f"VALUES ('{ORG}', $1, 49900, 'INR')"
    )
    await migrated.execute(insert, "pay_one")
    with pytest.raises(asyncpg.UniqueViolationError):
        await migrated.execute(insert, "pay_two")


async def test_a_plan_grant_ends_after_it_starts(migrated: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await migrated.execute(
            "INSERT INTO plan_grants (org_id, plan_slug, services, starts_at, ends_at, reason) "
            f"VALUES ('{ORG}', 'pro', 3, now(), now() - interval '1 day', 'backwards')"
        )


def test_the_revision_downgrades_and_upgrades_again(scratch_dsn: str) -> None:
    _alembic(scratch_dsn, "upgrade", ADMIN)
    _alembic(scratch_dsn, "downgrade", BEFORE)
    _alembic(scratch_dsn, "upgrade", ADMIN)
