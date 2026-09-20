"""Migration 0034 (API tokens) against a real Postgres, in a database of its own.

What it proves: a token's secret and its prefix are each unique, so two tokens
can never collide on the value the lookup indexes; a token ends after it is
made; a role the code does not know is refused; a token survives the person who
made it; its daily counter dies with it; and the revision downgrades and
upgrades cleanly.

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
BEFORE: Final = "0033"
TOKENS: Final = "0034"
ORG: Final = "00000000-0000-7000-8000-000000000001"
MAKER: Final = "00000000-0000-7000-8000-00000000000a"


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_api_tokens_migration_live.py",
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
    _alembic(scratch_dsn, "upgrade", TOKENS)
    connection = await asyncpg.connect(scratch_dsn)
    try:
        await connection.execute(
            f"INSERT INTO organizations (id, slug, name) VALUES ('{ORG}', 'acme', 'Acme')"
        )
        await connection.execute(
            "INSERT INTO users (id, org_id, email, password_hash, role) "
            f"VALUES ('{MAKER}', '{ORG}', 'raj@acme.test', 'x', 'owner')"
        )
        yield connection
    finally:
        await connection.close()


async def _token(connection: asyncpg.Connection, *, prefix: str, digest: str) -> uuid.UUID:
    made: uuid.UUID = await connection.fetchval(
        "INSERT INTO api_tokens (org_id, name, token_prefix, token_hash, created_by, expires_at) "
        f"VALUES ('{ORG}', 'CI', $1, $2, '{MAKER}', now() + interval '90 days') RETURNING id",
        prefix,
        digest,
    )
    return made


async def test_two_tokens_cannot_share_a_secret_or_a_prefix(migrated: asyncpg.Connection) -> None:
    await _token(migrated, prefix="nvt_live_aaaa", digest="a" * 64)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _token(migrated, prefix="nvt_live_bbbb", digest="a" * 64)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _token(migrated, prefix="nvt_live_aaaa", digest="b" * 64)


async def test_a_token_ends_after_it_is_made(migrated: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await migrated.execute(
            "INSERT INTO api_tokens "
            "(org_id, name, token_prefix, token_hash, expires_at) "
            f"VALUES ('{ORG}', 'Backwards', 'nvt_live_back', '{'c' * 64}', "
            "now() - interval '1 day')"
        )


async def test_a_role_the_code_does_not_know_is_refused(migrated: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await migrated.execute(
            "INSERT INTO api_tokens "
            "(org_id, name, role, token_prefix, token_hash, expires_at) "
            f"VALUES ('{ORG}', 'Writer', 'write', 'nvt_live_writ', '{'d' * 64}', "
            "now() + interval '1 day')"
        )


async def test_a_token_outlives_the_person_who_made_it(migrated: asyncpg.Connection) -> None:
    """AC17: CI does not stop when whoever set it up leaves."""
    token_id = await _token(migrated, prefix="nvt_live_keep", digest="e" * 64)

    await migrated.execute(f"DELETE FROM users WHERE id = '{MAKER}'")

    row = await migrated.fetchrow("SELECT created_by FROM api_tokens WHERE id = $1", token_id)
    assert row is not None
    assert row["created_by"] is None


async def test_a_tokens_daily_counters_die_with_it(migrated: asyncpg.Connection) -> None:
    token_id = await _token(migrated, prefix="nvt_live_cnts", digest="f" * 64)
    await migrated.execute(
        "INSERT INTO api_token_usage_daily (token_id, day, requests) VALUES ($1, current_date, 7)",
        token_id,
    )

    await migrated.execute("DELETE FROM api_tokens WHERE id = $1", token_id)

    assert await migrated.fetchval("SELECT count(*) FROM api_token_usage_daily") == 0


def test_the_revision_downgrades_and_upgrades_again(scratch_dsn: str) -> None:
    _alembic(scratch_dsn, "upgrade", TOKENS)
    _alembic(scratch_dsn, "downgrade", BEFORE)
    _alembic(scratch_dsn, "upgrade", TOKENS)
