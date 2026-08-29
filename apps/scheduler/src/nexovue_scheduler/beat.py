"""The beat scheduler: reads checks from Postgres, runs only while it is leader.

Two departures from stock Celery beat, both deliberate.

**The schedule comes from a table, not from code.** A customer adding a check in
the UI must see it run without anyone deploying anything, so the schedule is
re-read on a timer and the process holds no static entries.

**More than one of these runs.** Leadership is a Redis lock (`leader.py`); only
the holder schedules. A follower keeps an *empty* schedule, which is what stops
a failover firing a backlog: entries are built fresh when leadership is won, so
each one starts its interval from that moment instead of looking hours overdue.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

from celery.beat import ScheduleEntry, Scheduler
from celery.schedules import schedule as run_every
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexovue_scheduler.checks import CheckDefinition, load_enabled_checks
from nexovue_scheduler.leader import LeaderLock
from nexovue_scheduler.pollers.integrations import IntegrationDefinition, load_active_integrations
from nexovue_scheduler.settings import QUEUE_LIGHT, Settings, get_settings
from nexovue_scheduler.telemetry.logging import get_logger

# The task every check dispatches to. Named as a string rather than imported so
# beat does not pull in pycurl and Playwright to schedule work it never runs.
PROBE_TASK = "nexovue_scheduler.probes.run_check"

# Door 4b. Named as strings for the same reason: beat dispatches these and never
# calls them, so it has no business importing boto3 to schedule a poll.
POLL_TASKS = {
    "cloudwatch": "nexovue_scheduler.pollers.poll_cloudwatch",
    "postgres": "nexovue_scheduler.pollers.poll_postgres",
}

logger = get_logger(__name__)


class PostgresScheduler(Scheduler):
    """Celery's scheduler, reading its entries from `checks` while it holds the lock."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._settings: Settings = kwargs.pop("scheduler_settings", None) or get_settings()
        self._redis: Redis = kwargs.pop("redis_client", None) or Redis.from_url(
            str(self._settings.redis_url)
        )
        self._leader = LeaderLock(
            self._redis,
            key=self._settings.beat_leader_key,
            ttl_seconds=self._settings.beat_leader_ttl_seconds,
        )

        # Beat is a synchronous loop and the deployment's DSN names the async
        # driver, so the engine gets a loop of its own to run on. One loop for
        # the process, not one per refresh: connection pools do not survive
        # having the loop underneath them closed.
        self._loop = asyncio.new_event_loop()
        self._engine: AsyncEngine | None = None

        self._entries: dict[str, ScheduleEntry] = {}
        self._refresh_due_at = 0.0

        super().__init__(*args, **kwargs)
        # Never sleep past the point where leadership needs renewing, or a
        # leader would let its own lock expire while idling between checks.
        self.max_interval = self._settings.beat_leader_poll_seconds

    # --- Celery's interface -------------------------------------------------

    def setup_schedule(self) -> None:
        """Start with nothing. Entries appear only once this process is leader."""
        self._entries = {}

    @property
    def schedule(self) -> dict[str, ScheduleEntry]:
        return self._entries

    def tick(self, *args: Any, **kwargs: Any) -> float:
        """One beat iteration: renew leadership, refresh the schedule, dispatch.

        Returns how long to sleep. A follower sleeps the poll interval and does
        nothing else — it does not read Postgres, because nothing would act on
        what it read.
        """
        was_leader = self._leader.held
        if not self._leader.keep():
            if was_leader:
                # Lost it, usually because a tick overran the TTL. Drop the
                # entries so that winning it back rebuilds them from now rather
                # than replaying whatever came due while we were not scheduling.
                logger.warning("beat lost leadership", token=self._leader.token)
                self._entries = {}
            return float(self._settings.beat_leader_poll_seconds)

        if not was_leader:
            logger.info("beat became leader", token=self._leader.token)
            self._refresh_due_at = 0.0  # force an immediate load

        self._refresh_if_due()
        # Celery ships no type information, so the base class is untyped here.
        return float(super().tick(*args, **kwargs))

    def close(self) -> None:
        """Hand leadership back on a clean shutdown.

        Releasing beats waiting for the TTL: a redeploy otherwise leaves every
        check unscheduled for up to one lock lifetime for no reason.
        """
        try:
            self._leader.release()
        finally:
            self._shut_down_engine()
            super().close()

    def _shut_down_engine(self) -> None:
        """Dispose the engine and its loop, exactly once.

        Celery calls `close()` more than once on the way down — `sync()` does,
        and so does the beat service around it. Without this guard the second
        call ran a coroutine on the loop the first had already closed, and beat
        died with `RuntimeError: Event loop is closed` while it was shutting
        down anyway. Harmless in itself, but it buried the real reason for the
        shutdown under a stack trace that had nothing to do with it.
        """
        if self._loop.is_closed():
            return
        if self._engine is not None:
            self._loop.run_until_complete(self._engine.dispose())
            self._engine = None
        self._loop.close()

    # --- schedule maintenance ----------------------------------------------

    def _refresh_if_due(self) -> None:
        """Re-read `checks` when the refresh interval has elapsed."""
        now = self._monotonic()
        if now < self._refresh_due_at:
            return
        self._refresh_due_at = now + self._settings.beat_refresh_seconds

        try:
            engine = self._engine_or_create()
            checks = self._loop.run_until_complete(load_enabled_checks(engine))
            integrations = self._loop.run_until_complete(load_active_integrations(engine))
        except Exception:
            # Postgres being briefly unreachable must not empty the schedule:
            # the entries we already hold are still correct, and dropping them
            # would stop every check until it came back.
            logger.exception("beat could not read the schedule; keeping the current one")
            return

        self._entries = self._entries_for(checks) | self._poll_entries_for(integrations)

    def _entries_for(self, checks: list[CheckDefinition]) -> dict[str, ScheduleEntry]:
        """Build schedule entries, preserving the timing of ones already running.

        An unchanged check keeps its existing entry so that a refresh does not
        reset its interval — otherwise a refresh every 30s would stop a 60s
        check from ever coming due.
        """
        entries: dict[str, ScheduleEntry] = {}
        for check in checks:
            name = check.schedule_name
            existing = self._entries.get(name)
            interval = run_every(run_every=timedelta(seconds=check.interval_seconds))

            if existing is not None and existing.schedule == interval:
                entries[name] = existing
                continue

            entries[name] = ScheduleEntry(
                name=name,
                task=PROBE_TASK,
                schedule=interval,
                # The whole definition travels with the task, so the probe
                # worker needs no database. A check edited in the UI therefore
                # runs with its previous settings until the next refresh, which
                # is the trade `probes/__init__.py` documents.
                kwargs={
                    "check_id": str(check.id),
                    "org_id": str(check.org_id),
                    "project_id": str(check.project_id),
                    "check_type": check.type,
                    "target": check.target,
                    "config": check.config,
                },
                app=self.app,
            )
        return entries

    def _poll_entries_for(
        self, integrations: list[IntegrationDefinition]
    ) -> dict[str, ScheduleEntry]:
        """Schedule entries for door 4b, alongside the checks.

        An integration whose kind has no poller is skipped rather than raising:
        one unrecognised row must not stop every other customer's polls, and a
        kind can exist in the control plane before this service can run it.
        """
        entries: dict[str, ScheduleEntry] = {}
        for integration in integrations:
            task = POLL_TASKS.get(integration.kind)
            if task is None:
                continue
            name = integration.schedule_name
            interval = run_every(run_every=timedelta(seconds=integration.interval_seconds))

            existing = self._entries.get(name)
            if existing is not None and existing.schedule == interval:
                entries[name] = existing
                continue

            entries[name] = ScheduleEntry(
                name=name,
                task=task,
                schedule=interval,
                kwargs={
                    "integration_id": str(integration.id),
                    "org_id": str(integration.org_id),
                    "project_id": str(integration.project_id),
                    "config": integration.config,
                },
                options={"queue": QUEUE_LIGHT},
                app=self.app,
            )
        return entries

    def _engine_or_create(self) -> AsyncEngine:
        """The async engine, built on first use on this scheduler's own loop."""
        if self._engine is None:
            asyncio.set_event_loop(self._loop)
            self._engine = create_async_engine(
                str(self._settings.postgres_dsn),
                # Beat reads one small query on a timer. A wide pool would hold
                # connections open for a workload that needs one.
                pool_size=1,
                max_overflow=0,
                pool_pre_ping=True,
            )
        return self._engine

    @staticmethod
    def _monotonic() -> float:
        """Wall-clock independent, so an NTP correction cannot skip a refresh."""
        return time.monotonic()
