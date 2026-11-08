"""F19's three jobs, and `queue_jobs`, which hands them out (design, *What each job does*).

- **`judge_slos`**, on `slo`, one project each settled minute: where each target
  stands now: when it was worked out, the last minute its source sent anything, and
  the longest quiet stretch of the last 7 days, so a silent service reads **No recent
  requests** and never looks healthy (BR5). Burn tiers join in Step 4.
- **`count_slos`**, on `slo_counts`, by age: a project's recent days, rewritten until
  they are 7 days old and then final (D2, BR13).
- **`read_slo_history`**, on `slo_history`, one target: its past days, back as far as
  the source holds, when it is created or what it counts changes.

**Recounting by version** (FR28, AC7). A count never writes over a final day, nor over
a day counted under a newer definition. A history read under a newer definition
rewrites every day back to the recount limit, final or not, and marks a final day
whose figures moved as changed by the definition, so its month says so (BR13). Past
the limit a day stays as it was counted, under its old definition (AC45), and a day
first read there says nexovue's losses in it are not known (AC69).

**Who gets a job** is read from the targets themselves, not from detection's active
projects, which drop a project silent for a day (C8): a 27-day silence is still judged
(AC8). Projects go longest-waiting first, so whoever was skipped leads the next minute
(AC65). A failure to queue is the caller's to log; it must not stop detection (AC95).

**Each kind's source** is read once per project, one statement per source (`store`),
and turned into one target's minutes by `counts`. A check is read at its own cadence:
a judging reads it only once its interval has turned since the last (design, *What
each job does*).

**Order and repeats.** A project's judging holds a session lock, a target is skipped
when the minute is not newer than its last, and the status upsert refuses an older
minute, so two overlapping runs, or a late one, never move a target back (AC66).

Every statement binds `org_id`, read from the project's own row (F6's m6), except the
four named in `test_scheduler_postgres_tenancy_structure.py`: the three that hand out
work across every organization, and the one that finds a target's project.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from nexovue_platform import slo
from nexovue_platform.detection import DEFAULT_TIMEZONE
from nexovue_platform.jobs import JobRequest
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.detection import holds
from nexovue_scheduler.detection.projects import session_lock
from nexovue_scheduler.detection.store import ProjectBinding, bind_project
from nexovue_scheduler.jobs.queue import enqueue_all
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings
from nexovue_scheduler.slos import counts
from nexovue_scheduler.slos.store import (
    CheckMinute,
    LatencyMinute,
    MinuteCounts,
    SloReader,
    SloStore,
    connect_store,
)

logger = get_logger(__name__)

# A job not started within a minute is overtaken by the next minute's.
JOB_EXPIRES_SECONDS: Final = 60
# The claim takes the oldest `run_after` first; one transaction's jobs share its `now()`,
# so each is a millisecond after the one before, in the order given.
ORDER_STEP_SECONDS: Final = 0.001
# As detection's project job (`project_job.py`).
JUDGE_SOFT_LIMIT_SECONDS: Final = 20
JUDGE_HARD_LIMIT_SECONDS: Final = 30
# Guesses (design, *What each job does*).
COUNTS_SOFT_LIMIT_SECONDS: Final = 120
COUNTS_HARD_LIMIT_SECONDS: Final = 150
# The last day a judging reads, by the minute (design, *Performance and scale*).
JUDGE_LOOKBACK: Final = timedelta(hours=24)
# Today and yesterday are recounted when their row is older than this; older days not
# yet final, when theirs is older than an hour (design, *What each job does*).
RECENT_DAYS_DUE_AFTER: Final = timedelta(minutes=5)
OLDER_DAYS_DUE_AFTER: Final = timedelta(hours=1)
# The 7 days FR10a's attention limit reads the longest quiet stretch from.
QUIET_LOOKBACK_DAYS: Final = 7
# `service_stats_1m` holds at most 35 days on any plan (FR27): how far a service
# target's history reaches. An endpoint or check target reaches the plan's derived
# retention.
SERVICE_ROLLUP_DAYS: Final = 35
# Where a check names no location, it runs from every location the deployment has
# (`checks.py`); the task passes how many that is.
ONE_LOCATION: Final = 1
_MINUTE: Final = timedelta(minutes=1)
_JUDGE_LOCK: Final = "slo-judge"
_COUNT_LOCK: Final = "slo-count"
_HISTORY_LOCK: Final = "slo-history"

# ------------------------------------------------------------- handing out work

# Every organization's live projects that hold a live target, longest-waiting first: a
# target never judged waits longest (design, *Which projects get a job*, *Overload*).
_PROJECTS_TO_JUDGE: Final = text(
    """
    SELECT t.org_id, t.project_id,
           min(coalesce(s.evaluated_minute, '-infinity'::timestamptz)) AS oldest
    FROM slo_targets AS t
    JOIN project_retention AS live ON live.org_id = t.org_id AND live.project_id = t.project_id
    LEFT JOIN slo_status AS s ON s.org_id = t.org_id AND s.slo_id = t.id
    WHERE t.deleted_at IS NULL
    GROUP BY t.org_id, t.project_id
    ORDER BY oldest, t.org_id, t.project_id
    """
)
# Every organization's projects whose recent days are due a count: today missing or
# stale, yesterday stale and not final, or any day not final and an hour old. By what is
# due, not by the clock, so a skipped run is done by the next (AC65).
_PROJECTS_TO_COUNT: Final = text(
    """
    SELECT t.org_id, t.project_id
    FROM slo_targets AS t
    JOIN project_retention AS live ON live.org_id = t.org_id AND live.project_id = t.project_id
    JOIN projects AS p ON p.org_id = t.org_id AND p.id = t.project_id
    WHERE t.deleted_at IS NULL AND t.first_event_at IS NOT NULL
      AND (
        NOT EXISTS (
            SELECT 1 FROM slo_daily_counts AS today
            WHERE today.org_id = t.org_id AND today.slo_id = t.id
              AND today.day = CAST(timezone(coalesce(p.timezone, :zone), :now) AS date)
              AND today.computed_at >= :recent)
        OR EXISTS (
            SELECT 1 FROM slo_daily_counts AS day
            WHERE day.org_id = t.org_id AND day.slo_id = t.id AND NOT day.final
              AND (day.computed_at < :older
                   OR (day.computed_at < :recent
                       AND day.day >= CAST(timezone(coalesce(p.timezone, :zone), :now) AS date)
                                      - 1)))
      )
    GROUP BY t.org_id, t.project_id
    ORDER BY t.org_id, t.project_id
    """
)
# Every organization's targets whose history is behind their definition: just created,
# changed, or a read that crashed between its commit and its queueing (AC64).
_TARGETS_TO_READ: Final = text(
    """
    SELECT t.org_id, t.id
    FROM slo_targets AS t
    JOIN project_retention AS live ON live.org_id = t.org_id AND live.project_id = t.project_id
    WHERE t.deleted_at IS NULL AND t.history_read_version < t.definition_version
    ORDER BY t.updated_at, t.id
    """
)


@dataclass(frozen=True, slots=True)
class ProjectToJudge:
    org_id: uuid.UUID
    project_id: uuid.UUID


def judge_requests(projects: Sequence[ProjectToJudge], settled: datetime) -> list[JobRequest]:
    """One judging job per project, in the order given, each up to `settled`."""
    return [
        JobRequest(
            task=slo.JUDGE_SLOS_TASK,
            queue=slo.SLO_QUEUE,
            kwargs={"project_id": str(project.project_id), "settled_minute": settled.isoformat()},
            org_id=str(project.org_id),
            countdown_seconds=position * ORDER_STEP_SECONDS,
            expires_seconds=JOB_EXPIRES_SECONDS,
            soft_limit_seconds=JUDGE_SOFT_LIMIT_SECONDS,
            hard_limit_seconds=JUDGE_HARD_LIMIT_SECONDS,
        )
        for position, project in enumerate(projects)
    ]


def count_request(org_id: object, project_id: object) -> JobRequest:
    return JobRequest(
        task=slo.COUNT_SLOS_TASK,
        queue=slo.SLO_COUNTS_QUEUE,
        kwargs={"project_id": str(project_id)},
        org_id=str(org_id),
        expires_seconds=JOB_EXPIRES_SECONDS,
        soft_limit_seconds=COUNTS_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=COUNTS_HARD_LIMIT_SECONDS,
    )


async def queue_jobs(engine: AsyncEngine, settled: datetime, *, now: datetime) -> int:
    """Queue this minute's SLO work: a judging per project with a live target, the counts
    that are due, and the history reads that are behind. Returns the projects judged."""
    keys = {
        "now": now,
        "recent": now - RECENT_DAYS_DUE_AFTER,
        "older": now - OLDER_DAYS_DUE_AFTER,
        "zone": DEFAULT_TIMEZONE,
    }
    async with engine.connect() as connection:
        judged = [
            ProjectToJudge(row.org_id, row.project_id)
            for row in await connection.execute(_PROJECTS_TO_JUDGE)
        ]
        due = list(await connection.execute(_PROJECTS_TO_COUNT, keys))
        behind = list(await connection.execute(_TARGETS_TO_READ))
    requests = [
        *judge_requests(judged, settled),
        *(count_request(row.org_id, row.project_id) for row in due),
        *(slo.history_request(row.org_id, row.id) for row in behind),
    ]
    await enqueue_all(engine, requests)
    return len(judged)


# ----------------------------------------------------------------- the targets

_SETTINGS: Final = text("SELECT timezone FROM projects WHERE org_id = :org_id AND id = :project_id")
# The live targets of one project, enabled and disabled, oldest status first.
_TARGETS: Final = text(
    """
    SELECT t.id, t.kind, t.service_name, t.endpoint_name, t.boundary_index,
           t.left_out_endpoints, t.check_id, t.first_event_at, t.definition_version,
           c.interval_seconds AS check_interval_seconds, c.locations AS check_locations,
           c.enabled AS check_enabled,
           s.evaluated_minute, s.last_event_minute
    FROM slo_targets AS t
    LEFT JOIN slo_status AS s ON s.org_id = t.org_id AND s.slo_id = t.id
    LEFT JOIN checks AS c ON c.org_id = t.org_id AND c.id = t.check_id
    WHERE t.org_id = :org_id AND t.project_id = :project_id AND t.deleted_at IS NULL
    ORDER BY s.evaluated_minute NULLS FIRST, t.id
    """
)
# Each target's stored days since `since`, oldest first: the last event and quiet stretches.
_RECENT_DAYS: Final = text(
    """
    SELECT slo_id, day, total, quiet_minutes, lost_minutes, quiet_leading_minutes,
           quiet_trailing_minutes, longest_quiet_minutes
    FROM slo_daily_counts
    WHERE org_id = :org_id AND slo_id = ANY(:ids) AND day >= :since
    ORDER BY slo_id, day
    """
)
# The newest day each target held an event: where a silence longer than a day began.
_LAST_BUSY_DAYS: Final = text(
    """
    SELECT DISTINCT ON (slo_id) slo_id, day, quiet_trailing_minutes, computed_at
    FROM slo_daily_counts
    WHERE org_id = :org_id AND slo_id = ANY(:ids) AND total > 0
    ORDER BY slo_id, day DESC
    """
)
_FIRST_EVENT: Final = text(
    """
    UPDATE slo_targets
    SET first_event_at = CAST(:first AS timestamptz)
    WHERE org_id = :org_id AND id = :id
      AND (first_event_at IS NULL OR first_event_at > CAST(:first AS timestamptz))
    """
)
# Never an older minute over a newer one (AC66), and the last event only moves forward.
_SAVE_STATUS: Final = text(
    """
    INSERT INTO slo_status (slo_id, org_id, project_id, evaluated_minute, last_event_minute,
                            longest_quiet_7d)
    VALUES (:id, :org_id, :project_id, :minute, :last_event, :longest_quiet)
    ON CONFLICT (slo_id) DO UPDATE
    SET evaluated_minute = EXCLUDED.evaluated_minute,
        last_event_minute = GREATEST(slo_status.last_event_minute, EXCLUDED.last_event_minute),
        longest_quiet_7d = EXCLUDED.longest_quiet_7d
    WHERE slo_status.org_id = :org_id
      AND slo_status.evaluated_minute < EXCLUDED.evaluated_minute
    """
)


@dataclass(frozen=True, slots=True)
class Target:
    id: uuid.UUID
    kind: str
    service_name: str | None
    first_event_at: datetime | None
    definition_version: int
    evaluated_minute: datetime | None
    last_event_minute: datetime | None
    endpoint_name: str | None = None
    boundary_index: int | None = None
    left_out_endpoints: tuple[str, ...] = ()
    # A check target's check; None once the check is deleted (Source gone, AC70).
    check_id: uuid.UUID | None = None
    check_interval_seconds: int | None = None
    check_locations: tuple[str, ...] = ()
    check_enabled: bool = True

    @property
    def is_check(self) -> bool:
        return self.kind == slo.CHECK_AVAILABILITY


def _target_of(row: Any) -> Target:
    return Target(
        id=row.id,
        kind=row.kind,
        service_name=row.service_name,
        first_event_at=row.first_event_at,
        definition_version=row.definition_version,
        evaluated_minute=getattr(row, "evaluated_minute", None),
        last_event_minute=getattr(row, "last_event_minute", None),
        endpoint_name=row.endpoint_name,
        boundary_index=row.boundary_index,
        left_out_endpoints=tuple(row.left_out_endpoints or ()),
        check_id=row.check_id,
        check_interval_seconds=row.check_interval_seconds,
        check_locations=tuple(row.check_locations or ()),
        check_enabled=bool(row.check_enabled) if row.check_enabled is not None else True,
    )


async def _targets(connection: AsyncConnection, binding: ProjectBinding) -> list[Target]:
    return [_target_of(row) for row in await connection.execute(_TARGETS, _keys(binding))]


async def _zone(connection: AsyncConnection, binding: ProjectBinding) -> ZoneInfo:
    zone = await connection.scalar(_SETTINGS, _keys(binding))
    return ZoneInfo(zone or DEFAULT_TIMEZONE)


def _keys(binding: ProjectBinding) -> dict[str, uuid.UUID]:
    return {"org_id": uuid.UUID(binding.org_id), "project_id": uuid.UUID(binding.project_id)}


# ------------------------------------------------------------------ the sources


@dataclass(frozen=True, slots=True)
class Sources:
    """Everything a project's targets read for one span of time: one read per source."""

    services: Mapping[str, Mapping[datetime, MinuteCounts]]
    latency: Mapping[str, Mapping[datetime, LatencyMinute]]
    endpoints: Mapping[tuple[str, str], Mapping[datetime, MinuteCounts]]
    checks: Mapping[str, Mapping[datetime, CheckMinute]]


