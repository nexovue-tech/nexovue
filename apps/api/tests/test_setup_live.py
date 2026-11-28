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
import time
import uuid
from collections.abc import Callable, Coroutine, Iterator
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
# migrations/env.py's MIGRATION_LOCK; env.py runs Alembic when imported, so it is repeated here.
MIGRATION_LOCK: Final = 0x6E65786F76756502
SETUP_WAIT_SAMPLED_SECONDS: Final = 4.0
# How long the setup may take to start and ask for the lock, on a loaded CI runner.
SETUP_START_SECONDS: Final = 60.0
# Part of both pg_try_advisory_lock and pg_advisory_lock: the wait starts with the first statement
# calling either.
ADVISORY_LOCK_CALL: Final = "advisory_lock"
# migrations/env.py's MIGRATION_LOCK_POLL_SECONDS, for the stand-in waiters.
LOCK_POLL_SECONDS: Final = 0.5
# Long enough that a 50 ms sampler cannot miss it.
CONNECT_CHECK_HELD_SECONDS: Final = 1.0


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


def _environment(dsn: str, bucket: str | None = None) -> dict[str, str]:
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
    return environment


def _setup(dsn: str, bucket: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "nexovue_api.setup"],
        env=_environment(dsn, bucket),
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
    """Without a lock, the second creates tables the first is creating, and fails. With 0045's
    concurrent index in the run, a second that waits inside a statement deadlocks the first."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _setup(scratch_dsn), range(2)))

    for result in results:
        assert result.returncode == 0, result.stderr
    assert asyncio.run(_revision(scratch_dsn)) == _head()


async def _waiting_setup_samples(dsn: str, holder_pid: int) -> list[asyncpg.Record]:
    """pg_stat_activity of every session but the holder, sampled for SETUP_WAIT_SAMPLED_SECONDS
    from the moment one asks for the lock.

    Not from the moment the setup connects: SQLAlchemy's first connection runs a transaction of
    server checks (version, schema, settings) before the wait starts. It waits on nothing and ends
    on its own, so a concurrent index build waits for it a moment, never for the holder."""
    observer = await asyncpg.connect(dsn)
    try:
        samples: list[asyncpg.Record] = []
        give_up = time.monotonic() + SETUP_START_SECONDS
        stop_at: float | None = None
        while time.monotonic() < (give_up if stop_at is None else stop_at):
            sessions = await observer.fetch(
                "SELECT state, wait_event_type, wait_event, query FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid NOT IN ($1, pg_backend_pid())",
                holder_pid,
            )
            if stop_at is None and any(
                ADVISORY_LOCK_CALL in (session["query"] or "") for session in sessions
            ):
                stop_at = time.monotonic() + SETUP_WAIT_SAMPLED_SECONDS
            if stop_at is not None:
                samples += sessions
            await asyncio.sleep(0.05)
        return samples
    finally:
        await observer.close()


async def _hold_the_migration_lock(dsn: str) -> asyncpg.Connection:
    holder: asyncpg.Connection = await asyncpg.connect(dsn)
    await holder.execute(f"SELECT pg_advisory_lock({MIGRATION_LOCK})")
    return holder


def _blocked(samples: list[asyncpg.Record]) -> list[dict[str, Any]]:
    """Samples of a waiter that a concurrent index build would wait for while it waits."""
    return [
        {key: sample[key] for key in ("state", "wait_event_type", "wait_event", "query")}
        for sample in samples
        if sample["wait_event_type"] == "Lock" or str(sample["state"]).startswith("idle in")
    ]


async def _poll_for_the_lock(waiter: asyncpg.Connection) -> None:
    while not await waiter.fetchval(f"SELECT pg_try_advisory_lock({MIGRATION_LOCK})"):
        await asyncio.sleep(LOCK_POLL_SECONDS)


async def _check_the_server_then_poll(waiter: asyncpg.Connection) -> None:
    """SQLAlchemy's first connection: a transaction of server checks, ended before the wait."""
    async with waiter.transaction():
        await waiter.fetchval("SELECT current_schema()")
        await asyncio.sleep(CONNECT_CHECK_HELD_SECONDS)
    await _poll_for_the_lock(waiter)


async def _poll_inside_a_transaction(waiter: asyncpg.Connection) -> None:
    async with waiter.transaction():
        await _poll_for_the_lock(waiter)


async def _block_in_the_lock(waiter: asyncpg.Connection) -> None:
    await waiter.execute(f"SELECT pg_advisory_lock({MIGRATION_LOCK})")


async def _blocked_while_waiting(
    dsn: str, wait: Callable[[asyncpg.Connection], Coroutine[Any, Any, None]]
) -> list[dict[str, Any]]:
    holder = await _hold_the_migration_lock(dsn)
    waiter: asyncpg.Connection = await asyncpg.connect(dsn)
    waiting = asyncio.create_task(wait(waiter))
    try:
        samples = await _waiting_setup_samples(dsn, holder.get_server_pid())
    finally:
        waiting.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiting
        await waiter.close()
        await holder.close()
    assert samples, "the waiter was never seen waiting for the lock"
    return _blocked(samples)


def test_the_server_checks_on_connecting_are_not_counted_as_the_wait(
    scratch_dsn: str,
) -> None:
    """They end on their own, so a concurrent index build waits for them a moment, not forever."""
    assert asyncio.run(_blocked_while_waiting(scratch_dsn, _check_the_server_then_poll)) == []


def test_a_waiter_polling_inside_a_transaction_is_caught(scratch_dsn: str) -> None:
    blocked = asyncio.run(_blocked_while_waiting(scratch_dsn, _poll_inside_a_transaction))

    assert blocked
    assert {sample["state"] for sample in blocked} == {"idle in transaction"}


def test_a_waiter_blocked_in_pg_advisory_lock_is_caught(scratch_dsn: str) -> None:
    blocked = asyncio.run(_blocked_while_waiting(scratch_dsn, _block_in_the_lock))

    assert blocked
    assert {sample["wait_event_type"] for sample in blocked} == {"Lock"}


def test_a_setup_waiting_for_the_migration_lock_holds_no_transaction_or_statement(
    scratch_dsn: str,
) -> None:
    """CREATE INDEX CONCURRENTLY waits for every session holding a snapshot, so a waiter blocked
    in a statement, or idle in a transaction, would deadlock the setup it waits for."""
    loop = asyncio.new_event_loop()
    holder = loop.run_until_complete(_hold_the_migration_lock(scratch_dsn))
    setup = subprocess.Popen(
        [sys.executable, "-m", "nexovue_api.setup"],
        env=_environment(scratch_dsn),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        samples = loop.run_until_complete(
            _waiting_setup_samples(scratch_dsn, holder.get_server_pid())
        )
    finally:
        loop.run_until_complete(holder.close())
        loop.close()
    _, stderr = setup.communicate(timeout=120)

    assert samples, "the setup was never seen waiting for the lock"
    assert _blocked(samples) == []
    assert setup.returncode == 0, stderr
    assert asyncio.run(_revision(scratch_dsn)) == _head()


def test_the_bucket_is_created_once(scratch_dsn: str, bucket: str) -> None:
    for _ in range(2):
        result = _setup(scratch_dsn, bucket)
        assert result.returncode == 0, result.stderr

    _s3().head_bucket(Bucket=bucket)
