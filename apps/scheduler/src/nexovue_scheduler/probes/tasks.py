"""The probe tasks: run one check, publish what it saw, confirm before believing.

**Beat hands the whole check definition over rather than an id, so a probe never
touches Postgres.** A database query per check per interval would put the
control plane on the path of every measurement, and a probe that cannot run
because Postgres is briefly busy is a blind minute for that customer. Secrets
travel with the definition sealed, and are opened here immediately before use
(ADR-010).

The one control-plane write — a check's confirmed state — is a separate task,
dispatched *after* the measurement is published. A slow database then delays a
state change and never a reading.

The cost of carrying the definition is staleness bounded by the beat refresh
interval: a check edited in the console runs with its previous settings for up
to that long.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from celery.exceptions import SoftTimeLimitExceeded
from nexovue_platform.checks.config import HeartbeatCheckConfig, parse_config
from nexovue_platform.checks.evidence import RunEvidence, StepEvidence
from nexovue_platform.checks.maintenance import WindowSchedule, any_active
from nexovue_platform.checks.task import (
    RECORD_OUTCOME_TASK,
    RUN_CHECK_TASK,
    RUN_NOW_RESULT_TTL_SECONDS,
    SHARED_QUEUE,
    SWEEP_HEARTBEATS_TASK,
    SWEEP_UNATTACHED_FILES_TASK,
    CheckDefinition,
    MaintenanceSchedule,
    new_run_id,
    probe_queue,
    run_now_result_key,
    time_limits_for,
)
from nexovue_platform.telemetry.logging import get_logger
from pydantic import ValidationError
from redis import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexovue_scheduler.celery_app import app
from nexovue_scheduler.checks import HEARTBEAT_TYPE, ScheduledCheck, load_enabled_checks
from nexovue_scheduler.probes import heartbeat, runner, state_store
from nexovue_scheduler.probes.files import ObjectStoreFiles
from nexovue_scheduler.probes.fitting import fitted
from nexovue_scheduler.probes.result import RunMeasurement
from nexovue_scheduler.publish import ResultPublisher, build_envelope, build_evidence_envelope
from nexovue_scheduler.settings import Settings, get_settings

logger = get_logger(__name__)

NANOS_PER_SECOND: Final = 1_000_000_000

# Spacing between an attempt and its confirmation. Long enough for a blip to
# pass, short enough that a real outage is confirmed well inside one interval.
RETRY_DELAY_SECONDS: Final = 5

# How long an abandoned upload is kept before the sweep takes it (FR4), and how
# long a file a check stopped using is kept — long enough to undo the edit.
UNATTACHED_FILE_LIFETIME: Final = timedelta(hours=24)
DETACHED_FILE_LIFETIME: Final = timedelta(hours=1)

# Delivery is retried here, not by re-running the task: a re-run is a new probe —
# another request to the customer's API, under a new run id nothing can collapse.
DELIVERY_ATTEMPTS: Final = 3
DELIVERY_BACKOFF_SECONDS: Final = 2


_publisher: ResultPublisher | None = None
_engine: AsyncEngine | None = None
_loop: asyncio.AbstractEventLoop | None = None
_files: ObjectStoreFiles | None = None
_redis: Redis | None = None


def _publisher_for_process() -> ResultPublisher:
    """One producer per worker process, built on first use.

    Celery forks workers, and a Kafka producer's background thread does not
    survive a fork — so it is created after the fork, on first task, rather than
    at import.
    """
    global _publisher
    if _publisher is None:
        _publisher = ResultPublisher(get_settings())
    return _publisher


def _files_for_process() -> ObjectStoreFiles:
    global _files
    if _files is None:
        _files = ObjectStoreFiles(get_settings())
    return _files


def _redis_for_process() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(str(get_settings().redis_url))
    return _redis


@app.task(name=RUN_CHECK_TASK, bind=True, max_retries=2)
def run_check(
    self: Any,
    *,
    definition: dict[str, Any],
    location: str,
    attempt: int = 1,
    run_now_request_id: str | None = None,
) -> None:
    """Run one check once, from one location.

    A failing target is a successful task: down is a measurement, and it is
    published exactly as up is. What retries as a *task* is a failure to deliver
    the measurement. What retries as a *check* is a down result, confirmed from
    another location before anybody believes it (FR25, AC19, AC20).
    """
    settings = get_settings()
    check = CheckDefinition.model_validate(definition)
    started = datetime.now(UTC)
    observed_at = time.time_ns()
    context = runner.RunContext(
        location=location,
        attempt=attempt,
        run_id=new_run_id(),
        started_at=started,
        maintenance=run_now_request_id is None and _in_maintenance(check.maintenance, started),
        private_allowed=settings.private_probe_targets_allowed,
        secret_key=settings.check_secret_key,
        files=_files_for_process(),
        dns_resolvers=tuple(settings.dns_resolvers),
    )
    evidence, measurement = runner.run(check, context)

    if run_now_request_id is not None:
        # Run now records nothing: no reading, no evidence log, no state (AC24).
        _store_run_now_result(check.org_id, run_now_request_id, evidence)
        return

    confirming = evidence.outcome == "down" and attempt <= check.retries
    evidence = evidence.model_copy(update={"final": not confirming})
    measurement = _with_final(measurement, not confirming)
    logger.info(
        "check completed",
        check_id=check.check_id,
        check_type=check.type,
        location=location,
        attempt=attempt,
        outcome=evidence.outcome,
        final=not confirming,
        duration_ms=None if evidence.duration_ms is None else round(evidence.duration_ms, 1),
    )

    _deliver([_messages(check, measurement, evidence, observed_at, settings)], check.check_id)

    if confirming:
        _confirm_elsewhere(check, location, attempt)
    elif not context.maintenance and evidence.outcome != "prober_error":
        _record_outcome(check, evidence, location, started)


@app.task(name=RECORD_OUTCOME_TASK, bind=True, max_retries=5)
def record_outcome(
    self: Any,
    *,
    check_id: str,
    org_id: str,
    project_id: str,
    outcome: str,
    run_id: str,
    location: str,
    observed_at: str,
    reason: str | None = None,
) -> None:
    """Apply one confirmed run to the check's state (FR27, FR28).

    Retried with backoff when Postgres is unavailable: the reading is already
    published, so waiting costs nothing but how soon the state is current.
    """
    recorded = state_store.RecordedOutcome(
        check_id=check_id,
        org_id=org_id,
        project_id=project_id,
        outcome=outcome,
        run_id=run_id,
        location=location,
        observed_at=datetime.fromisoformat(observed_at),
        reason=reason,
    )
    try:
        changed_to = _with_engine(lambda engine: state_store.record(engine, recorded))
    except Exception as failure:
        logger.warning("recording a check state failed; retrying", check_id=check_id)
        raise self.retry(exc=failure, countdown=2 ** (self.request.retries + 1)) from failure
    if changed_to is not None:
        logger.info("check state changed", check_id=check_id, state=changed_to, run_id=run_id)


@app.task(name=SWEEP_HEARTBEATS_TASK, bind=True, max_retries=0)
def sweep_heartbeats(self: Any) -> None:
    """Judge every heartbeat against its last call, publish the reading, and
    record the state. One query for all of them, once a minute (plan Q5)."""
    settings = get_settings()
    heartbeats = _with_engine(
        lambda engine: load_enabled_checks(
            engine, all_locations=settings.probe_locations, only_type=HEARTBEAT_TYPE
        )
    )
    now = datetime.now(UTC)
    judged: list[tuple[CheckDefinition, RunEvidence, list[tuple[str, str, bytes]]]] = []
    for scheduled in heartbeats:
        try:
            result = _judge_heartbeat(scheduled, now, settings)
        except SoftTimeLimitExceeded:
            raise
        except Exception:
            logger.exception("judging a heartbeat failed", check_id=str(scheduled.id))
            continue
        if result is not None:
            judged.append(result)
    if not judged:
        return
    _deliver([messages for _, _, messages in judged], None)
    for check, evidence, _ in judged:
        if not evidence.maintenance:
            _record_outcome(check, evidence, evidence.location, now)


# The row goes first, and only while still unattached: a file attached since the
# sweep read it keeps its row, and so keeps its object.
_TAKE_UNATTACHED: Final = text(
    """
    DELETE FROM check_files
    WHERE id IN (
        SELECT id FROM check_files
        WHERE check_id IS NULL
          AND (
            (detached_at IS NULL AND created_at < :upload_cutoff)
            OR detached_at < :detach_cutoff
          )
        ORDER BY created_at
        LIMIT 500
        FOR UPDATE SKIP LOCKED
    )
    AND check_id IS NULL
    RETURNING id, org_id
    """
)


@app.task(name=SWEEP_UNATTACHED_FILES_TASK, bind=True, max_retries=0)
def sweep_unattached_files(self: Any) -> None:
    """Remove files no check uses: an upload nobody saved a day on (FR4), and a file
    a check stopped using — or a deleted check's — an hour on (BR9).

    Row first, committed, then the object. An object left behind by a failure here
    is under the tenant's prefix, which a tenant purge removes; a row whose object
    was deleted from under it would be a check that fails for a reason of ours.
    """
    now = datetime.now(UTC)
    parameters = {
        "upload_cutoff": now - UNATTACHED_FILE_LIFETIME,
        "detach_cutoff": now - DETACHED_FILE_LIFETIME,
    }

    async def take(engine: AsyncEngine) -> list[tuple[str, str]]:
        async with engine.begin() as connection:
            rows = (await connection.execute(_TAKE_UNATTACHED, parameters)).all()
        return [(str(org_id), str(file_id)) for file_id, org_id in rows]

    taken = _with_engine(take)
    for org_id, file_id in taken:
        try:
            _files_for_process().delete(org_id, file_id)
        except Exception:
            logger.warning("a swept check file's object could not be deleted", file_id=file_id)
    if taken:
        logger.info("unused check files removed", files=len(taken))


# --- the parts ---------------------------------------------------------------------


def _in_maintenance(schedules: list[MaintenanceSchedule], at: datetime) -> bool:
    return any_active(
        [
            WindowSchedule(
                starts_at=datetime.fromisoformat(schedule.starts_at),
                ends_at=datetime.fromisoformat(schedule.ends_at),
                recurrence=schedule.recurrence,
                repeat_until=(
                    datetime.fromisoformat(schedule.repeat_until) if schedule.repeat_until else None
                ),
            )
            for schedule in schedules
        ],
        at,
    )


def _with_final(measurement: RunMeasurement, final: bool) -> RunMeasurement:
    return replace(measurement, final=final)


def _messages(
    check: CheckDefinition,
    measurement: RunMeasurement,
    evidence: RunEvidence,
    observed_at: int,
    settings: Settings,
) -> list[tuple[str, str, bytes]]:
    """A run's reading and its evidence, as the broker messages that carry them."""
    envelope_arguments: dict[str, Any] = {
        "org_id": check.org_id,
        "project_id": check.project_id,
        "observed_at_unix_nano": observed_at,
        "environment": settings.env,
        "region": evidence.location,
    }
    return [
        (
            settings.kafka_metrics_topic,
            check.org_id,
            build_envelope(measurement, **envelope_arguments),
        ),
        (
            settings.kafka_logs_topic,
            check.org_id,
            build_evidence_envelope(fitted(evidence), **envelope_arguments),
        ),
    ]