def read_sources(
    reader: SloReader, targets: Sequence[Target], *, start: datetime, end: datetime
) -> Sources:
    """Each source the targets need, in [start, end), each read only if one needs it."""
    services = {t.service_name for t in targets if t.kind == slo.SERVICE_AVAILABILITY}
    latency = {t.service_name for t in targets if t.kind == slo.SERVICE_LATENCY}
    endpoints = {
        (t.service_name, t.endpoint_name)
        for t in targets
        if t.kind == slo.ENDPOINT_AVAILABILITY and t.service_name and t.endpoint_name
    } | {
        (t.service_name, name)
        for t in targets
        if t.kind == slo.SERVICE_AVAILABILITY and t.service_name
        for name in t.left_out_endpoints
    }
    checks = {str(t.check_id) for t in targets if t.is_check and t.check_id is not None}
    return Sources(
        services=reader.service_minutes([name for name in services if name], start=start, end=end),
        latency=reader.service_latency([name for name in latency if name], start=start, end=end),
        endpoints=reader.endpoint_minutes(endpoints, start=start, end=end),
        checks=reader.check_runs(checks, start=start, end=end),
    )


def request_minutes(target: Target, sources: Sources) -> dict[datetime, MinuteCounts]:
    """A request kind's minutes from the project's sources (FR1)."""
    service = target.service_name or ""
    if target.kind == slo.SERVICE_LATENCY:
        if target.boundary_index is None:
            return {}
        return counts.latency_minutes(sources.latency.get(service, {}), target.boundary_index)
    if target.kind == slo.ENDPOINT_AVAILABILITY:
        return dict(sources.endpoints.get((service, target.endpoint_name or ""), {}))
    return counts.without_left_out(
        sources.services.get(service, {}),
        [sources.endpoints.get((service, name), {}) for name in target.left_out_endpoints],
    )


