"""What a monitor reads about the scheduler (ADR-021), served by `health.py`.

Two kinds. Every process reports its own: how long since its loop beat, and —
beat — whether it leads; workers, how their jobs ended. Beat's leader alone
reads the queues every `MEASURE_SECONDS`, because they are the same rows
whichever process asks: jobs ready per queue and how long the oldest has
waited, alerts waiting to be delivered, and erasures overdue.

The queue readings are across every organization on purpose: they are counts of
our own work, and no label names a tenant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from prometheus_client import Counter, Gauge, disable_created_metrics
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

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
# F6, design 2.1 item 1: detection held every project for nexovue's own intake, or
# released such a hold at its limit. Whichever worker ran the sweep counts it.
DETECTION_INTAKE_EVENTS: Final = Counter(
    "nexovue_scheduler_detection_intake_events",
    "Times detection held every project for nexovue's intake, or released a hold at its limit.",
    ["kind"],
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


async def read_queues(connection: AsyncConnection, *, now: datetime) -> QueueReadings:
    jobs = (await connection.execute(_JOBS_READY)).all()
    alerts = (await connection.execute(_ALERTS_WAITING)).one()
    overdue = await owed.overdue(connection, now=now)
    return QueueReadings(
        jobs_ready={row.queue: int(row.ready) for row in jobs},
        oldest_ready_seconds={row.queue: float(row.oldest) for row in jobs},
        alerts_waiting=int(alerts.waiting),
        oldest_alert_waiting_seconds=float(alerts.oldest),
        erasures_overdue=overdue.count,
    )


def publish(readings: QueueReadings) -> None:
    # Cleared first: a queue that has emptied stops being reported, rather
    # than repeating its last count.
    JOBS_READY.clear()
    OLDEST_READY.clear()
    for queue, ready in readings.jobs_ready.items():
        JOBS_READY.labels(queue=queue).set(ready)
        OLDEST_READY.labels(queue=queue).set(readings.oldest_ready_seconds[queue])
    ALERTS_WAITING.set(readings.alerts_waiting)
    OLDEST_ALERT.set(readings.oldest_alert_waiting_seconds)
    ERASURES_OVERDUE.set(readings.erasures_overdue)


def forget_queues() -> None:
    """A beat that stops leading stops speaking for the queues."""
    for gauge in (JOBS_READY, OLDEST_READY):
        gauge.clear()
    for single in (ALERTS_WAITING, OLDEST_ALERT, ERASURES_OVERDUE):
        single.set(0)
