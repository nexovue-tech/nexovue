"""A database of a suite's own on the dev stack's Postgres server, prepared by the real `setup`.

A suite that runs the scheduler's real worker processes cannot share the stack's database: the
stack's own workers claim every due job on the same queues, whichever organization it is for,
and a job one of them takes runs with the stack's settings, not the suite's (a different
connection key, the real GitHub). `jobs_held` cannot help, because the suite's own workers must
claim the jobs. In a private database no stack worker polls the `jobs` table, so the suite's
workers alone take the jobs its routes and workers add.

The database is created empty, brought to the newest migration by `python -m nexovue_api.setup`
as a deployment's is, and dropped afterwards with every connection to it.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import asyncpg

# A bound well past a migration from empty (about 4 s measured on the dev stack, 2026-11-22).
SETUP_TIMEOUT_SECONDS: Final = 300


async def _admin(server_dsn: str, statement: str) -> None:
    connection = await asyncpg.connect(server_dsn)
    try:
        await connection.execute(statement)
    finally:
        await connection.close()


def _named(dsn: str, database: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@contextmanager
def private_database(
    server_dsn: str, *, prefix: str, environment: Mapping[str, str] | None = None
) -> Iterator[str]:
    """A new database on `server_dsn`'s server, migrated by `setup` run with `environment`
    added (an account password, say), as a `postgresql+asyncpg://` DSN; dropped on exit.

    `server_dsn` is a plain `postgresql://` DSN whose account may create databases."""
    name = f"{prefix}_{uuid.uuid4().hex[:12]}"
    asyncio.run(_admin(server_dsn, f'CREATE DATABASE "{name}"'))
    try:
        dsn = _named(server_dsn, name).replace("postgresql://", "postgresql+asyncpg://", 1)
        prepared = subprocess.run(
            [sys.executable, "-m", "nexovue_api.setup"],
            env={
                **{k: v for k, v in os.environ.items() if not k.startswith(("POSTGRES", "S3_"))},
                **(environment or {}),
                "POSTGRES_DSN": dsn,
            },
            capture_output=True,
            text=True,
            timeout=SETUP_TIMEOUT_SECONDS,
            check=False,
        )
        assert prepared.returncode == 0, f"setup failed:\n{prepared.stdout}\n{prepared.stderr}"
        yield dsn
    finally:
        asyncio.run(_admin(server_dsn, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