def check_runs(target: Target, sources: Sources) -> Mapping[datetime, CheckMinute]:
    if target.check_id is None:
        return {}
    return sources.checks.get(str(target.check_id), {})


def first_event_of(target: Target, sources: Sources) -> datetime | None:
    if target.is_check:
        return counts.first_run(check_runs(target, sources))
    return counts.first_event(request_minutes(target, sources))


def last_event_of(target: Target, sources: Sources) -> datetime | None:
    if target.is_check:
        return counts.last_run(check_runs(target, sources))
    return counts.last_event(request_minutes(target, sources))


def day_row_of(
    target: Target,
    sources: Sources,
    *,
    day_start: datetime,
    day_end: datetime,
    through: datetime,
    first_event_at: datetime,
    lost: frozenset[datetime],
    probe_locations: int,
) -> counts.DayRow:
    """One day of one target, by its kind."""
    if not target.is_check:
        return counts.day_row(
            request_minutes(target, sources),
            day_start=day_start,
            day_end=day_end,
            through=through,
            first_event_at=first_event_at,
            lost=lost,
        )
    row = counts.check_day_row(
        check_runs(target, sources),
        day_start=day_start,
        day_end=day_end,
        through=through,
        first_event_at=first_event_at,
        interval_seconds=target.check_interval_seconds or slo.CHECK_FASTEST_INTERVAL_SECONDS,
        locations=len(target.check_locations) or probe_locations,
    )
    if not target.check_enabled:
        # A disabled check expects nothing (FR11: runs expected while it was enabled).
        seen = row.total + (row.maintenance_runs or 0)
        row = replace(row, expected_runs=seen, missed_runs=0)
    return row


