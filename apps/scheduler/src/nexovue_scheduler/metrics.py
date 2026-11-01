"""What a monitor reads about the scheduler (ADR-021), served by `health.py`.

Two kinds. Every process reports its own: how long since its loop beat, and —
beat — whether it leads; workers, how their jobs ended. Beat's leader alone
reads the queues every `MEASURE_SECONDS`, because they are the same rows
whichever process asks: jobs ready per queue and how long the oldest has
waited, alerts waiting to be delivered, erasures overdue, and detection's
progress — how old the newest settled minute is, how many projects it has
stopped judging, how long the stalest has waited, and the events it recorded
about nexovue's own intake (F6, C8). Those are read from Postgres, not counted
where they happen, because the sweep runs in a worker's child process, whose
metrics no scrape sees.

The queue readings are across every organization on purpose: they are counts of
our own work, and no label names a tenant. A project detection has stopped
judging is named in the scheduler's log, never here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from nexovue_platform.detection import NOT_RUNNING_AFTER
from prometheus_client import Counter, Gauge, disable_created_metrics
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from nexovue_scheduler.detection.settle import EXPIRED_EVENT, GUARD_EVENT
from nexovue_scheduler.erasure import owed

MEASURE_SECONDS: Final = 15.0

# A `_created` timestamp beside every counter is noise to every monitor we read with.
disable_created_metrics()  # type: ignore[no-untyped-call]

# The claim's own idea of ready (`jobs/queue._CLAIM`): due, not leased, not
# expired, with an attempt left. Not ready is not waiting.
_JOBS_READY: Final = text(
    """
    SELECT queue, count(*) AS ready, EXTRACT(EPOCH FROM now() - min(run_after)) AS oldest
    FROM jobs
    WHERE run_after <= now()
      AND (lease_until IS NULL OR lease_until < now())
      AND (expires_at IS NULL OR expires_at > now())
      AND attempts < max_attempts
    GROUP BY queue
    """
)

# The outbox claim's own predicate (`alerts/outbox.py`): due and pending, or a
# delivery whose lease lapsed.
_ALERTS_WAITING: Final = text(
    """
    SELECT count(*) AS waiting,
           COALESCE(EXTRACT(EPOCH FROM now() - min(deliver_after)), 0) AS oldest
    FROM notifications
    WHERE (state = 'pending' AND deliver_after <= now())
       OR (state = 'delivering' AND lease_until < now())
    """
)

# The API's *not running*, install-wide: a project detection is expected to judge
# whose run has not completed for `NOT_RUNNING_AFTER`, counted from the later of
# its last run and when it became expected (D34). The lag is the stalest such
# clock, healthy projects included, so it reads small while all is well.
_DETECTION_PROJECTS: Final = text(
    """
    SELECT count(*) FILTER (
               WHERE behind_since < now() - CAST(:not_running AS interval)
           ) AS behind,
           COALESCE(EXTRACT(EPOCH FROM now() - min(behind_since)), 0) AS oldest
    FROM (
        SELECT GREATEST(last_completed_at, expected_since) AS behind_since
        FROM detection_project_runs
        WHERE expected_since IS NOT NULL
    ) AS expected
    """
)
# Null when no sweep has settled a minute: read as infinitely old.
_DETECTION_SETTLED_AGE: Final = text(
    "SELECT EXTRACT(EPOCH FROM now() - settled_minute) FROM detection_sweep"
)
# The operator's alert stays up this long after each event, as one page per event.
DETECTION_EVENTS_RECENT_SECONDS: Final = 15 * 60
DETECTION_EVENT_KINDS: Final = (GUARD_EVENT, EXPIRED_EVENT)
_DETECTION_EVENTS: Final = text(
    """
    SELECT kind, count(*) AS recent FROM detection_events
    WHERE at > now() - make_interval(secs => :recent_seconds)
    GROUP BY kind
    """
)

LOOP_SILENT: Final = Gauge(
    "nexovue_scheduler_loop_silent_seconds", "Seconds since this process's loop last beat."
)
LEADING: Final = Gauge(
    "nexovue_scheduler_beat_leading", "1 while this beat holds the leader lock, else 0."
)
JOBS_ADDED: Final = Counter("nexovue_scheduler_jobs_added", "Jobs beat added to the queue.")
ENQUEUE_FAILURES: Final = Counter(
    "nexovue_scheduler_enqueue_failures", "Times beat could not add what was due."
)
JOBS_ENDED: Final = Counter(
    "nexovue_scheduler_jobs_ended", "Jobs this worker ran, by how they ended.", ["outcome"]
)
JOBS_READY: Final = Gauge(
    "nexovue_scheduler_jobs_ready", "Jobs due and unclaimed, per queue (leader only).", ["queue"]
)
OLDEST_READY: Final = Gauge(
    "nexovue_scheduler_oldest_ready_job_seconds",
    "How long the oldest ready job has waited, per queue (leader only).",
    ["queue"],
)
ALERTS_WAITING: Final = Gauge(
    "nexovue_scheduler_alerts_waiting", "Alert notifications due and undelivered (leader only)."
)
OLDEST_ALERT: Final = Gauge(
    "nexovue_scheduler_oldest_alert_waiting_seconds",
    "How long the oldest undelivered alert has been due (leader only).",
)
ERASURES_OVERDUE: Final = Gauge(
    "nexovue_scheduler_erasures_overdue",
    "Deleted organizations whose erasure is past its three days (leader only).",
)
DETECTION_SETTLED_AGE: Final = Gauge(
    "nexovue_scheduler_detection_settled_minute_age_seconds",
    "How old the newest minute detection has settled is; +Inf before any (leader only).",
)
DETECTION_PROJECTS_BEHIND: Final = Gauge(
    "nexovue_scheduler_detection_projects_behind",
    "Projects detection should judge whose job has completed no run for 3 minutes (leader only).",
)
DETECTION_OLDEST_LAG: Final = Gauge(
    "nexovue_scheduler_detection_oldest_lag_seconds",
    "How long the project longest without a completed detection run has waited (leader only).",
)
DETECTION_EVENTS_RECENT: Final = Gauge(
    "nexovue_scheduler_detection_events_recent",
    "detection_events rows of each kind recorded in the last 15 minutes (leader only).",
    ["kind"],
)

# How a worker's job ended — the label values JOBS_ENDED takes.
FINISHED: Final = "finished"
RETRIED: Final = "retried"
KILLED: Final = "killed"


@dataclass(frozen=True, slots=True)
class QueueReadings:
    jobs_ready: dict[str, int] = field(default_factory=dict)
    oldest_ready_seconds: dict[str, float] = field(default_factory=dict)
    alerts_waiting: int = 0
    oldest_alert_waiting_seconds: float = 0.0
    erasures_overdue: int = 0
    detection_settled_minute_age_seconds: float = math.inf
    detection_projects_behind: int = 0
    detection_oldest_lag_seconds: float = 0.0
    detection_events_recent: dict[str, int] = field(default_factory=dict)


async def read_queues(connection: AsyncConnection, *, now: datetime) -> QueueReadings:
    jobs = (await connection.execute(_JOBS_READY)).all()
    alerts = (await connection.execute(_ALERTS_WAITING)).one()
    overdue = await owed.overdue(connection, now=now)
    projects = (
        await connection.execute(_DETECTION_PROJECTS, {"not_running": NOT_RUNNING_AFTER})
    ).one()
    settled_age = await connection.scalar(_DETECTION_SETTLED_AGE)
    events = await connection.execute(
        _DETECTION_EVENTS, {"recent_seconds": DETECTION_EVENTS_RECENT_SECONDS}
    )
    recent = dict.fromkeys(DETECTION_EVENT_KINDS, 0) | {row.kind: int(row.recent) for row in events}
    return QueueReadings(
        jobs_ready={row.queue: int(row.ready) for row in jobs},
        oldest_ready_seconds={row.queue: float(row.oldest) for row in jobs},
        alerts_waiting=int(alerts.waiting),
        oldest_alert_waiting_seconds=float(alerts.oldest),
        erasures_overdue=overdue.count,
        detection_settled_minute_age_seconds=(
            math.inf if settled_age is None else float(settled_age)
        ),
        detection_projects_behind=int(projects.behind),
        detection_oldest_lag_seconds=float(projects.oldest),
        detection_events_recent=recent,
    )


def publish(readings: QueueReadings) -> None:
    # Cleared first: a queue that has emptied stops being reported, rather
    # than repeating its last count.
    for gauge in (JOBS_READY, OLDEST_READY, DETECTION_EVENTS_RECENT):
        gauge.clear()
    for queue, ready in readings.jobs_ready.items():
        JOBS_READY.labels(queue=queue).set(ready)
        OLDEST_READY.labels(queue=queue).set(readings.oldest_ready_seconds[queue])
    ALERTS_WAITING.set(readings.alerts_waiting)
    OLDEST_ALERT.set(readings.oldest_alert_waiting_seconds)
    ERASURES_OVERDUE.set(readings.erasures_overdue)
    DETECTION_SETTLED_AGE.set(readings.detection_settled_minute_age_seconds)
    DETECTION_PROJECTS_BEHIND.set(readings.detection_projects_behind)
    DETECTION_OLDEST_LAG.set(readings.detection_oldest_lag_seconds)
    for kind, recent in readings.detection_events_recent.items():
        DETECTION_EVENTS_RECENT.labels(kind=kind).set(recent)


def forget_queues() -> None:
    """A beat that stops leading stops speaking for the queues."""
    for gauge in (JOBS_READY, OLDEST_READY, DETECTION_EVENTS_RECENT):
        gauge.clear()
    for single in (
        ALERTS_WAITING,
        OLDEST_ALERT,
        ERASURES_OVERDUE,
        DETECTION_SETTLED_AGE,
        DETECTION_PROJECTS_BEHIND,
        DETECTION_OLDEST_LAG,
    ):
        single.set(0)
