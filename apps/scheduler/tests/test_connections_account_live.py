"""The tracker account reaches the queue only through migration 0085's functions, and they do
what the shared statements do (F22 plan T8.7, by F3's AC44).

Every case writes its rows inside a transaction that is rolled back, so no other session sees
them, and the account is taken with `SET LOCAL ROLE` inside it: safe beside a running
`scheduler-connections`, which claims from the same queues.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from connections_login import CONNECTIONS_ACCOUNT, DEV_CONNECTIONS_PASSWORD
from live import require_stack
from nexovue_platform.connections import (
    CONNECTION_TEST_RESULT_KIND,
    CONNECTIONS_INTERACTIVE_QUEUE,
    CONNECTIONS_QUEUE,
)
from nexovue_platform.evidence import INSIGHT_QUEUE
from nexovue_platform.jobs import ENQUEUE_UNLESS_WAITING, JobRequest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from nexovue_api import connections_account
from nexovue_scheduler.jobs import queue, results
from nexovue_scheduler.jobs.queue import LEASE_GRACE_SECONDS, QueueStatements

DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
MIGRATION: Final = (
    Path(__file__).resolve().parents[2]
    / "api"
    / "migrations"
    / "versions"
    / "0085_connections_account.py"
)
HARD_LIMIT_SECONDS: Final = 90
_LONG_AGO: Final = "now() - interval '10 years'"
TRACKER_QUEUES: Final = [CONNECTIONS_QUEUE, CONNECTIONS_INTERACTIVE_QUEUE]


def _reachable() -> bool:
    parts = urlsplit(DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="the connections account suite",
    remedy="start the dev stack so Postgres answers (`just dev`)",
)


@pytest.fixture
async def install(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """The install's own account, after the real `setup` step's tracker part."""
    monkeypatch.setenv(connections_account.PASSWORD_VARIABLE, DEV_CONNECTIONS_PASSWORD)
    said: list[str] = []
    assert await connections_account.provision(ASYNC_DSN, say=said.append), said
    built = create_async_engine(ASYNC_DSN)
    yield built
    await built.dispose()


_INSERT_JOB: Final = text(
    """
    INSERT INTO jobs (queue, task, kwargs, run_after, lease_until, leased_by, expires_at,
                      attempts, max_attempts, soft_limit_seconds, hard_limit_seconds)
    VALUES (:queue, 'connections-account-test.noop', '{}'::jsonb,
            now() - interval '10 years' + make_interval(secs => CAST(:order AS integer)),
            CASE WHEN CAST(:lease AS integer) IS NULL THEN NULL
                 ELSE now() + make_interval(secs => CAST(:lease AS integer)) END,
            :leased_by,
            CASE WHEN CAST(:expires AS integer) IS NULL THEN NULL
                 ELSE now() + make_interval(secs => CAST(:expires AS integer)) END,
            :attempts, :max_attempts, 60, 90)
    RETURNING id
    """
)
_OBSERVE: Final = text(
    """
    SELECT id, attempts, leased_by,
           round(extract(epoch FROM lease_until - now()))::int AS lease_left,
           round(extract(epoch FROM run_after - now()))::int AS due_in, last_error
    FROM jobs WHERE id = ANY(:ids)
    """
)

# label: (queue, order, lease seconds, leased by, expires in, attempts, max attempts)
Rows = dict[str, tuple[str, int, int | None, str | None, int | None, int, int]]
DUE_AND_NOT: Final[Rows] = {
    "due": (CONNECTIONS_QUEUE, 1, None, None, None, 0, 3),
    "interactive": (CONNECTIONS_INTERACTIVE_QUEUE, 2, None, None, None, 0, 3),
    "leased": (CONNECTIONS_QUEUE, 3, 300, "other", None, 1, 3),
    "lapsed": (CONNECTIONS_QUEUE, 4, -10, "dead", None, 1, 3),
    "expired": (CONNECTIONS_QUEUE, 5, None, None, -1, 0, 3),
    "spent": (CONNECTIONS_QUEUE, 6, None, None, None, 1, 1),
    "an_alert_s": ("deliveries", 0, None, None, None, 0, 3),
    "an_ai_job": (INSIGHT_QUEUE, 0, None, None, None, 0, 3),
}
HELD: Final[Rows] = {"held": (CONNECTIONS_QUEUE, 1, 300, "w", None, 1, 3)}
HELD_ELSEWHERE: Final[Rows] = {"held": ("deliveries", 1, 300, "w", None, 1, 3)}

Act = Callable[[AsyncConnection, QueueStatements, dict[str, uuid.UUID]], Awaitable[Any]]


async def _write(connection: AsyncConnection, rows: Rows) -> dict[str, uuid.UUID]:
    ids = {}
    for label, (name, order, lease, leased_by, expires, attempts, most) in rows.items():
        ids[label] = (
            await connection.execute(
                _INSERT_JOB,
                {
                    "queue": name,
                    "order": order,
                    "lease": lease,
                    "leased_by": leased_by,
                    "expires": expires,
                    "attempts": attempts,
                    "max_attempts": most,
                },
            )
        ).scalar_one()
    return ids


async def _observe(connection: AsyncConnection, ids: dict[str, uuid.UUID]) -> dict[str, Any]:
    labels = {job: label for label, job in ids.items()}
    seen = {
        labels[row.id]: (row.attempts, row.leased_by, row.lease_left, row.due_in, row.last_error)
        for row in await connection.execute(_OBSERVE, {"ids": list(ids.values())})
    }
    return {label: seen.get(label, "gone") for label in ids}


async def _run(
    engine: AsyncEngine, rows: Rows, act: Act, *, as_account: bool
) -> tuple[Any, dict[str, Any]]:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            ids = await _write(connection, rows)
            statements = queue.SHARED_STATEMENTS
            if as_account:
                await connection.execute(text(f"SET LOCAL ROLE {CONNECTIONS_ACCOUNT}"))
                statements = queue.CONNECTIONS_STATEMENTS
            answer = await act(connection, statements, ids)
            await connection.execute(text("RESET ROLE"))
            return answer, await _observe(connection, ids)
        finally:
            await transaction.rollback()


def _claim(queues: list[str], limit: int) -> Act:
    async def act(
        connection: AsyncConnection, statements: QueueStatements, ids: dict[str, uuid.UUID]
    ) -> list[str]:
        rows = await connection.execute(
            statements.claim,
            statements.claim_parameters(queues=queues, worker="w", limit=limit),
        )
        claimed = {row.id for row in rows}
        return sorted(label for label, job in ids.items() if job in claimed)

    return act


def _retry(worker: str) -> Act:
    async def act(
        connection: AsyncConnection, statements: QueueStatements, ids: dict[str, uuid.UUID]
    ) -> None:
        await connection.execute(
            statements.retry,
            {"id": ids["held"], "worker": worker, "countdown": 60.0, "error": "boom"},
        )

    return act


def _finish(worker: str) -> Act:
    async def act(
        connection: AsyncConnection, statements: QueueStatements, ids: dict[str, uuid.UUID]
    ) -> None:
        await connection.execute(statements.finish, {"id": ids["held"], "worker": worker})

    return act


async def test_a_claim_takes_the_tracker_queues_due_jobs_alike(install: AsyncEngine) -> None:
    """Leases, attempts, order and expiry, as `_CLAIM`; asked for its own queues only."""
    act = _claim(TRACKER_QUEUES, limit=10)

    shared = await _run(install, DUE_AND_NOT, act, as_account=False)
    through = await _run(install, DUE_AND_NOT, act, as_account=True)

    assert through == shared
    assert through[0] == ["due", "interactive", "lapsed"]
    assert through[1]["due"][:3] == (1, "w", HARD_LIMIT_SECONDS + LEASE_GRACE_SECONDS)


async def test_a_claim_never_reaches_another_role_s_queue(install: AsyncEngine) -> None:
    """Asked for an alert's and an AI job's queue too, the function still matches neither."""
    answer, observed = await _run(
        install,
        DUE_AND_NOT,
        _claim([*TRACKER_QUEUES, "deliveries", INSIGHT_QUEUE], limit=10),
        as_account=True,
    )

    assert answer == ["due", "interactive", "lapsed"]
    assert observed["an_alert_s"][1] is None and observed["an_ai_job"][1] is None


@pytest.mark.parametrize(
    ("label", "act"),
    [
        ("its holder hands a job back", _retry("w")),
        ("a stranger cannot hand it back", _retry("x")),
        ("its holder finishes a job", _finish("w")),
        ("a stranger cannot finish it", _finish("x")),
    ],
)
async def test_retry_and_finish_are_fenced_by_the_lease_alike(
    install: AsyncEngine, label: str, act: Act
) -> None:
    shared = await _run(install, HELD, act, as_account=False)
    through = await _run(install, HELD, act, as_account=True)

    assert through == shared, label


@pytest.mark.parametrize("act", [_retry("w"), _finish("w")])
async def test_retry_and_finish_leave_another_queue_s_job_alone(
    install: AsyncEngine, act: Act
) -> None:
    _, observed = await _run(install, HELD_ELSEWHERE, act, as_account=True)

    assert observed["held"] == (1, "w", 300, observed["held"][3], None)


def _request(org_id: str, queue_name: str = CONNECTIONS_QUEUE) -> JobRequest:
    return JobRequest(
        task="connections-account-test.noop",
        queue=queue_name,
        kwargs={"n": 1},
        org_id=org_id,
        expires_seconds=600,
    )


_NEW_ORGANIZATION: Final = text(
    "INSERT INTO organizations (slug, name) "
    "VALUES ('t87-' || substr(md5(random()::text), 1, 12), 't8.7') RETURNING id"
)


async def _enqueue_twice(engine: AsyncEngine, queue_name: str, *, as_account: bool) -> list[Any]:
    """Two adds of one job for one organization, in a transaction that is rolled back."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            org = (await connection.execute(_NEW_ORGANIZATION)).scalar_one()
            request = _request(str(org), queue_name)
            if as_account:
                await connection.execute(text(f"SET LOCAL ROLE {CONNECTIONS_ACCOUNT}"))
                statement = queue.CONNECTIONS_ENQUEUE
            else:
                statement = text(ENQUEUE_UNLESS_WAITING)
            for _ in range(2):
                await connection.execute(statement, request.parameters())
            await connection.execute(text("RESET ROLE"))
            rows = await connection.execute(
                text(
                    "SELECT queue, task, kwargs, org_id, attempts, max_attempts, "
                    "soft_limit_seconds, hard_limit_seconds, "
                    "round(extract(epoch FROM expires_at - now()))::int AS expires_in, "
                    "round(extract(epoch FROM run_after - now()))::int AS due_in "
                    "FROM jobs WHERE task = :task AND kwargs = CAST(:kwargs AS jsonb)"
                ),
                {"task": request.task, "kwargs": json.dumps(request.kwargs)},
            )
            return [tuple(row)[:3] + tuple(row)[4:] for row in rows]
        finally:
            await transaction.rollback()