def _interval_turned(target: Target, settled: datetime) -> bool:
    """A check is read at its own cadence: once its interval has turned since the last
    judging, not every minute (design, *What each job does*)."""
    if not target.is_check:
        return True
    interval = target.check_interval_seconds or slo.CHECK_FASTEST_INTERVAL_SECONDS
    if target.evaluated_minute is None:
        return True
    return int(settled.timestamp()) // interval != int(target.evaluated_minute.timestamp()) // (
        interval
    )


def _has_source(target: Target) -> bool:
    return target.check_id is not None if target.is_check else bool(target.service_name)


# --------------------------------------------------------------------- judging


async def judge_slos(
    engine: AsyncEngine, store: SloStore, project_id: uuid.UUID, settled: datetime
) -> int:
    """Work out where each of a project's targets stands at `settled`. Returns how many
    targets were saved; 0 when another run holds the project or nothing is newer."""
    async with engine.connect() as connection:
        binding = await bind_project(connection, project_id)
        if binding is None:
            return 0
        async with session_lock(connection, _JUDGE_LOCK, project_id) as held:
            if not held:
                return 0
            targets = [
                target
                for target in await _targets(connection, binding)
                if target.evaluated_minute is None or target.evaluated_minute < settled
            ]
            if not targets:
                return 0
            zone = await _zone(connection, binding)
            reader = store.for_project(binding.org_id, binding.project_id)
            # Read once for the project. A failed read writes nothing, so the status
            # ages into Not up to date rather than moving on stale figures.
            sources = read_sources(
                reader,
                [target for target in targets if _interval_turned(target, settled)],
                start=settled - JUDGE_LOOKBACK + _MINUTE,
                end=settled + _MINUTE,
            )
            days = await _stored_days(connection, binding, targets, zone=zone, settled=settled)
            for target in targets:
                await _save_status(connection, binding, target, sources, days, zone, settled)
            await connection.commit()
            return len(targets)