def _deliver(batches: list[list[tuple[str, str, bytes]]], check_id: str | None) -> None:
    """Deliver messages already built, retrying the delivery rather than the probe.

    A measurement that never reached the broker is indistinguishable from a check
    that never ran, so a delivery that still fails is raised, not swallowed.
    """
    messages = [message for batch in batches for message in batch]
    for attempt in range(1, DELIVERY_ATTEMPTS + 1):
        try:
            _publisher_for_process().publish_many(messages)
            return
        except Exception:
            if attempt == DELIVERY_ATTEMPTS:
                logger.error("publishing check results failed", check_id=check_id)
                raise
            logger.warning("publishing check results failed; retrying", check_id=check_id)
            time.sleep(DELIVERY_BACKOFF_SECONDS * attempt)


def _confirm_elsewhere(check: CheckDefinition, location: str, attempt: int) -> None:
    """The next attempt goes to the next location, or this one when it is the only
    one (FR25). It expires with the interval: a confirmation that cannot start
    before the next scheduled run is worth nothing (FR26)."""
    locations = check.locations
    position = locations.index(location) if location in locations else -1
    next_location = locations[(position + 1) % len(locations)]
    app.send_task(
        RUN_CHECK_TASK,
        kwargs={
            "definition": check.model_dump(mode="json"),
            "location": next_location,
            "attempt": attempt + 1,
        },
        queue=probe_queue(next_location),
        countdown=RETRY_DELAY_SECONDS,
        expires=check.interval_seconds,
        **time_limits_for(check.config),
    )


