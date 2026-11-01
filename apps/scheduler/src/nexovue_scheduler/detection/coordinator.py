"""`sweep:detection`, once a minute: which minute is settled, and one project job
for each project detection judges (design, "Where it runs").

It judges nothing itself. Each project's job runs on the `detection` queue, in
an order that takes one project of each organization in turn, so an organization
with many projects cannot hold the others back (design 2.1, M5). At most
`DETECTION_CONCURRENCY` of them run at once in a worker (`jobs/worker.py`).

A minute that is not settled queues nothing: a project's freshness is its last
completed run (C17), so a stalled pipeline shows as detection falling behind,
never as a quiet minute. Nor is one nexovue's own intake failed in: a minute the
receiver refused, or one the install-wide guard holds (`settle.py`, `holds.py`).
Each job is told where the intake fell and what the guard holds, so a drop that
is nexovue's opens nothing.

Each sweep also tells the owners of every project whose detection has not run
for 15 minutes of being expected to, or never has (D24, D34) — first, and
whatever the pipeline says, since a sweep that cannot settle a minute is one of
the ways detection stops.

Last, it records itself in `detection_sweep`: when it ran, and the newest minute
it handed out. Beat reads that minute's age for *detection stalled* (plan T7.1),
the operator's alarm for the case the owners' email cannot report: this sweep
no longer running.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Final

from nexovue_platform.alerts.tasks import DELIVER_TASK, DELIVERIES_QUEUE
from nexovue_platform.jobs import JobRequest
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.alerts.sweeps import KafkaWatermarks
from nexovue_scheduler.detection import (
    DETECTION_QUEUE,
    JUDGE_PROJECT_TASK,
    SWEEP_DETECTION_TASK,
    holds,
    notify,
    settle,
)
from nexovue_scheduler.detection.project_job import (
    NO_TROUBLE,
    PROJECT_HARD_LIMIT_SECONDS,
    PROJECT_SOFT_LIMIT_SECONDS,
    IntakeTrouble,
    judge_request_kwargs,
)
from nexovue_scheduler.detection.projects import ActiveProject, active_projects, fair_order
from nexovue_scheduler.jobs.queue import enqueue_all
from nexovue_scheduler.jobs.registry import SoftTimeLimit, task
from nexovue_scheduler.process_engine import enqueue, with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# A project job not started within a minute is overtaken by the next sweep's.
PROJECT_JOB_EXPIRES_SECONDS: Final = 60
# The claim takes the oldest `run_after` first; jobs added in one transaction share
# its `now()`, so each is a millisecond after the one before, in the fair order.
FAIR_STEP_SECONDS: Final = 0.001

NO_PROGRESS_STORE: Final = "CLICKHOUSE_DSN is not set for the scheduler; no minute can be settled"

# One row, install-wide. The minute never moves backwards: a hold can hand out an
# older one, and the age read is of the newest minute judged.
_RECORD_SWEEP: Final = text(
    """
    INSERT INTO detection_sweep (swept_at, settled_minute)
    VALUES (clock_timestamp(), CAST(:settled AS timestamptz))
    ON CONFLICT (one_row) DO UPDATE
    SET swept_at = EXCLUDED.swept_at,
        settled_minute = GREATEST(detection_sweep.settled_minute, EXCLUDED.settled_minute)
    """
)

_progress: settle.PipelineProgress | None = None
_watermarks: KafkaWatermarks | None = None


def project_requests(
    projects: Sequence[ActiveProject], settled: datetime, trouble: IntakeTrouble = NO_TROUBLE
) -> list[JobRequest]:
    """One job per project, in the fair order, each judging up to `settled`."""
    return [
        JobRequest(
            task=JUDGE_PROJECT_TASK,
            queue=DETECTION_QUEUE,
            kwargs=judge_request_kwargs(project.project_id, settled, trouble),
            org_id=str(project.org_id),
            countdown_seconds=position * FAIR_STEP_SECONDS,
            expires_seconds=PROJECT_JOB_EXPIRES_SECONDS,
            soft_limit_seconds=PROJECT_SOFT_LIMIT_SECONDS,
            hard_limit_seconds=PROJECT_HARD_LIMIT_SECONDS,
        )
        for position, project in enumerate(fair_order(list(projects)))
    ]


async def queue_project_jobs(
    engine: AsyncEngine, settled: datetime, trouble: IntakeTrouble, *, now: datetime
) -> int:
    async with engine.connect() as connection:
        projects = await active_projects(connection, now=now)
    await enqueue_all(engine, project_requests(projects, settled, trouble))
    return len(projects)


async def report_stopped(engine: AsyncEngine, *, now: datetime) -> int:
    async with engine.begin() as connection:
        return await notify.report_stopped(connection, now=now)


async def record_sweep(connection: AsyncConnection, settled: datetime | None) -> None:
    """The sweep ran, handing out `settled`, or nothing when no minute settled."""
    await connection.execute(_RECORD_SWEEP, {"settled": settled})


async def _record(engine: AsyncEngine, settled: datetime | None) -> None:
    async with engine.begin() as connection:
        await record_sweep(connection, settled)


@task(SWEEP_DETECTION_TASK)
def sweep() -> None:
    _report_stopped()
    settled = _hand_out()
    with_engine(lambda engine: _record(engine, settled))


def _hand_out() -> datetime | None:
    """Queue each project's job up to the settled minute, and return that minute;
    None when no minute could be settled."""
    found = _settled()
    if found is None:
        return None
    intake = _intake(found.now)
    decision = with_engine(lambda engine: holds.decide(engine, found, intake))
    settled = decision.minute
    if settled is None:
        return None
    if decision.holding:
        logger.warning(
            "nexovue's intake failed; holding every project's minutes",
            settled_minute=settled.isoformat(),
            held=[
                {"reason": hold.reason, "from": hold.span.first.isoformat()}
                for hold in decision.holding
            ],
        )
    queued = with_engine(
        lambda engine: queue_project_jobs(engine, settled, decision.trouble, now=datetime.now(UTC))
    )
    logger.info(
        "detection project jobs queued", settled_minute=settled.isoformat(), projects=queued
    )
    return settled


def _report_stopped() -> None:
    """D24. Its own failure must not stop the judging that follows."""
    try:
        written = with_engine(lambda engine: report_stopped(engine, now=datetime.now(UTC)))
    except (SoftTimeLimit, KeyboardInterrupt):
        raise
    except Exception:
        logger.exception("could not tell owners whose detection stopped; trying next minute")
        return
    if written:
        logger.warning("owners told that detection stopped", notifications=written)
        enqueue(JobRequest(task=DELIVER_TASK, queue=DELIVERIES_QUEUE))


def _intake(now: datetime) -> settle.Intake | None:
    """What the receiver got Kafka to accept, minute by minute; None, and no guard
    this minute, when Kafka cannot say."""
    try:
        return settle.read_intake(_kafka(), get_settings().kafka_spans_topic, now)
    except (SoftTimeLimit, KeyboardInterrupt):
        raise
    except Exception:
        logger.exception("could not read what the receiver accepted; no install-wide guard")
        return None


def _settled() -> settle.Settled | None:
    """The pipeline's settled minute, or None, said at the level it deserves."""
    settings = get_settings()
    if not settings.clickhouse_dsn:
        logger.error(NO_PROGRESS_STORE)
        return None
    try:
        found = settle.read_settled(_progress_store(), _kafka(), settings.kafka_spans_topic)
    except (SoftTimeLimit, KeyboardInterrupt):
        raise
    except Exception:
        logger.exception("could not read which minute is settled; judging nothing this minute")
        return None
    if found.minute is None:
        logger.error(
            "a span partition holds records the worker has never counted; judging nothing",
            missing=list(found.missing),
        )
        return None
    if found.behind:
        logger.warning(
            "the pipeline is behind; judging only what has settled",
            settled_minute=found.minute.isoformat(),
            current_minute=found.current.isoformat(),
            lagging=list(found.lagging),
        )
    return found


def _progress_store() -> settle.PipelineProgress:
    global _progress
    if _progress is None:
        _progress = settle.connect(get_settings().clickhouse_dsn)
    return _progress


def _kafka() -> KafkaWatermarks:
    global _watermarks
    if _watermarks is None:
        _watermarks = KafkaWatermarks(get_settings().kafka_brokers)
    return _watermarks
