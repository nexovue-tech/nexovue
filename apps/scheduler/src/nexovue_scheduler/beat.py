"""Beat: reads the schedule from Postgres and adds each job as it comes due.

**The schedule comes from a table, not from code.** A customer adding a check in
the UI must see it run without anyone deploying anything, so the schedule is
re-read on a timer and the process holds no static entries.

**More than one of these runs.** Leadership is an advisory lock (`leader.py`);
only the holder schedules. A follower keeps an *empty* schedule, which is what
stops a failover firing a backlog: entries are built fresh when leadership is
won, so each one starts its interval from that moment instead of looking hours
overdue.

**A job is marked as fired only once it is in the table.** If Postgres refuses
the insert, the entry stays due and is added on the next tick — beat cannot
believe it scheduled something that was never queued.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from nexovue_platform.alerts.tasks import (
    DELIVERIES_QUEUE,
    EVALUATE_NEW_ERRORS_TASK,
    EVALUATE_SILENCE_TASK,
    EVALUATE_THRESHOLDS_TASK,
    PRUNE_ATTEMPTS_TASK,
    SWEEP_DELIVERIES_TASK,
)
from nexovue_platform.checks.task import (
    RUN_CHECK_TASK,
    SHARED_QUEUE,
    SWEEP_HEARTBEATS_TASK,
    SWEEP_UNATTACHED_FILES_TASK,
    probe_queue,
    time_limits_for,
)
from nexovue_platform.jobs import JobRequest
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.checks import ScheduledCheck, load_enabled_checks
from nexovue_scheduler.jobs import queue
from nexovue_scheduler.jobs.expiry import SWEEP_EXPIRED_TASK
from nexovue_scheduler.leader import LeaderLock
from nexovue_scheduler.pollers.integrations import IntegrationDefinition, load_active_integrations
from nexovue_scheduler.settings import Settings

# Task names come from the shared contract rather than imports, so beat does not
# pull in pycurl to schedule work it never runs.
PROBE_TASK: Final = RUN_CHECK_TASK

# A heartbeat is judged once a minute: the floor every check shares (spec D5).
HEARTBEAT_SWEEP_SECONDS: Final = 60
FILE_SWEEP_SECONDS: Final = 3600
HEARTBEAT_SWEEP_ENTRY: Final = "sweep:heartbeats"
FILE_SWEEP_ENTRY: Final = "sweep:unattached-files"

# F21. The delivery sweep is the outbox's safety net: whatever the fast path did
# not take is found within this long (ADR-013). The delivery log is pruned hourly.
DELIVERY_SWEEP_SECONDS: Final = 15
ATTEMPT_PRUNE_SECONDS: Final = 3600
DELIVERY_SWEEP_ENTRY: Final = "sweep:alert-deliveries"
# The service conditions, judged once a minute (ADR-012).
EVALUATION_SECONDS: Final = 60
THRESHOLD_EVALUATION_ENTRY: Final = "sweep:alert-thresholds"
SILENCE_EVALUATION_ENTRY: Final = "sweep:alert-silence"
NEW_ERROR_EVALUATION_ENTRY: Final = "sweep:alert-new-errors"
ATTEMPT_PRUNE_ENTRY: Final = "sweep:alert-attempts"

# The rows that lapse rather than being removed (ADR-020): sessions, links, results, jobs.
EXPIRY_SWEEP_SECONDS: Final = 60
EXPIRY_SWEEP_ENTRY: Final = "sweep:expired-rows"

# A sweep judges every heartbeat, or every unattached file, in one task.
SWEEP_SOFT_TIME_LIMIT_SECONDS: Final = 50
SWEEP_TIME_LIMIT_SECONDS: Final = 58

# A poll retries publishing twice (`pollers/tasks.py`).
POLL_ATTEMPTS: Final = 3

# How often a leader looks for due entries. A check is never more than this late.
TICK_SECONDS: Final = 1.0

# Door 4b. Named as strings for the same reason: beat dispatches these and never
# calls them, so it has no business importing boto3 to schedule a poll.
POLL_TASKS = {
    "cloudwatch": "nexovue_scheduler.pollers.poll_cloudwatch",
    "postgres": "nexovue_scheduler.pollers.poll_postgres",
}

logger = get_logger(__name__)


def _start_offset(check: ScheduledCheck) -> timedelta:
    """Where in its interval a check first fires: a stable hash of its id.

    Without it every check saved in the same refresh fires on the same second,
    every minute, and the probe workers see a thundering herd instead of a flow.
    """
    digest = int(hashlib.sha256(check.id.bytes).hexdigest()[:8], 16)
    return timedelta(seconds=digest % check.interval_seconds)


@dataclass(slots=True)
class Entry:
    """One thing beat adds on an interval, and when it is next due."""

    name: str
    request: JobRequest
    interval_seconds: int
    due_at: float


def _unchanged(existing: Entry | None, interval_seconds: int, request: JobRequest) -> bool:
    """Whether a running entry still matches the row it was built from.

    Both halves matter. The interval decides when it fires; the request carries
    the settings it fires with, and comparing only the first meant an edit to a
    target or a config was invisible until the interval happened to change too.
    """
    return (
        existing is not None
        and existing.interval_seconds == interval_seconds
        and existing.request == request
    )


class Beat:
    """Schedules every entry while it holds the leader lock."""

    def __init__(self, engine: AsyncEngine, leader: LeaderLock, settings: Settings) -> None:
        self._engine = engine
        self._leader = leader
        self._settings = settings
        self._entries: dict[str, Entry] = {}
        self._refresh_due_at = 0.0

        # What the heartbeat reports. Counters rather than gauges, because the
        # question that matters is "is it still moving", and a number that has
        # not changed since the last line answers it.
        self._heartbeat_due_at = 0.0
        self._refreshes = 0
        self._refresh_failures = 0
        self._ticks = 0
        self._enqueued = 0
        self._enqueue_failures = 0

    async def run(self, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                pause = await self.tick()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=pause)
        finally:
            # Releasing beats waiting for the connection to be noticed gone: a
            # redeploy otherwise leaves every check unscheduled meanwhile.
            await self._leader.release()

    async def tick(self) -> float:
        """One iteration: hold leadership, refresh the schedule, add what is due.

        Returns how long to sleep. A follower sleeps the poll interval and does
        nothing else — it does not read Postgres, because nothing would act on
        what it read.
        """
        was_leader = self._leader.held
        if not await self._leader.keep():
            if was_leader:
                # Drop the entries so that winning it back rebuilds them from now
                # rather than replaying whatever came due while we were not leading.
                logger.warning("beat lost leadership")
                self._entries = {}
            return float(self._settings.beat_leader_poll_seconds)

        if not was_leader:
            logger.info("beat became leader")
            self._refresh_due_at = 0.0  # force an immediate load

        await self._refresh_if_due()
        await self._enqueue_due()
        self._ticks += 1
        self._heartbeat_if_due()
        return TICK_SECONDS

    async def _enqueue_due(self) -> None:
        now = self._monotonic()
        due = [entry for entry in self._entries.values() if entry.due_at <= now]
        if not due:
            return
        try:
            await queue.enqueue_all(self._engine, [entry.request for entry in due])
        except Exception:
            self._enqueue_failures += 1
            logger.exception("beat could not add due jobs; they stay due", jobs=len(due))
            return
        for entry in due:
            entry.due_at = now + entry.interval_seconds
        self._enqueued += len(due)

    def _heartbeat_if_due(self) -> None:
        """Say what the scheduler is holding, on a timer.

        Emitted only by the leader — a follower holds nothing and would report
        zeros that read as a fault. The counters are what show movement: ticks
        that stop rising mean beat is wedged even though the process is up,
        which is exactly the state that went unnoticed for six minutes once.
        """
        now = self._monotonic()
        if now < self._heartbeat_due_at:
            return
        self._heartbeat_due_at = now + self._settings.beat_heartbeat_seconds

        checks = sum(1 for name in self._entries if name.startswith("check:"))
        polls = sum(1 for name in self._entries if name.startswith("integration:"))
        logger.info(
            "beat heartbeat",
            leader=True,
            ticks=self._ticks,
            scheduled_checks=checks,
            scheduled_polls=polls,
            jobs_added=self._enqueued,
            enqueue_failures=self._enqueue_failures,
            schedule_refreshes=self._refreshes,
            refresh_failures=self._refresh_failures,
        )

    # --- schedule maintenance ----------------------------------------------

    async def _refresh_if_due(self) -> None:
        """Re-read `checks` when the refresh interval has elapsed."""
        now = self._monotonic()
        if now < self._refresh_due_at:
            return
        self._refresh_due_at = now + self._settings.beat_refresh_seconds

        try:
            checks = await load_enabled_checks(
                self._engine, all_locations=self._settings.probe_locations
            )
            integrations = await load_active_integrations(self._engine)
        except Exception:
            # Postgres being briefly unreachable must not empty the schedule:
            # the entries we already hold are still correct, and dropping them
            # would stop every check until it came back.
            self._refresh_failures += 1
            logger.exception("beat could not read the schedule; keeping the current one")
            return

        self._refreshes += 1
        self._entries = (
            self._entries_for(checks) | self._sweep_entries() | self._poll_entries_for(integrations)
        )

    def _entries_for(self, checks: list[ScheduledCheck]) -> dict[str, Entry]:
        """One entry per check per location, preserving the timing of running ones.

        An unchanged check keeps its existing entry so that a refresh does not
        reset its interval — otherwise a refresh every 30s would stop a 60s check
        from ever coming due.

        "Unchanged" has to mean the whole definition, not just the interval. An
        entry carries the check's settings in its request, so keeping it because
        the interval matched meant an edited target or config never reached the
        worker — the one thing this table exists to make possible.

        A heartbeat has no entry of its own: nothing is probed, and the sweep
        judges every one of them together.
        """
        entries: dict[str, Entry] = {}
        now = self._monotonic()
        for check in checks:
            if check.is_heartbeat:
                continue
            served = [
                location
                for location in check.definition.locations
                if location in self._settings.probe_locations
            ]
            if len(served) < len(check.definition.locations):
                # A queue nothing consumes grows for ever; the browser queue did.
                logger.warning(
                    "check names a location this deployment does not have",
                    check_id=str(check.id),
                    unserved=sorted(set(check.definition.locations) - set(served)),
                )
            for location in served:
                name = check.schedule_name(location)
                request = JobRequest(
                    task=PROBE_TASK,
                    queue=probe_queue(location),
                    # The whole definition travels with the job, so the probe
                    # worker needs no database; see `probes/tasks.py`.
                    kwargs={
                        "definition": check.definition.model_dump(mode="json"),
                        "location": location,
                    },
                    org_id=check.definition.org_id,
                    # A run not started before the next is due is dropped, not
                    # queued behind it (FR26).
                    expires_seconds=check.interval_seconds,
                    **time_limits_for(check.definition.config),
                )
                existing = self._entries.get(name)
                if existing is not None and _unchanged(existing, check.interval_seconds, request):
                    entries[name] = existing
                    continue
                entries[name] = Entry(
                    name=name,
                    request=request,
                    interval_seconds=check.interval_seconds,
                    due_at=now + check.interval_seconds - _start_offset(check).total_seconds(),
                )
        return entries

    def _sweep_entries(self) -> dict[str, Entry]:
        """The periodic sweeps, kept across refreshes like any other entry."""
        wanted = {
            HEARTBEAT_SWEEP_ENTRY: (SWEEP_HEARTBEATS_TASK, HEARTBEAT_SWEEP_SECONDS, SHARED_QUEUE),
            FILE_SWEEP_ENTRY: (SWEEP_UNATTACHED_FILES_TASK, FILE_SWEEP_SECONDS, SHARED_QUEUE),
            DELIVERY_SWEEP_ENTRY: (
                SWEEP_DELIVERIES_TASK,
                DELIVERY_SWEEP_SECONDS,
                DELIVERIES_QUEUE,
            ),
            ATTEMPT_PRUNE_ENTRY: (PRUNE_ATTEMPTS_TASK, ATTEMPT_PRUNE_SECONDS, SHARED_QUEUE),
            THRESHOLD_EVALUATION_ENTRY: (
                EVALUATE_THRESHOLDS_TASK,
                EVALUATION_SECONDS,
                SHARED_QUEUE,
            ),
            SILENCE_EVALUATION_ENTRY: (EVALUATE_SILENCE_TASK, EVALUATION_SECONDS, SHARED_QUEUE),
            NEW_ERROR_EVALUATION_ENTRY: (
                EVALUATE_NEW_ERRORS_TASK,
                EVALUATION_SECONDS,
                SHARED_QUEUE,
            ),
            EXPIRY_SWEEP_ENTRY: (SWEEP_EXPIRED_TASK, EXPIRY_SWEEP_SECONDS, SHARED_QUEUE),
        }
        entries: dict[str, Entry] = {}
        now = self._monotonic()
        for name, (task, seconds, queue_name) in wanted.items():
            request = JobRequest(
                task=task,
                queue=queue_name,
                expires_seconds=seconds,
                soft_limit_seconds=SWEEP_SOFT_TIME_LIMIT_SECONDS,
                hard_limit_seconds=SWEEP_TIME_LIMIT_SECONDS,
            )
            existing = self._entries.get(name)
            if existing is not None and _unchanged(existing, seconds, request):
                entries[name] = existing
                continue
            entries[name] = Entry(
                name=name, request=request, interval_seconds=seconds, due_at=now + seconds
            )
        return entries

    def _poll_entries_for(self, integrations: list[IntegrationDefinition]) -> dict[str, Entry]:
        """Schedule entries for door 4b, alongside the checks.

        An integration whose kind has no poller is skipped rather than raising:
        one unrecognised row must not stop every other customer's polls, and a
        kind can exist in the control plane before this service can run it.
        """
        entries: dict[str, Entry] = {}
        now = self._monotonic()
        for integration in integrations:
            task = POLL_TASKS.get(integration.kind)
            if task is None:
                continue
            name = integration.schedule_name
            request = JobRequest(
                task=task,
                queue=SHARED_QUEUE,
                kwargs={
                    "integration_id": str(integration.id),
                    "org_id": str(integration.org_id),
                    "project_id": str(integration.project_id),
                    "config": integration.config,
                    # Sent alongside rather than inside `config`: it is ours,
                    # not the customer's, and the loader skips a row without one.
                    "external_id": str(integration.external_id),
                },
                org_id=str(integration.org_id),
                max_attempts=POLL_ATTEMPTS,
            )
            existing = self._entries.get(name)
            if existing is not None and _unchanged(existing, integration.interval_seconds, request):
                entries[name] = existing
                continue
            entries[name] = Entry(
                name=name,
                request=request,
                interval_seconds=integration.interval_seconds,
                due_at=now + integration.interval_seconds,
            )
        return entries

    @staticmethod
    def _monotonic() -> float:
        """Wall-clock independent, so an NTP correction cannot skip a run."""
        return time.monotonic()
