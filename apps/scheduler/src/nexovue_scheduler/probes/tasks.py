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

from nexovue_platform.telemetry.logging import get_logger

from nexovue_scheduler.celery_app import app
from nexovue_scheduler.probes import browser, http_check
from nexovue_scheduler.probes.result import ProbeResult
from nexovue_scheduler.publish import ResultPublisher, build_envelope
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# Per-check override, read from the `config` column. Bounded below by the task's
# own limits, so a check cannot ask to outlive the worker slot it occupies.
# Its own name, because the name is what Celery routes on. See run_browser_flow.
BROWSER_TASK: Final = "nexovue_scheduler.probes.browser.run_flow"

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


@app.task(
    name=BROWSER_TASK,
    bind=True,
    max_retries=2,
    soft_time_limit=get_settings().browser_task_soft_time_limit_seconds,
    time_limit=get_settings().browser_task_hard_time_limit_seconds,
)
def run_browser_flow(
    self: Any,
    *,
    check_id: str,
    org_id: str,
    project_id: str,
    check_type: str = browser.CHECK_TYPE_BROWSER,
    target: str,
    config: dict[str, Any] | None = None,
) -> None:
    """A browser flow, which is a separate task so that it lands on its own queue.

    The name is what routes it: `task_routes` sends
    `nexovue_scheduler.probes.browser.*` to the browser worker, and everything
    else under `probes.*` to the light one. Dispatching both check types through
    a single task name put browser flows on the light queue, where Playwright is
    deliberately not installed — so every flow failed on an import, and the two
    queues that exist to keep a slow browser away from sub-second checks were
    doing nothing.

    It also reports as logs rather than metrics: a flow's result is a narrative
    of steps, which is what the frozen contract for this door carries.
    """
    settings = get_settings()
    # Divided across the steps rather than applied to each, so the whole flow
    # fits inside the task's own limit. A per-step timeout that ignores how many
    # steps there are is how a flow gets killed mid-run.
    timeout = _step_budget(config or {}, settings.browser_task_soft_time_limit_seconds)
    observed_at = time.time_ns()

    flow = browser.perform(
        check_id,
        target,
        timeout,
        config or {},
        private_allowed=settings.private_probe_targets_allowed,
    )
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
        self,
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
        return http_check.perform(
            check_id, target, timeout, private_allowed=get_settings().private_probe_targets_allowed
        )
    raise ValueError(f"check {check_id}: no prober for type {check_type!r}")


def _step_budget(config: dict[str, Any], flow_budget_seconds: int) -> int:
    """How long any one step may take, so the whole flow fits its task limit.

    The budget is shared: ten steps get a tenth each. A step still cannot exceed
    what the customer asked for, and never gets less than a second — a timeout
    of zero would fail every step instantly and report the site as down.
    """
    steps = config.get("steps")
    count = len(steps) if isinstance(steps, list) and steps else 1
    # Leave a little of the budget for starting the browser and publishing.
    share = int(flow_budget_seconds * 0.8) // count
    return max(1, min(share, _timeout_for(config, flow_budget_seconds)))


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
