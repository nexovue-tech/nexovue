"""The Celery application for doors 2 and 4b.

**A worker is a probe location.** It consumes `light`, shared by every location
— pollers, state writes, sweeps — and `light.<its location>`, where runs from
that location are dispatched. Beat and the API name the queue on every probe
dispatch, which is what makes "run from Hyderabad" mean a worker in Hyderabad.

**There is no result backend.** Probe output is not a return value — a task
normalizes its result to the envelope and publishes it to Kafka itself, the same
way every other door reaches the broker. A result backend here would be a second
path for the same data, and the invariant is that there is only one.
"""

from __future__ import annotations

from celery import Celery
from kombu import Exchange, Queue
from nexovue_platform.alerts.tasks import (
    DELIVER_TASK,
    DELIVERIES_QUEUE,
    EVALUATE_NEW_ERRORS_TASK,
    EVALUATE_SILENCE_TASK,
    EVALUATE_THRESHOLDS_TASK,
    PRUNE_ATTEMPTS_TASK,
    SEND_TEST_TASK,
    SWEEP_DELIVERIES_TASK,
)
from nexovue_platform.checks.task import (
    RECORD_OUTCOME_TASK,
    SHARED_QUEUE,
    SWEEP_HEARTBEATS_TASK,
    SWEEP_UNATTACHED_FILES_TASK,
    probe_queue,
)

from nexovue_scheduler.settings import SERVICE_NAME, get_settings

_settings = get_settings()

app = Celery(SERVICE_NAME, broker=str(_settings.redis_url))

app.conf.update(
    # No backend: see the module docstring. Celery warns rather than fails when
    # a task returns something, and probe tasks return None for this reason.
    result_backend=None,
    task_default_queue=SHARED_QUEUE,
    # Each queue on its own exchange and key, which is exactly the queue Celery
    # creates when a producer names one it has not declared. Left to the
    # default, both bound the key `light` — and the API's Run now, naming
    # `light.local`, would publish to a binding nothing consumed.
    task_queues=tuple(
        Queue(name, Exchange(name, type="direct"), routing_key=name)
        # `deliveries` is declared so a producer and a consumer agree on it; only the
        # deliveries worker consumes it, and `scheduler-light` excludes it (`-X`).
        for name in (SHARED_QUEUE, probe_queue(_settings.prober_region), DELIVERIES_QUEUE)
    ),
    # Both ceilings come from settings so an operator can raise them without a
    # code change. A probe that hangs holds a worker slot, and a held slot is a
    # check that has silently stopped running — so the hard limit is a real
    # ceiling, not a formality.
    task_soft_time_limit=_settings.task_soft_time_limit_seconds,
    task_time_limit=_settings.task_hard_time_limit_seconds,
    # Acknowledge after the task runs, so a worker killed mid-probe leaves the
    # message for another. A repeated check costs one extra measurement; a lost
    # one is a blind minute.
    task_acks_late=True,
    # A probe's result is only useful while it is current. If a worker dies
    # holding one, re-running it is right; replaying a backlog of stale checks
    # an hour later is not.
    broker_transport_options={"visibility_timeout": _settings.task_hard_time_limit_seconds * 2},
    timezone="UTC",
    enable_utc=True,
    # Workers must import the task modules to register them. Beat imports this
    # app too and so imports them as well, which is harmless: it dispatches by
    # name and never calls them.
    imports=(
        "nexovue_scheduler.probes.tasks",
        "nexovue_scheduler.pollers.tasks",
        "nexovue_scheduler.alerts.tasks",
        "nexovue_scheduler.alerts.sweeps",
    ),
)

# Routing lives here rather than on each task, so the queue a task lands on is
# readable in one place and cannot drift per-decorator.
# A probe run is deliberately absent: every dispatch names its location's queue —
# beat, a confirmation, Run now. One that named none would fall to `light` and run
# from whichever location took it.
app.conf.task_routes = {
    RECORD_OUTCOME_TASK: {"queue": SHARED_QUEUE},
    SWEEP_HEARTBEATS_TASK: {"queue": SHARED_QUEUE},
    SWEEP_UNATTACHED_FILES_TASK: {"queue": SHARED_QUEUE},
    DELIVER_TASK: {"queue": DELIVERIES_QUEUE},
    SWEEP_DELIVERIES_TASK: {"queue": DELIVERIES_QUEUE},
    SEND_TEST_TASK: {"queue": DELIVERIES_QUEUE},
    PRUNE_ATTEMPTS_TASK: {"queue": SHARED_QUEUE},
    EVALUATE_THRESHOLDS_TASK: {"queue": SHARED_QUEUE},
    EVALUATE_SILENCE_TASK: {"queue": SHARED_QUEUE},
    EVALUATE_NEW_ERRORS_TASK: {"queue": SHARED_QUEUE},
    "nexovue_scheduler.pollers.*": {"queue": SHARED_QUEUE},
}