@dataclass(frozen=True, slots=True)
class StoredDays:
    """What a target's stored days say about its silence."""

    recent: Mapping[uuid.UUID, list[counts.DayRow]]
    last_busy: Mapping[uuid.UUID, tuple[date, int, datetime]]


async def _stored_days(
    connection: AsyncConnection,
    binding: ProjectBinding,
    targets: Sequence[Target],
    *,
    zone: ZoneInfo,
    settled: datetime,
) -> StoredDays:
    keys: dict[str, Any] = {
        "org_id": uuid.UUID(binding.org_id),
        "ids": [target.id for target in targets],
        "since": settled.astimezone(zone).date() - timedelta(days=QUIET_LOOKBACK_DAYS - 1),
    }
    recent: dict[uuid.UUID, list[counts.DayRow]] = {}
    for row in await connection.execute(_RECENT_DAYS, keys):
        recent.setdefault(row.slo_id, []).append(
            counts.DayRow(
                good=0,
                total=row.total,
                failures=0,
                quiet_minutes=row.quiet_minutes,
                lost_minutes=row.lost_minutes,
                quiet_leading_minutes=row.quiet_leading_minutes,
                quiet_trailing_minutes=row.quiet_trailing_minutes,
                longest_quiet_minutes=row.longest_quiet_minutes,
            )
        )
    last_busy = {
        row.slo_id: (row.day, row.quiet_trailing_minutes, row.computed_at)
        for row in await connection.execute(_LAST_BUSY_DAYS, keys)
    }
    return StoredDays(recent=recent, last_busy=last_busy)


async def _save_status(
    connection: AsyncConnection,
    binding: ProjectBinding,
    target: Target,
    sources: Sources,
    days: StoredDays,
    zone: ZoneInfo,
    settled: datetime,
) -> None:
    first = first_event_of(target, sources)
    if first is not None and (target.first_event_at is None or first < target.first_event_at):
        await connection.execute(
            _FIRST_EVENT, {"org_id": uuid.UUID(binding.org_id), "id": target.id, "first": first}
        )
    last = _latest(
        target.last_event_minute,
        last_event_of(target, sources),
        _last_event_from_days(days.last_busy.get(target.id), zone),
    )
    await connection.execute(
        _SAVE_STATUS,
        {
            "id": target.id,
            "org_id": uuid.UUID(binding.org_id),
            "project_id": uuid.UUID(binding.project_id),
            "minute": settled,
            "last_event": last,
            "longest_quiet": slo.longest_quiet(days.recent.get(target.id, [])),
        },
    )


def _last_event_from_days(
    busy: tuple[date, int, datetime] | None, zone: ZoneInfo
) -> datetime | None:
    """The last event a stored day says: its end, less its trailing quiet stretch. Only
    for a day counted after it ended, so its trailing stretch runs to its end; a day
    still under way is within the minutes the judging reads, which say it exactly."""
    if busy is None:
        return None
    day, trailing, computed_at = busy
    start, end = slo.day_bounds(day, zone)
    if end > computed_at:
        return None
    last = end - _MINUTE * (trailing + 1)
    return last if last >= start else None


def _minute_of(moment: datetime) -> datetime:
    """The minute a moment falls in: where counting stops before any sweep has settled one."""
    return moment.replace(second=0, microsecond=0)


def _latest(*moments: datetime | None) -> datetime | None:
    return max((moment for moment in moments if moment is not None), default=None)


# -------------------------------------------------------------------- counting

