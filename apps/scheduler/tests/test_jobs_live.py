"""The job queue that replaced Celery, against real Postgres and real child processes.

What it must keep from Celery: a job runs once even with several workers
claiming; a run nobody started in time is dropped; the soft limit lets a task
let go and the hard limit kills it; a dead worker's job runs again; a retry
waits its countdown; and a person pressing Run now is not kept waiting for the
next poll.

Every test claims from a queue of its own, so the dev stack's workers never take
these jobs and these workers never take theirs.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

import job_fixtures
import pytest
from live import require_stack
from nexovue_platform.jobs import JobRequest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexovue_scheduler.jobs import queue, worker
from nexovue_scheduler.jobs.queue import enqueue
from nexovue_scheduler.jobs.worker import Worker

DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
MODULES: Final = ("job_fixtures",)
# Long enough for a spawned child to import every task module and run a job.
PATIENCE_SECONDS: Final = 20.0


def _reachable() -> bool:
    parts = urlsplit(DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="the job queue suite",
    remedy="start the dev stack so Postgres answers (`just dev`)",
)


@pytest.fixture(autouse=True)
def _child_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """What a child reads at start; spawned children inherit the environment."""
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("POSTGRES_DSN", ASYNC_DSN)
    monkeypatch.setenv("KAFKA_BROKERS", "localhost:29092")


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    built = create_async_engine(ASYNC_DSN)
    yield built
    await built.dispose()


@pytest.fixture
async def lane(engine: AsyncEngine) -> AsyncIterator[str]:
    """A queue nothing else claims from, emptied afterwards."""
    name = f"jobs-test.{uuid.uuid4().hex}"
    yield name
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM jobs WHERE queue = :q"), {"q": name})


async def _add(engine: AsyncEngine, request: JobRequest, *, wake: bool = False) -> None:
    async with engine.begin() as connection:
        await enqueue(connection, request, wake=wake)


async def _jobs_left(engine: AsyncEngine, lane: str) -> int:
    async with engine.connect() as connection:
        left = await connection.scalar(
            text("SELECT count(*) FROM jobs WHERE queue = :q"), {"q": lane}
        )
    return int(left or 0)


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


async def _work_until(
    engine: AsyncEngine,
    lane: str,
    done: Callable[[], bool],
    *,
    workers: int = 1,
    concurrency: int = 1,
    queues: list[str] | None = None,
    caps: dict[str, int] | None = None,
) -> None:
    """Run workers on `lane` until `done()` holds, or fail when patience runs out."""
    stop = asyncio.Event()
    running = [
        asyncio.create_task(
            Worker(
                engine,
                listen_dsn=DSN,
                queues=queues or [lane],
                concurrency=concurrency,
                task_modules=MODULES,
                caps=caps or {},
            ).run(stop)
        )
        for _ in range(workers)
    ]
    try:
        async with asyncio.timeout(PATIENCE_SECONDS):
            while not done():
                await asyncio.sleep(0.1)
    finally:
        stop.set()
        await asyncio.gather(*running)


async def test_each_job_runs_once_however_many_workers_claim(
    engine: AsyncEngine, lane: str, tmp_path: Path
) -> None:
    """Also the regression for a claim leasing more than it asked for: written as
    `id IN (SELECT … LIMIT n FOR UPDATE SKIP LOCKED)`, Postgres re-ran the subquery
    under some plans and one claim for two slots took all twenty jobs; this test
    then timed out in about one run in three."""
    notes = tmp_path / "notes"
    for number in range(20):
        await _add(
            engine,
            JobRequest(
                task=job_fixtures.NOTE,
                queue=lane,
                # Still running when the other worker polls, so a job claimed
                # twice would show up here as a marker written twice.
                kwargs={"path": str(notes), "marker": str(number), "pause": 0.5},
                # Attempts to spare: only the lease stops a running job being
                # claimed again.
                max_attempts=3,
            ),
        )

    await _work_until(engine, lane, lambda: len(_lines(notes)) >= 20, workers=2, concurrency=2)
    await asyncio.sleep(2)

    assert sorted(_lines(notes), key=int) == [str(number) for number in range(20)]
    assert await _jobs_left(engine, lane) == 0


async def test_a_job_nobody_started_in_time_is_dropped_unrun(
    engine: AsyncEngine, lane: str, tmp_path: Path
) -> None:
    """FR26: a probe due every minute is worthless five minutes late."""
    notes = tmp_path / "notes"
    late = JobRequest(
        task=job_fixtures.NOTE,
        queue=lane,
        kwargs={"path": str(notes), "marker": "late"},
        expires_seconds=0.01,
    )
    on_time = JobRequest(
        task=job_fixtures.NOTE, queue=lane, kwargs={"path": str(notes), "marker": "on time"}
    )
    await _add(engine, late)
    await asyncio.sleep(0.1)
    await _add(engine, on_time)

    await _work_until(engine, lane, lambda: "on time" in _lines(notes))

    assert _lines(notes) == ["on time"]
    await queue.sweep_spent(engine)
    assert await _jobs_left(engine, lane) == 0


async def test_the_soft_limit_lets_a_task_clean_up(
    engine: AsyncEngine, lane: str, tmp_path: Path
) -> None:
    notes = tmp_path / "notes"
    await _add(
        engine,
        JobRequest(
            task=job_fixtures.SLEEP,
            queue=lane,
            kwargs={"path": str(notes), "seconds": 30},
            soft_limit_seconds=1,
            hard_limit_seconds=10,
        ),
    )

    await _work_until(engine, lane, lambda: "cleaned up" in _lines(notes))
    await asyncio.sleep(0.5)

    assert await _jobs_left(engine, lane) == 0


async def test_the_hard_limit_kills_a_stuck_task_and_its_slot_runs_the_next(
    engine: AsyncEngine, lane: str, tmp_path: Path
) -> None:
    """A probe hung inside libcurl ignores its soft limit; only the kill frees the
    slot, and the slot must then take the next job rather than stay dead."""
    notes = tmp_path / "notes"
    await _add(
        engine,
        JobRequest(
            task=job_fixtures.BLOCK,
            queue=lane,
            kwargs={"path": str(notes), "seconds": 60},
            soft_limit_seconds=1,
            hard_limit_seconds=2,
        ),
    )
    await _add(
        engine,
        JobRequest(
            task=job_fixtures.NOTE, queue=lane, kwargs={"path": str(notes), "marker": "next"}
        ),
    )

    await _work_until(engine, lane, lambda: "next" in _lines(notes))

    assert _lines(notes) == ["blocking", "next"]


async def test_a_dead_workers_job_runs_again_once_its_lease_lapses(
    engine: AsyncEngine, lane: str, tmp_path: Path
) -> None:
    notes = tmp_path / "notes"
    await _add(
        engine,
        JobRequest(
            task=job_fixtures.NOTE,
            queue=lane,
            kwargs={"path": str(notes), "marker": "rescued"},
            max_attempts=2,
        ),
    )
    [claimed] = await queue.claim(engine, queues=[lane], worker="died-mid-job", limit=1)
    # The worker dies holding it; its lease runs out.
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE jobs SET lease_until = now() - interval '1 second' WHERE id = :id"),
            {"id": claimed.id},
        )

    await _work_until(engine, lane, lambda: "rescued" in _lines(notes))


async def test_a_job_with_no_attempt_left_is_not_run_again(
    engine: AsyncEngine, lane: str, tmp_path: Path
) -> None:
    """At-least-once, but bounded: a job whose every attempt died is swept, and
    said so, rather than killing worker after worker."""
    await _add(engine, JobRequest(task=job_fixtures.NOTE, queue=lane, kwargs={}))
    [claimed] = await queue.claim(engine, queues=[lane], worker="died-mid-job", limit=1)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE jobs SET lease_until = now() - interval '1 second' WHERE id = :id"),
            {"id": claimed.id},
        )

    assert await queue.claim(engine, queues=[lane], worker="another", limit=1) == []
    await queue.sweep_spent(engine)
    assert await _jobs_left(engine, lane) == 0


async def test_a_retry_waits_its_countdown_and_stops_when_it_succeeds(
    engine: AsyncEngine, lane: str, tmp_path: Path
) -> None:
    notes = tmp_path / "notes"
    await _add(
        engine,
        JobRequest(
            task=job_fixtures.RETRY,
            queue=lane,
            kwargs={"path": str(notes), "succeed_on": 2},
            max_attempts=3,
        ),
    )

    await _work_until(engine, lane, lambda: len(_lines(notes)) >= 2)
    await asyncio.sleep(1.5)

    runs = [line.split() for line in _lines(notes)]
    assert [run[1] for run in runs] == ["1", "2"]
    assert float(runs[1][3]) - float(runs[0][3]) >= 1.0
    assert await _jobs_left(engine, lane) == 0


async def test_a_job_somebody_waits_on_wakes_the_worker(
    engine: AsyncEngine, lane: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run now and Send test notify; with the poll stretched to a minute, the job
    still runs at once."""
    monkeypatch.setattr(worker, "POLL_SECONDS", 60.0)
    notes = tmp_path / "notes"
    stop = asyncio.Event()
    running = asyncio.create_task(
        Worker(engine, listen_dsn=DSN, queues=[lane], concurrency=1, task_modules=MODULES).run(stop)
    )
    try:
        # The first claim happens at start; wait until the worker is idle on the poll.
        await asyncio.sleep(3)
        await _add(
            engine,
            JobRequest(
                task=job_fixtures.NOTE, queue=lane, kwargs={"path": str(notes), "marker": "woken"}
            ),
            wake=True,
        )
        async with asyncio.timeout(PATIENCE_SECONDS):
            while "woken" not in _lines(notes):
                await asyncio.sleep(0.1)
    finally:
        stop.set()
        await running


