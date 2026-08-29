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
from nexovue_scheduler.settings import Settings, get_settings
from nexovue_scheduler.telemetry.logging import get_logger

# The task every check dispatches to. Named as a string rather than imported so
# beat does not pull in pycurl and Playwright to schedule work it never runs.
PROBE_TASK = "nexovue_scheduler.probes.run_check"

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
            if self._engine is not None:
                self._loop.run_until_complete(self._engine.dispose())
            self._loop.close()
            super().close()

    # --- schedule maintenance ----------------------------------------------

    def _refresh_if_due(self) -> None:
        """Re-read `checks` when the refresh interval has elapsed."""
        now = self._monotonic()
        if now < self._refresh_due_at:
            return
        self._refresh_due_at = now + self._settings.beat_refresh_seconds

        try:
            checks = self._loop.run_until_complete(load_enabled_checks(self._engine_or_create()))
        except Exception:
            # Postgres being briefly unreachable must not empty the schedule:
            # the entries we already hold are still correct, and dropping them
            # would stop every check until it came back.
            logger.exception("beat could not read checks; keeping the current schedule")
            return

        self._entries = self._entries_for(checks)

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
