"""The Celery application for doors 2 and 4b.

Two queues, kept apart on purpose: `light` runs sub-second HTTP checks at high
concurrency, `browser` runs Playwright one at a time in a much larger container.
Sharing a pool would let one slow browser run starve the checks that are
supposed to fire every minute.

**There is no result backend.** Probe output is not a return value — a task
normalizes its result to the envelope and publishes it to Kafka itself, the same
way every other door reaches the broker. A result backend here would be a second
path for the same data, and the invariant is that there is only one.
"""

from __future__ import annotations

from celery import Celery

from nexovue_scheduler.settings import QUEUE_BROWSER, QUEUE_LIGHT, SERVICE_NAME, get_settings

_settings = get_settings()

app = Celery(SERVICE_NAME, broker=str(_settings.redis_url))

app.conf.update(
    # No backend: see the module docstring. Celery warns rather than fails when
    # a task returns something, and probe tasks return None for this reason.
    result_backend=None,
    task_default_queue=QUEUE_LIGHT,
    task_queues_late_binding=True,
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
    imports=("nexovue_scheduler.probes.tasks", "nexovue_scheduler.pollers.tasks"),
)

# Routing lives here rather than on each task, so the queue a task lands on is
# readable in one place and cannot drift per-decorator.
app.conf.task_routes = {
    "nexovue_scheduler.probes.browser.*": {"queue": QUEUE_BROWSER},
    "nexovue_scheduler.probes.*": {"queue": QUEUE_LIGHT},
    "nexovue_scheduler.pollers.*": {"queue": QUEUE_LIGHT},
}
