"""`python -m nexovue_api.setup`, the step every deployment runs before any service starts
(docs/changes/ready-to-deploy, T2), against real Postgres and the S3 store.

It must bring an empty database to the newest migration, change nothing when run again, and —
because two installs or two replicas can start together — survive two copies started at once.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import subprocess
import sys
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import boto3
import pytest
from live import require_stack

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
S3_ENDPOINT: Final = os.environ.get("NEXOVUE_TEST_S3_ENDPOINT", "http://localhost:18333")
S3_KEY: Final = os.environ.get("NEXOVUE_TEST_S3_KEY", "nexovue_dev_access")
S3_SECRET: Final = os.environ.get("NEXOVUE_TEST_S3_SECRET", "nexovue_dev_secret")


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN) and _reachable(S3_ENDPOINT),
    suite="apps/api/tests/test_setup_live.py",
    remedy="Postgres or the S3 store is not reachable — run `just up`",
)


async def _admin(statement: str) -> None:
    connection = await asyncpg.connect(POSTGRES_DSN)
    try:
        await connection.execute(statement)
    finally:
        await connection.close()


@pytest.fixture
def scratch_dsn() -> Iterator[str]:
    name = f"nexovue_setup_{uuid.uuid4().hex[:12]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    try:
        parts = urlsplit(POSTGRES_DSN)
        yield urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
def bucket() -> Iterator[str]:
    name = f"setup-probe-{uuid.uuid4().hex[:12]}"
    yield name
    client = _s3()
    with contextlib.suppress(client.exceptions.ClientError):
        client.delete_bucket(Bucket=name)


def _s3() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_KEY,
        aws_secret_access_key=S3_SECRET,
        region_name="us-east-1",
    )


def _setup(dsn: str, bucket: str | None = None) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("S3_", "POSTGRES_DSN"))
    } | {"POSTGRES_DSN": dsn.replace("postgresql://", "postgresql+asyncpg://", 1)}
    if bucket is not None:
        environment |= {
            "S3_ENDPOINT": S3_ENDPOINT,
            "S3_BUCKET": bucket,
            "S3_ACCESS_KEY_ID": S3_KEY,
            "S3_SECRET_ACCESS_KEY": S3_SECRET,
        }
    return subprocess.run(
        [sys.executable, "-m", "nexovue_api.setup"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


async def _revision(dsn: str) -> str:
    connection = await asyncpg.connect(dsn)
    try:
        revision: str = await connection.fetchval("SELECT version_num FROM alembic_version")
        return revision
    finally:
        await connection.close()


def _head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from nexovue_api.setup import ALEMBIC_INI

    head = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()
    assert head is not None
    return head


def test_an_empty_database_reaches_the_newest_migration_and_a_rerun_changes_nothing(
    scratch_dsn: str,
) -> None:
    first = _setup(scratch_dsn)
    assert first.returncode == 0, first.stderr
    assert asyncio.run(_revision(scratch_dsn)) == _head()

    again = _setup(scratch_dsn)
    assert again.returncode == 0, again.stderr
    assert asyncio.run(_revision(scratch_dsn)) == _head()


def test_two_setups_started_together_both_succeed(scratch_dsn: str) -> None:
    """Without a lock, the second creates tables the first is creating, and fails."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _setup(scratch_dsn), range(2)))

    for result in results:
        assert result.returncode == 0, result.stderr
    assert asyncio.run(_revision(scratch_dsn)) == _head()


def test_the_bucket_is_created_once(scratch_dsn: str, bucket: str) -> None:
    for _ in range(2):
        result = _setup(scratch_dsn, bucket)
        assert result.returncode == 0, result.stderr

    _s3().head_bucket(Bucket=bucket)
