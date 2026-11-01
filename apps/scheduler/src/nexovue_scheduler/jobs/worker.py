"""A worker: claims jobs from its queues and runs each in a child process.

**One slot per child, a fixed number of children** — the shape of Celery's
prefork pool, so `scheduler-light` still runs eight probes at once and
`scheduler-deliveries` four. A child is reused job after job; one killed at its
hard limit, or one that died, is replaced before its slot takes another job.

**It polls every second and wakes early on `NOTIFY`.** The poll is what picks up
beat's work and every retry; the notification only shortens the wait for a
person who pressed Run now or Send test.

**Some queues are capped.** A worker never runs more than `QUEUE_CAPS` allows
from a capped queue at once, whatever slots are free: `scheduler-light` takes the
`detection` queue beside its probes, and detection may hold at most two of its
eight slots (D32). The cap is per worker, which is per container; it is the
container's slots it protects.

**Stopping is orderly.** On SIGTERM it claims nothing more and waits for what it
is running. A job still running when the process is killed outright keeps its
lease until the lease lapses, and is then claimed again by another worker if it
has an attempt left.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import socket
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnProcess
from typing import Any, Final

import asyncpg
from nexovue_platform.jobs import NOTIFY_CHANNEL
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler import metrics
from nexovue_scheduler.detection import DETECTION_CONCURRENCY, DETECTION_QUEUE
from nexovue_scheduler.health import Pulse
from nexovue_scheduler.jobs import child, queue, registry
from nexovue_scheduler.jobs.queue import ClaimedJob

logger = get_logger(__name__)

POLL_SECONDS: Final = 1.0
# With every slot busy the loop claims nothing, so it asks Postgres this often
# anyway: readiness is a recent answer, and long jobs must not read as an outage.
REACH_POSTGRES_SECONDS: Final = 10.0
# How long a lost notification connection waits before it is tried again; the
# poll carries every job meanwhile.
RELISTEN_SECONDS: Final = 30.0
KILLED: Final = "killed"
# The child died mid-job, so it could not report its own end: killed by the
# kernel over the container's memory limit, or crashed.
DIED: Final = "died"
# How long a dead child is given to be reaped, so its exit code can be read.
REAP_SECONDS: Final = 5.0
# The most jobs a worker runs at once from each capped queue.
QUEUE_CAPS: Final[Mapping[str, int]] = {DETECTION_QUEUE: DETECTION_CONCURRENCY}

_SPAWN = multiprocessing.get_context("spawn")


class Slot:
    """One child process and the pipe to it."""

    def __init__(self, modules: tuple[str, ...] = registry.TASK_MODULES) -> None:
        self._modules = modules
        self._process: SpawnProcess | None = None
        self._pipe: Connection | None = None
        self.start()

    def start(self) -> None:
        ours, theirs = _SPAWN.Pipe()
        process = _SPAWN.Process(target=child.serve, args=(theirs, self._modules), daemon=True)
        process.start()
        theirs.close()
        self._process, self._pipe = process, ours

    async def run(self, job: ClaimedJob) -> tuple[Any, ...]:
        """Run one job in the child; kill and replace the child at the hard limit."""
        assert self._pipe is not None
        self._pipe.send((job.task, job.kwargs, job.soft_limit_seconds, job.attempts))
        finished = await asyncio.to_thread(self._pipe.poll, job.hard_limit_seconds)
        if finished:
            try:
                outcome: tuple[Any, ...] = self._pipe.recv()
                return outcome
            except (EOFError, OSError):
                return (DIED, self._reap())
        self._replace()
        return (KILLED, f"killed at its hard time limit of {job.hard_limit_seconds}s")

    def stop(self) -> None:
        if self._pipe is not None:
            self._pipe.close()
        if self._process is not None:
            self._process.join(timeout=5)
            if self._process.is_alive():
                self._process.kill()

    def _reap(self) -> int | None:
        """Replace a child that died, returning its exit code: minus the signal
        that killed it, -9 when the kernel's OOM killer did."""
        assert self._process is not None
        self._process.join(REAP_SECONDS)
        exit_code = self._process.exitcode
        self._replace()
        return exit_code

    def _replace(self) -> None:
        if self._process is not None:
            self._process.kill()
            self._process.join()
        if self._pipe is not None:
            self._pipe.close()
        self.start()