def _most_at_once(lines: list[str], marker_prefix: str) -> int:
    running = most = 0
    for line in lines:
        kind, marker = line.split(" ", 1)
        if not marker.startswith(marker_prefix):
            continue
        running += 1 if kind == "start" else -1
        most = max(most, running)
    return most


async def test_a_capped_queue_never_holds_more_than_its_slots(
    engine: AsyncEngine, lane: str, tmp_path: Path
) -> None:
    """C23, D32: `scheduler-light` takes detection beside its probes, and detection
    holds at most its cap of the slots while the rest stay free for the others."""
    others = f"{lane}.others"
    notes = tmp_path / "notes"
    try:
        for number in range(4):
            await _add(
                engine,
                JobRequest(
                    task=job_fixtures.SPAN,
                    queue=lane,
                    kwargs={"path": str(notes), "marker": f"capped {number}", "pause": 0.6},
                ),
            )
        for number in range(4):
            await _add(
                engine,
                JobRequest(
                    task=job_fixtures.SPAN,
                    queue=others,
                    kwargs={"path": str(notes), "marker": f"other {number}", "pause": 0.6},
                ),
            )

        await _work_until(
            engine,
            lane,
            lambda: sum(line.startswith("end") for line in _lines(notes)) >= 8,
            concurrency=4,
            queues=[lane, others],
            caps={lane: 1},
        )

        lines = _lines(notes)
        assert _most_at_once(lines, "capped") == 1
        # The slots the cap leaves are the others', and they used them.
        assert _most_at_once(lines, "other") >= 2
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM jobs WHERE queue = :q"), {"q": others})