_SETTLED: Final = text("SELECT settled_minute FROM detection_sweep")
# Days not yet final whose row is an hour old: rewritten as they settle (BR13).
_STALE_DAYS: Final = text(
    """
    SELECT DISTINCT day FROM slo_daily_counts
    WHERE org_id = :org_id AND slo_id = ANY(:ids) AND NOT final AND computed_at < :older
    """
)
_NEW_DAY: Final = """
    INSERT INTO slo_daily_counts (
        org_id, slo_id, day, good, total, failures, quiet_minutes, lost_minutes,
        quiet_leading_minutes, quiet_trailing_minutes, longest_quiet_minutes, expected_runs,
        missed_runs, maintenance_runs, loss_known, definition_version, final, computed_at)
    VALUES (
        :org_id, :id, :day, :good, :total, :failures, :quiet, :lost, :leading, :trailing,
        :longest, :expected_runs, :missed_runs, :maintenance_runs, :loss_known, :version,
        :final, :computed_at)
"""
_COUNTED_COLUMNS: Final = """
    good = EXCLUDED.good, total = EXCLUDED.total, failures = EXCLUDED.failures,
    quiet_minutes = EXCLUDED.quiet_minutes, lost_minutes = EXCLUDED.lost_minutes,
    quiet_leading_minutes = EXCLUDED.quiet_leading_minutes,
    quiet_trailing_minutes = EXCLUDED.quiet_trailing_minutes,
    longest_quiet_minutes = EXCLUDED.longest_quiet_minutes,
    expected_runs = EXCLUDED.expected_runs, missed_runs = EXCLUDED.missed_runs,
    maintenance_runs = EXCLUDED.maintenance_runs,
    loss_known = EXCLUDED.loss_known, definition_version = EXCLUDED.definition_version,
    computed_at = EXCLUDED.computed_at
"""
# A count: never over a final day (AC7), nor over a day a newer definition counted.
_SAVE_DAY: Final = text(
    f"""
    {_NEW_DAY}
    ON CONFLICT (org_id, slo_id, day) DO UPDATE
    SET {_COUNTED_COLUMNS}, final = EXCLUDED.final
    WHERE slo_daily_counts.org_id = :org_id AND NOT slo_daily_counts.final
      AND slo_daily_counts.definition_version <= EXCLUDED.definition_version
    """
)
# A history read inside the recount limit (FR28): also over a final day counted under an
# older definition. A final day whose figures moved is marked changed, so its month says
# so after it ended (BR13); one that read the same is not.
_RECOUNT_DAY: Final = text(
    f"""
    {_NEW_DAY}
    ON CONFLICT (org_id, slo_id, day) DO UPDATE
    SET {_COUNTED_COLUMNS},
        final = slo_daily_counts.final OR EXCLUDED.final,
        changed_at = CASE WHEN slo_daily_counts.final
                               AND (slo_daily_counts.good, slo_daily_counts.total)
                                   IS DISTINCT FROM (EXCLUDED.good, EXCLUDED.total)
                          THEN EXCLUDED.computed_at ELSE slo_daily_counts.changed_at END,
        changed_why = CASE WHEN slo_daily_counts.final
                                AND (slo_daily_counts.good, slo_daily_counts.total)
                                    IS DISTINCT FROM (EXCLUDED.good, EXCLUDED.total)
                           THEN :changed_why ELSE slo_daily_counts.changed_why END
    WHERE slo_daily_counts.org_id = :org_id
      AND slo_daily_counts.definition_version <= EXCLUDED.definition_version
      AND (NOT slo_daily_counts.final
           OR slo_daily_counts.definition_version < EXCLUDED.definition_version)
    """
)
# A history read past the recount limit: only a day never counted (AC69); one already
# held stays as it was counted, under its own definition (AC45).
_FIRST_READ_DAY: Final = text(
    f"""
    {_NEW_DAY}
    ON CONFLICT (org_id, slo_id, day) DO NOTHING
    """
)
_MARK_FINAL: Final = text(
    """
    UPDATE slo_daily_counts SET final = true
    WHERE org_id = :org_id AND slo_id = ANY(:ids) AND day < :cutoff AND NOT final
    """
)