class Worker:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        listen_dsn: str,
        queues: list[str],
        concurrency: int,
        task_modules: tuple[str, ...] = registry.TASK_MODULES,
        pulse: Pulse | None = None,
        caps: Mapping[str, int] = QUEUE_CAPS,
    ) -> None:
        self._engine = engine
        self._pulse = pulse or Pulse()
        self._reach_postgres_at = 0.0
        self._listen_dsn = listen_dsn
        self._queues = queues
        self._caps = {queue_name: cap for queue_name, cap in caps.items() if queue_name in queues}
        self._uncapped = [queue_name for queue_name in queues if queue_name not in self._caps]
        self._running_from = dict.fromkeys(self._caps, 0)
        # Unique per worker, not per process: the lease fence is this name, and two
        # workers sharing one would finish each other's jobs.
        self._name = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._idle = [Slot(task_modules) for _ in range(concurrency)]
        self._running: set[asyncio.Task[None]] = set()
        self._wake = asyncio.Event()
        self._listener: asyncpg.Connection | None = None
        self._relisten_at = 0.0

    async def run(self, stop: asyncio.Event) -> None:
        logger.info("worker started", queues=self._queues, concurrency=len(self._idle))
        loop = asyncio.get_running_loop()
        # One thread per slot waits on its child's pipe, so a full pool never
        # queues a job's wait — and so its hard limit — behind another's.
        loop.set_default_executor(ThreadPoolExecutor(max_workers=len(self._idle) + 2))
        try:
            while not stop.is_set():
                if self._listener is None and loop.time() >= self._relisten_at:
                    await self._listen()
                if self._idle:
                    await self._claim_into_idle_slots()
                elif loop.time() >= self._reach_postgres_at:
                    await self._reach_postgres()
                self._pulse.beat()
                self._wake.clear()
                waiting = asyncio.ensure_future(self._wake.wait())
                stopping = asyncio.ensure_future(stop.wait())
                await asyncio.wait(
                    {waiting, stopping}, timeout=POLL_SECONDS, return_when=asyncio.FIRST_COMPLETED
                )
                waiting.cancel()
                stopping.cancel()
        finally:
            logger.info("worker stopping; finishing what it is running", running=len(self._running))
            if self._running:
                await asyncio.gather(*self._running, return_exceptions=True)
            for slot in self._idle:
                slot.stop()
            if self._listener is not None:
                await self._listener.close()

    async def _claim_into_idle_slots(self) -> None:
        # Capped queues first: each takes no more than its room, so it cannot
        # crowd out the rest, and the rest cannot starve it.
        lanes = [
            ([queue_name], cap - self._running_from[queue_name])
            for queue_name, cap in self._caps.items()
        ]
        if self._uncapped:
            lanes.append((self._uncapped, len(self._idle)))
        for lane, room in lanes:
            limit = min(room, len(self._idle))
            if limit <= 0:
                continue
            try:
                jobs = await queue.claim(self._engine, queues=lane, worker=self._name, limit=limit)
            except Exception:
                logger.exception("jobs could not be claimed; trying again")
                return
            self._pulse.reached_postgres()
            capped = lane[0] if lane[0] in self._caps else None
            for job in jobs:
                slot = self._idle.pop()
                if capped is not None:
                    self._running_from[capped] += 1
                running = asyncio.create_task(self._run(slot, job, capped))
                self._running.add(running)
                running.add_done_callback(self._running.discard)

    async def _reach_postgres(self) -> None:
        self._reach_postgres_at = asyncio.get_running_loop().time() + REACH_POSTGRES_SECONDS
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            logger.warning("Postgres did not answer while every slot was busy")
            return
        self._pulse.reached_postgres()

    async def _run(self, slot: Slot, job: ClaimedJob, capped: str | None) -> None:
        try:
            outcome = await slot.run(job)
            await self._record(job, outcome)
        except Exception:
            # The lease lapses and the job is claimed again if it has an attempt
            # left; nothing here is worth stopping the worker for.
            logger.exception("a job's outcome could not be recorded", task=job.task)
        finally:
            if capped is not None:
                self._running_from[capped] -= 1
            self._idle.append(slot)
            self._wake.set()

    async def _record(self, job: ClaimedJob, outcome: tuple[Any, ...]) -> None:
        kind = outcome[0]
        if kind == child.RETRY:
            countdown, reason = outcome[1], outcome[2]
            if job.may_retry:
                await queue.retry_later(
                    self._engine, job, worker=self._name, countdown=countdown, error=reason
                )
                metrics.JOBS_ENDED.labels(outcome=metrics.RETRIED).inc()
                return
            logger.error(
                "a job asked to retry with no attempt left", task=job.task, attempts=job.attempts
            )
        elif kind == KILLED:
            logger.error(
                "a job was killed at its hard time limit", task=job.task, reason=outcome[1]
            )
        elif kind == DIED:
            logger.error("a job's process died", task=job.task, exit_code=outcome[1])
        await queue.finish(self._engine, job, worker=self._name)
        ended = metrics.KILLED if kind in (KILLED, DIED) else metrics.FINISHED
        metrics.JOBS_ENDED.labels(outcome=ended).inc()

    async def _listen(self) -> None:
        try:
            self._listener = await asyncpg.connect(self._listen_dsn)
            await self._listener.add_listener(NOTIFY_CHANNEL, self._notified)
            self._listener.add_termination_listener(self._listener_lost)
        except Exception:
            logger.warning("job notifications are unavailable; polling only")
            self._listener = None
            self._relisten_at = asyncio.get_running_loop().time() + RELISTEN_SECONDS

    def _notified(self, _connection: Any, _pid: int, _channel: str, payload: str) -> None:
        if payload in self._queues:
            self._wake.set()

    def _listener_lost(self, _connection: Any) -> None:
        self._listener = None
        self._relisten_at = asyncio.get_running_loop().time() + RELISTEN_SECONDS