def _record_outcome(
    check: CheckDefinition, evidence: RunEvidence, location: str, observed_at: datetime
) -> None:
    if check.check_id is None:
        return
    app.send_task(
        RECORD_OUTCOME_TASK,
        kwargs={
            "check_id": check.check_id,
            "org_id": check.org_id,
            "project_id": check.project_id,
            "outcome": evidence.outcome,
            "run_id": evidence.run_id,
            "location": location,
            "observed_at": observed_at.isoformat(),
            "reason": evidence.error,
        },
        queue=SHARED_QUEUE,
    )


def _store_run_now_result(org_id: str, request_id: str, evidence: RunEvidence) -> None:
    _redis_for_process().set(
        run_now_result_key(org_id, request_id),
        evidence.model_dump_json(),
        ex=RUN_NOW_RESULT_TTL_SECONDS,
    )


def _judge_heartbeat(
    scheduled: ScheduledCheck, now: datetime, settings: Settings
) -> tuple[CheckDefinition, RunEvidence, list[tuple[str, str, bytes]]] | None:
    check = scheduled.definition
    try:
        config = parse_config(check.config)
    except ValidationError:
        return None
    if not isinstance(config, HeartbeatCheckConfig):
        return None
    outcome = heartbeat.outcome_of(
        period_seconds=scheduled.interval_seconds,
        grace_seconds=config.grace_seconds,
        last_ping_at=scheduled.last_ping_at,
        created_at=scheduled.created_at,
        now=now,
    )
    if outcome is None:
        return None

    location = settings.prober_region
    maintenance = _in_maintenance(check.maintenance, now)
    reason = (
        None
        if outcome == "up"
        else heartbeat.reason_for(
            period_seconds=scheduled.interval_seconds,
            grace_seconds=config.grace_seconds,
            last_ping_at=scheduled.last_ping_at,
            now=now,
        )
    )
    run_id = new_run_id()
    evidence = RunEvidence(
        run_id=run_id,
        check_id=check.check_id,
        check_type=check.type,
        location=location,
        attempt=1,
        final=True,
        maintenance=maintenance,
        outcome=outcome,
        started_at=now,
        # A heartbeat is not probed: there is no duration to report.
        steps=[
            StepEvidence(
                position=0,
                target="heartbeat",
                answers=[scheduled.last_ping_at.isoformat()] if scheduled.last_ping_at else [],
                passed=outcome == "up",
                error=reason,
            )
        ],
        error=reason,
    )
    measurement = RunMeasurement(
        check_id=check.check_id or "",
        check_type=check.type,
        run_id=run_id,
        outcome=evidence.outcome,
        attempt=1,
        final=True,
        maintenance=maintenance,
        location=location,
        total_seconds=None,
    )
    return check, evidence, _messages(check, measurement, evidence, time.time_ns(), settings)


def _with_engine(work: Any) -> Any:
    """One small engine per worker process, on one event loop of its own.

    Built on first use, after Celery's fork. A fresh engine per task opened a new
    connection for every state write, and those arrive once per run of every check.
    """
    global _engine, _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
    if _engine is None:
        _engine = create_async_engine(
            str(get_settings().postgres_dsn), pool_size=1, max_overflow=1, pool_pre_ping=True
        )
    return _loop.run_until_complete(work(_engine))