async def count_slos(
    engine: AsyncEngine,
    store: SloStore,
    project_id: uuid.UUID,
    *,
    now: datetime,
    probe_locations: int = ONE_LOCATION,
) -> int:
    """Rewrite a project's due days: today, yesterday, and any day not yet final whose
    row is an hour old. Marks days past 7 final. Returns the rows written."""
    async with engine.connect() as connection:
        binding = await bind_project(connection, project_id)
        if binding is None:
            return 0
        async with session_lock(connection, _COUNT_LOCK, project_id) as held:
            if not held:
                return 0
            settled = await connection.scalar(_SETTLED) or _minute_of(now)
            targets = [t for t in await _targets(connection, binding) if t.first_event_at]
            if not targets:
                return 0
            zone = await _zone(connection, binding)
            today = now.astimezone(zone).date()
            keys = {"org_id": uuid.UUID(binding.org_id), "ids": [t.id for t in targets]}
            stale = await connection.execute(
                _STALE_DAYS, keys | {"older": now - OLDER_DAYS_DUE_AFTER}
            )
            cutoff = today - timedelta(days=slo.FINAL_AFTER_DAYS)
            due = sorted(
                {today, today - timedelta(days=1)} | {row.day for row in stale if row.day >= cutoff}
            )
            written = await _count_days(
                connection,
                binding,
                store.for_project(binding.org_id, binding.project_id),
                targets,
                due,
                zone=zone,
                through=settled + _MINUTE,
                today=today,
                now=now,
                probe_locations=probe_locations,
            )
            await connection.execute(_MARK_FINAL, keys | {"cutoff": cutoff})
            await connection.commit()
            return written


async def _count_days(
    connection: AsyncConnection,
    binding: ProjectBinding,
    reader: SloReader,
    targets: Sequence[Target],
    days: Sequence[date],
    *,
    zone: ZoneInfo,
    through: datetime,
    today: date,
    now: datetime,
    probe_locations: int,
    recount_from: date | None = None,
) -> int:
    """Count each day for every target, one read per source per day for the project, and
    save the rows. Returns how many were written. With `recount_from`, a history read's
    recount limit: days from it are recounted, older ones only first read (FR28)."""
    written = 0
    counted = [target for target in targets if _has_source(target)]
    for day in days:
        start, end = slo.day_bounds(day, zone)
        if start >= through:
            continue
        sources = read_sources(reader, counted, start=start, end=min(end, through))
        lost = await holds.lost_minutes(connection, start=start, end=min(end, through))
        loss_known = (today - day).days <= slo.LOSS_RECORDS_DAYS
        statement = _day_statement(day, recount_from)
        for target in counted:
            if target.first_event_at is None or end <= target.first_event_at:
                continue
            row = day_row_of(
                target,
                sources,
                day_start=start,
                day_end=end,
                through=through,
                first_event_at=target.first_event_at,
                lost=lost,
                probe_locations=probe_locations,
            )
            await _save_day(
                connection,
                statement,
                binding,
                target,
                day,
                row,
                loss_known=loss_known,
                final=(today - day).days > slo.FINAL_AFTER_DAYS,
                now=now,
            )
            written += 1
        await connection.commit()
    return written


def _day_statement(day: date, recount_from: date | None) -> TextClause:
    if recount_from is None:
        return _SAVE_DAY
    return _RECOUNT_DAY if day >= recount_from else _FIRST_READ_DAY


async def _save_day(
    connection: AsyncConnection,
    statement: TextClause,
    binding: ProjectBinding,
    target: Target,
    day: date,
    row: counts.DayRow,
    *,
    loss_known: bool,
    final: bool,
    now: datetime,
) -> None:
    await connection.execute(
        statement,
        {
            "org_id": uuid.UUID(binding.org_id),
            "id": target.id,
            "day": day,
            "good": row.good,
            "total": row.total,
            "failures": row.failures,
            "quiet": row.quiet_minutes,
            "lost": row.lost_minutes,
            "leading": row.quiet_leading_minutes,
            "trailing": row.quiet_trailing_minutes,
            "longest": row.longest_quiet_minutes,
            "expected_runs": row.expected_runs,
            "missed_runs": row.missed_runs,
            "maintenance_runs": row.maintenance_runs,
            "loss_known": loss_known,
            "version": target.definition_version,
            "final": final,
            "computed_at": now,
            "changed_why": slo.CHANGED_BY_DEFINITION,
        },
    )


# --------------------------------------------------------------------- history

# What establishes the tenant: a history job is given a target id and reads its
# organization and project from the target's own row, never from the caller.
_TARGET_PROJECT: Final = text("SELECT project_id FROM slo_targets WHERE id = :id")
_HISTORY_TARGET: Final = text(
    """
    SELECT t.id, t.kind, t.service_name, t.endpoint_name, t.boundary_index,
           t.left_out_endpoints, t.check_id, t.first_event_at, t.definition_version,
           c.interval_seconds AS check_interval_seconds, c.locations AS check_locations,
           c.enabled AS check_enabled
    FROM slo_targets AS t
    LEFT JOIN checks AS c ON c.org_id = t.org_id AND c.id = t.check_id
    WHERE t.org_id = :org_id AND t.id = :id AND t.deleted_at IS NULL
    """
)
_HISTORY_READ: Final = text(
    """
    UPDATE slo_targets
    SET history_read_version = GREATEST(history_read_version, :version),
        history_read_through = :through
    WHERE org_id = :org_id AND id = :id
    """
)
_CURRENT_VERSION: Final = text(
    "SELECT definition_version FROM slo_targets WHERE org_id = :org_id AND id = :id"
)