async def test_an_enqueue_adds_one_job_unless_one_waits_alike(install: AsyncEngine) -> None:
    shared = await _enqueue_twice(install, CONNECTIONS_QUEUE, as_account=False)
    through = await _enqueue_twice(install, CONNECTIONS_QUEUE, as_account=True)

    assert through == shared
    assert len(through) == 1


async def test_an_enqueue_onto_another_role_s_queue_is_refused(install: AsyncEngine) -> None:
    """Raised, not dropped: the account adds work only for itself."""
    for name in ("deliveries", INSIGHT_QUEUE, "light"):
        with pytest.raises(DBAPIError, match="is not a tracker queue"):
            await _enqueue_twice(install, name, as_account=True)


async def test_a_connection_test_result_is_stored_as_its_kind_and_the_first_stands(
    install: AsyncEngine,
) -> None:
    async with install.connect() as connection:
        transaction = await connection.begin()
        try:
            org = (
                await connection.execute(
                    text(
                        "INSERT INTO organizations (slug, name) "
                        "VALUES ('t87-' || substr(md5(random()::text), 1, 12), 't8.7') RETURNING id"
                    )
                )
            ).scalar_one()
            request_id = str(uuid.uuid4())
            await connection.execute(text(f"SET LOCAL ROLE {CONNECTIONS_ACCOUNT}"))
            for answer in ({"ok": True}, {"ok": False}):
                await connection.execute(
                    results.STORE_CONNECTION_TEST_RESULT,
                    {
                        "request_id": request_id,
                        "org_id": str(org),
                        "payload": json.dumps(answer),
                        "ttl_seconds": 60.0,
                    },
                )
            await connection.execute(text("RESET ROLE"))
            stored = (
                await connection.execute(
                    text("SELECT kind, payload FROM job_results WHERE request_id = :id"),
                    {"id": request_id},
                )
            ).one()
        finally:
            await transaction.rollback()

    assert stored.kind == CONNECTION_TEST_RESULT_KIND
    assert stored.payload == {"ok": True}


def test_the_migration_s_frozen_names_are_the_code_s() -> None:
    spec = importlib.util.spec_from_file_location("revision_0085", MIGRATION)
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    assert ", ".join(f"'{name}'" for name in TRACKER_QUEUES) == revision._QUEUES
    assert revision._ACCOUNT == CONNECTIONS_ACCOUNT
    assert tuple(revision._SIGNATURES) == connections_account.CONNECTIONS_FUNCTIONS
    assert queue.CONNECTIONS_STATEMENTS.queues == frozenset(TRACKER_QUEUES)
    assert queue.STATEMENTS_OF_ROLE["connections"] is queue.CONNECTIONS_STATEMENTS
