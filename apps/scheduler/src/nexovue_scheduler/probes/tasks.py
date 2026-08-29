"""The probe tasks: run one check, publish what it measured.

Beat hands the whole check definition over rather than an id, so this worker
never touches Postgres. A database query per check per interval would put the
control plane on the path of every measurement, and a probe that cannot run
because Postgres is briefly busy is a blind minute for that customer.

The cost is staleness bounded by the beat refresh interval: a check edited in
the UI runs with its previous settings for up to that long. That is the right
trade for a check that repeats every minute anyway.
"""

from __future__ import annotations

import time
from typing import Any, Final

from nexovue_scheduler.celery_app import app
from nexovue_scheduler.probes import browser, http_check
from nexovue_scheduler.probes.result import ProbeResult
from nexovue_scheduler.publish import ResultPublisher, build_envelope
from nexovue_scheduler.settings import get_settings
from nexovue_scheduler.telemetry.logging import get_logger

logger = get_logger(__name__)

# Per-check override, read from the `config` column. Bounded below by the task's
# own limits, so a check cannot ask to outlive the worker slot it occupies.
CONFIG_TIMEOUT_SECONDS: Final = "timeout_seconds"
DEFAULT_TIMEOUT_SECONDS: Final = 10

NANOS_PER_SECOND: Final = 1_000_000_000

_publisher: ResultPublisher | None = None


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


@app.task(name="nexovue_scheduler.probes.run_check", bind=True, max_retries=2)
def run_check(
    self: Any,
    *,
    check_id: str,
    org_id: str,
    project_id: str,
    check_type: str,
    target: str,
    config: dict[str, Any] | None = None,
) -> None:
    """Run one check and publish its result.

    A failing target is a successful task: down is a measurement, and the result
    is published exactly as an up one is. What does retry is a failure to
    *deliver* the measurement, because an unpublished reading is indistinguishable
    from a check that never ran.
    """
    settings = get_settings()
    timeout = _timeout_for(config or {}, settings.task_soft_time_limit_seconds)
    observed_at = time.time_ns()

    if check_type == browser.CHECK_TYPE_BROWSER:
        _run_browser_flow(
            self,
            check_id=check_id,
            org_id=org_id,
            project_id=project_id,
            target=target,
            timeout=timeout,
            config=config or {},
            observed_at=observed_at,
            settings=settings,
        )
        return

    result = _probe(check_id, check_type, target, timeout)
    logger.info(
        "check completed",
        check_id=check_id,
        check_type=check_type,
        up=result.up,
        status_code=result.status_code,
        duration_seconds=round(result.total_seconds, 4),
        error=result.error,
    )

    _publish_or_retry(
        self,
        build_envelope(
            result,
            org_id=org_id,
            project_id=project_id,
            observed_at_unix_nano=observed_at,
            environment=settings.env,
            region=settings.prober_region,
        ),
        org_id=org_id,
        check_id=check_id,
    )


def _run_browser_flow(
    task: Any,
    *,
    check_id: str,
    org_id: str,
    project_id: str,
    target: str,
    timeout: int,
    config: dict[str, Any],
    observed_at: int,
    settings: Any,
) -> None:
    """A browser flow, which reports as logs rather than metrics.

    A flow's result is a narrative — which step failed, and why — so the frozen
    contract for this door carries SIGNAL_LOGS. That is the only reason it does
    not share the path above.
    """
    flow = browser.perform(check_id, target, timeout, config)
    logger.info(
        "browser flow completed",
        check_id=check_id,
        up=flow.up,
        steps_passed=flow.steps_passed,
        steps_total=flow.steps_total,
        duration_seconds=round(flow.total_seconds, 4),
        failed_step=flow.failure.name if flow.failure else None,
    )
    _publish_or_retry(
        task,
        browser.build_envelope(
            flow,
            org_id=org_id,
            project_id=project_id,
            observed_at_unix_nano=observed_at,
            environment=settings.env,
            region=settings.prober_region,
        ),
        org_id=org_id,
        check_id=check_id,
        topic=settings.kafka_logs_topic,
    )


def _publish_or_retry(
    task: Any, envelope: bytes, *, org_id: str, check_id: str, topic: str | None = None
) -> None:
    """Deliver a result, or retry until it lands.

    A measurement that never reached the broker is indistinguishable from a
    check that never ran, so this is the one failure the task does not swallow.
    """
    try:
        _publisher_for_process().publish(envelope, org_id=org_id, topic=topic)
    except Exception as failure:
        logger.warning("publishing a check result failed; retrying", check_id=check_id)
        raise task.retry(exc=failure, countdown=5) from failure


def _probe(check_id: str, check_type: str, target: str, timeout: int) -> ProbeResult:
    """Dispatch to the prober for this check's type.

    An unknown type raises rather than defaulting: a check nobody can run must
    be visible as a failure, not silently absent from the graph.
    """
    if check_type == http_check.CHECK_TYPE_HTTP:
        return http_check.perform(check_id, target, timeout)
    raise ValueError(f"check {check_id}: no prober for type {check_type!r}")


def _timeout_for(config: dict[str, Any], ceiling: int) -> int:
    """The check's timeout, bounded by what the worker will allow.

    A probe is killed at the task's own soft limit regardless, so a check
    configured to wait longer would be cut off mid-request and report a failure
    that is ours rather than the target's.
    """
    requested = config.get(CONFIG_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS)
    try:
        seconds = int(requested)
    except (TypeError, ValueError):
        seconds = DEFAULT_TIMEOUT_SECONDS
    return max(1, min(seconds, ceiling))