async def read_slo_history(
    engine: AsyncEngine,
    store: SloStore,
    target_id: uuid.UUID,
    *,
    now: datetime,
    probe_locations: int = ONE_LOCATION,
) -> int:
    """Count a target's past days, oldest first, back as far as its source holds, and
    run again while its definition moved during the read (AC64). Returns rows written."""
    async with engine.connect() as connection:
        project_id = await connection.scalar(_TARGET_PROJECT, {"id": target_id})
        await connection.commit()
        if project_id is None:
            return 0
        binding = await bind_project(connection, project_id)
        if binding is None:
            return 0
        async with session_lock(connection, _HISTORY_LOCK, target_id) as held:
            if not held:
                return 0
            written = 0
            while True:
                read = await _read_history_once(
                    connection, store, binding, target_id, now=now, probe_locations=probe_locations
                )
                if read is None:
                    return written
                version, rows = read
                written += rows
                current = await connection.scalar(
                    _CURRENT_VERSION, {"org_id": uuid.UUID(binding.org_id), "id": target_id}
                )
                await connection.commit()
                if current is None or current == version:
                    return written


async def _read_history_once(
    connection: AsyncConnection,
    store: SloStore,
    binding: ProjectBinding,
    target_id: uuid.UUID,
    *,
    now: datetime,
    probe_locations: int,
) -> tuple[int, int] | None:
    keys = {"org_id": uuid.UUID(binding.org_id), "id": target_id}
    row = (await connection.execute(_HISTORY_TARGET, keys)).one_or_none()
    settled = await connection.scalar(_SETTLED)
    await connection.commit()
    if row is None:
        return None
    zone = await _zone(connection, binding)
    await connection.commit()
    through = (settled or _minute_of(now)) + _MINUTE
    today = now.astimezone(zone).date()
    target = _target_of(row)
    reach = binding.derived_retention_days
    if target.kind in (slo.SERVICE_AVAILABILITY, slo.SERVICE_LATENCY):
        reach = min(reach, SERVICE_ROLLUP_DAYS)
    days = [today - timedelta(days=offset) for offset in range(reach, -1, -1)]
    recount_from = slo.recount_limit(today, retention_days=reach)
    reader = store.for_project(binding.org_id, binding.project_id)
    first = target.first_event_at
    written = 0
    for day in days if _has_source(target) else []:
        start, end = slo.day_bounds(day, zone)
        if first is None:
            found = first_event_of(
                target, read_sources(reader, [target], start=start, end=min(end, through))
            )
            if found is None:
                continue
            first = found
            await connection.execute(_FIRST_EVENT, keys | {"first": first})
            await connection.commit()
        written += await _count_days(
            connection,
            binding,
            reader,
            [_with_first(target, first)],
            [day],
            zone=zone,
            through=through,
            today=today,
            now=now,
            probe_locations=probe_locations,
            recount_from=recount_from,
        )
    await connection.execute(
        _HISTORY_READ, keys | {"version": target.definition_version, "through": today}
    )
    await connection.commit()
    return target.definition_version, written


def _with_first(target: Target, first: datetime) -> Target:
    return replace(target, first_event_at=first)


# ----------------------------------------------------------------------- tasks

_store: SloStore | None = None

NO_STORE: Final = "CLICKHOUSE_DSN is not set for the scheduler; reliability targets judge nothing"


def _the_store() -> SloStore | None:
    global _store
    if _store is None:
        dsn = get_settings().clickhouse_dsn
        if not dsn:
            logger.error(NO_STORE)
            return None
        _store = connect_store(dsn)
    return _store


@task(slo.JUDGE_SLOS_TASK)
def judge_slos_task(project_id: str, settled_minute: str) -> None:
    store = _the_store()
    if store is None:
        return
    settled = datetime.fromisoformat(settled_minute)
    with_engine(lambda engine: judge_slos(engine, store, uuid.UUID(project_id), settled))


@task(slo.COUNT_SLOS_TASK)
def count_slos_task(project_id: str) -> None:
    store = _the_store()
    if store is None:
        return
    now = datetime.now(UTC)
    locations = len(get_settings().probe_locations)
    with_engine(
        lambda engine: count_slos(
            engine, store, uuid.UUID(project_id), now=now, probe_locations=locations
        )
    )


@task(slo.READ_SLO_HISTORY_TASK)
def read_slo_history_task(target_id: str) -> None:
    store = _the_store()
    if store is None:
        return
    now = datetime.now(UTC)
    locations = len(get_settings().probe_locations)
    with_engine(
        lambda engine: read_slo_history(
            engine, store, uuid.UUID(target_id), now=now, probe_locations=locations
        )
    )
