"""Migration 0035 (the super admin) against a real Postgres, in a database of its own.

What it proves: the platform role exists on the enum and a user can hold it;
`staff_accounts` is gone; the two columns that named a staff account now name a
user, and keep naming one when that user is removed; and the revision downgrades
and upgrades again — which for this one is not symmetric, because Postgres
cannot remove an enum value, and the migration says so.

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
BEFORE: Final = "0034"
SUPER_ADMIN: Final = "0035"
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
    suite="apps/api/tests/test_super_admin_migration_live.py",
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
    _alembic(scratch_dsn, "upgrade", SUPER_ADMIN)
    connection = await asyncpg.connect(scratch_dsn)
    try:
        await connection.execute(
            f"INSERT INTO organizations (id, slug, name) VALUES ('{ORG}', 'nexovue', 'nexovue')"
        )
        yield connection
    finally:
        await connection.close()


async def _user(connection: asyncpg.Connection, *, role: str) -> str:
    created: str = await connection.fetchval(
        "INSERT INTO users (org_id, email, password_hash, role) "
        f"VALUES ('{ORG}', $1, 'x', $2) RETURNING id::text",
        f"{role}-{uuid.uuid4().hex[:8]}@nexovue.com",
        role,
    )
    return created


async def test_a_user_can_hold_the_platform_role(migrated: asyncpg.Connection) -> None:
    operator = await _user(migrated, role="super_admin")

    assert (
        await migrated.fetchval("SELECT role::text FROM users WHERE id = $1::uuid", operator)
        == "super_admin"
    )


async def test_a_role_nobody_defined_is_still_refused(migrated: asyncpg.Connection) -> None:
    """The enum gained one value, not the freedom to hold any string."""
    with pytest.raises(asyncpg.InvalidTextRepresentationError):
        await _user(migrated, role="root")


async def test_the_staff_table_is_gone(migrated: asyncpg.Connection) -> None:
    assert await migrated.fetchval("SELECT to_regclass('public.staff_accounts')") is None


async def test_what_named_a_staff_account_now_names_a_user(migrated: asyncpg.Connection) -> None:
    operator = await _user(migrated, role="super_admin")

    await migrated.execute(
        "INSERT INTO website_payments (org_id, razorpay_payment_id, amount_minor, currency, "
        f"recorded_by) VALUES ('{ORG}', 'pay_one', 49900, 'INR', $1::uuid)",
        operator,
    )
    await migrated.execute(
        "INSERT INTO plan_grants (org_id, plan_slug, services, starts_at, ends_at, reason, "
        f"granted_by) VALUES ('{ORG}', 'pro', 3, now(), now() + interval '30 days', 'comped', "
        "$1::uuid)",
        operator,
    )

    recorded = await migrated.fetchval(
        "SELECT recorded_by::text FROM website_payments WHERE razorpay_payment_id = 'pay_one'"
    )
    assert recorded == operator


async def test_the_record_outlives_the_person_who_made_it(migrated: asyncpg.Connection) -> None:
    """A payment and a comp are history; the person who entered them is a label."""
    operator = await _user(migrated, role="super_admin")
    await migrated.execute(
        "INSERT INTO website_payments (org_id, razorpay_payment_id, amount_minor, currency, "
        f"recorded_by) VALUES ('{ORG}', 'pay_two', 49900, 'INR', $1::uuid)",
        operator,
    )

    await migrated.execute("DELETE FROM users WHERE id = $1::uuid", operator)

    row = await migrated.fetchrow(
        "SELECT recorded_by FROM website_payments WHERE razorpay_payment_id = 'pay_two'"
    )
    assert row is not None
    assert row["recorded_by"] is None


def test_the_revision_downgrades_and_upgrades_again(scratch_dsn: str) -> None:
    """Not symmetric, and the migration says why: the enum keeps `super_admin`
    because Postgres has no way to remove a value. Coming back up must therefore
    not fail on a value that is already there."""
    _alembic(scratch_dsn, "upgrade", SUPER_ADMIN)
    _alembic(scratch_dsn, "downgrade", BEFORE)
    _alembic(scratch_dsn, "upgrade", SUPER_ADMIN)
