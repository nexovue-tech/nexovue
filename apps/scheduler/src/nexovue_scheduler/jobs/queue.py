"""The `jobs` table's statements: add, claim, finish, retry, fail, and sweep.

**A claim is a lease.** Claiming a job stamps who holds it and until when —
longer than the job's hard limit, so a job is always killed before its lease
lapses and cannot be claimed twice. A worker that dies leaves its lease to lapse,
and the job is claimed again by whoever polls next: the at-least-once guarantee
Celery's late acknowledgement gave, without a visibility timeout to tune.

**Every outcome is fenced by the lease.** Finishing, retrying and failing name
the worker that claimed the job, so a worker that stalled past its lease cannot
undo what the job's next holder did — the fence `alerts/outbox.py` uses.

**The AI role reaches the queue only through functions** (F3, design *The Postgres
account*). Its account holds no right on `jobs`, since a right to change a table is a
right to lock it; migration 0070's four functions do what the statements below do, for
the `insight` queue alone. A worker takes its statements from `STATEMENTS_OF_ROLE`, and
`test_insight_account_live.py` holds both kinds to one table of cases (AC44).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Final

from nexovue_platform.evidence import INSIGHT_QUEUE
from nexovue_platform.jobs import ENQUEUE, WAKE, JobRequest
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import TextClause, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.types import Text

logger = get_logger(__name__)

# Room past a job's hard limit before its lease lapses: the kill, the report
# back and the write of the outcome all fit inside it.
LEASE_GRACE_SECONDS: Final = 30

# How many expired rows one sweep statement deletes, so a backlog is cleared in
# bites rather than one lock held over the whole table.
SWEEP_BATCH: Final = 1000

# The due rows are chosen once, in a materialized CTE. Written as
# `WHERE id IN (SELECT … LIMIT n FOR UPDATE SKIP LOCKED)`, Postgres may run the
# subquery more than once and lease more than `n` jobs — measured: twenty claimed
# for two free slots, and the worker died holding eighteen.
_CLAIM: Final = text(
    """
    WITH due AS MATERIALIZED (
        SELECT id FROM jobs
        WHERE queue = ANY(:queues)
          AND run_after <= now()
          AND (lease_until IS NULL OR lease_until < now())
          AND (expires_at IS NULL OR expires_at > now())
          AND attempts < max_attempts
        ORDER BY run_after
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
    )
    UPDATE jobs SET
        lease_until = now() + make_interval(secs => jobs.hard_limit_seconds + :grace),
        leased_by = :worker,
        attempts = jobs.attempts + 1
    FROM due
    WHERE jobs.id = due.id
    RETURNING jobs.id, jobs.task, jobs.kwargs, jobs.attempts, jobs.max_attempts,
              jobs.soft_limit_seconds, jobs.hard_limit_seconds
    """
).bindparams(bindparam("queues", type_=ARRAY(Text)))

_FINISH: Final = text("DELETE FROM jobs WHERE id = :id AND leased_by = :worker")

_RETRY: Final = text(
    """
    UPDATE jobs SET
        run_after = now() + make_interval(secs => CAST(:countdown AS double precision)),
        lease_until = NULL,
        leased_by = NULL,
        last_error = :error
    WHERE id = :id AND leased_by = :worker
    """
)

# The same three for the AI role's account, through migration 0070's functions, which name
# the `insight` queue themselves.
_INSIGHT_CLAIM: Final = text("SELECT * FROM insight_claim_jobs(:worker, :limit, :grace)")
_INSIGHT_FINISH: Final = text("SELECT insight_finish_job(:id, :worker)")
_INSIGHT_RETRY: Final = text(
    "SELECT insight_retry_job(:id, :worker, CAST(:countdown AS double precision), :error)"
)


@dataclass(frozen=True, slots=True)
class QueueStatements:
    """How one kind of worker claims, retries and finishes. `queues` is what its claim can
    reach, or None for any queue it is given."""

    claim: TextClause
    retry: TextClause
    finish: TextClause
    queues: frozenset[str] | None

    def claim_parameters(self, *, queues: list[str], worker: str, limit: int) -> dict[str, object]:
        common: dict[str, object] = {"worker": worker, "limit": limit, "grace": LEASE_GRACE_SECONDS}
        return common if self.queues is not None else common | {"queues": queues}


SHARED_STATEMENTS: Final = QueueStatements(_CLAIM, _RETRY, _FINISH, queues=None)
INSIGHT_STATEMENTS: Final = QueueStatements(
    _INSIGHT_CLAIM, _INSIGHT_RETRY, _INSIGHT_FINISH, queues=frozenset({INSIGHT_QUEUE})
)
# By `Settings.scheduler_role`: only the AI role's account lacks rights on `jobs`.
STATEMENTS_OF_ROLE: Final[dict[str, QueueStatements]] = {
    "full": SHARED_STATEMENTS,
    "connections": SHARED_STATEMENTS,
    "insight": INSIGHT_STATEMENTS,
}

# Expired jobs, and jobs whose lease lapsed with no attempt left — what a dead
# worker leaves when the job could not be run again.
_SWEEP_SPENT: Final = text(
    """
    DELETE FROM jobs WHERE id IN (
        SELECT id FROM jobs
        WHERE expires_at <= now()
           OR (attempts >= max_attempts AND lease_until < now())
        LIMIT :batch
        FOR UPDATE SKIP LOCKED
    )
    RETURNING task, attempts, max_attempts, (expires_at <= now()) AS expired
    """
)


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: uuid.UUID
    task: str
    kwargs: dict[str, Any]
    attempts: int
    max_attempts: int
    soft_limit_seconds: int
    hard_limit_seconds: int

    @property
    def may_retry(self) -> bool:
        return self.attempts < self.max_attempts


async def enqueue(connection: AsyncConnection, request: JobRequest, *, wake: bool = False) -> None:
    """Add a job on a connection already in a transaction.

    `wake` is for a job a person is waiting on; see `nexovue_platform.jobs`.
    """
    await connection.execute(text(ENQUEUE), request.parameters())
    if wake:
        await connection.execute(text(WAKE), request.wake_parameters())


async def enqueue_all(engine: AsyncEngine, requests: list[JobRequest]) -> None:
    """Add jobs together, in one transaction."""
    if not requests:
        return
    async with engine.begin() as connection:
        for request in requests:
            await enqueue(connection, request)


async def claim(
    engine: AsyncEngine,
    *,
    queues: list[str],
    worker: str,
    limit: int,
    statements: QueueStatements = SHARED_STATEMENTS,
) -> list[ClaimedJob]:
    """Up to `limit` due jobs from `queues`, leased to `worker`."""
    async with engine.begin() as connection:
        rows = await connection.execute(
            statements.claim,
            statements.claim_parameters(queues=queues, worker=worker, limit=limit),
        )
        return [
            ClaimedJob(
                id=row.id,
                task=row.task,
                kwargs=row.kwargs if isinstance(row.kwargs, dict) else json.loads(row.kwargs),
                attempts=row.attempts,
                max_attempts=row.max_attempts,
                soft_limit_seconds=row.soft_limit_seconds,
                hard_limit_seconds=row.hard_limit_seconds,
            )
            for row in rows
        ]


async def finish(
    engine: AsyncEngine,
    job: ClaimedJob,
    *,
    worker: str,
    statements: QueueStatements = SHARED_STATEMENTS,
) -> None:
    """A job that ran to the end, or failed with no attempt left: gone."""
    async with engine.begin() as connection:
        await connection.execute(statements.finish, {"id": job.id, "worker": worker})


async def retry_later(
    engine: AsyncEngine,
    job: ClaimedJob,
    *,
    worker: str,
    countdown: float,
    error: str,
    statements: QueueStatements = SHARED_STATEMENTS,
) -> None:
    """Hand a job back to the queue to run again after `countdown` seconds."""
    async with engine.begin() as connection:
        await connection.execute(
            statements.retry,
            {"id": job.id, "worker": worker, "countdown": countdown, "error": error},
        )


async def sweep_spent(engine: AsyncEngine) -> int:
    """Delete expired jobs, and lapsed ones with no attempt left. How many went."""
    removed = 0
    while True:
        async with engine.begin() as connection:
            rows = (await connection.execute(_SWEEP_SPENT, {"batch": SWEEP_BATCH})).all()
        for row in rows:
            if not row.expired:
                # A worker died holding its last attempt. Said once, by name,
                # because nothing else will say this job never finished.
                logger.error(
                    "a job was lost with its worker and has no attempt left",
                    task=row.task,
                    attempts=row.attempts,
                )
        removed += len(rows)
        if len(rows) < SWEEP_BATCH:
            return removed
