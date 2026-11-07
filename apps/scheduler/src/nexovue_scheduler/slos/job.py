"""F19's three jobs, and `queue_jobs`, which hands them out (design, *What each job does*).

- **`judge_slos`**, on `slo`, one project each settled minute: where each target
  stands now. Step 1 records when it was worked out, the last minute its source sent
  anything, and the longest quiet stretch of the last 7 days, so a silent service
  reads **No recent requests** and never looks healthy (BR5). Burn tiers join in Step 4.
- **`count_slos`**, on `slo_counts`, by age: a project's recent days, rewritten until
  they are 7 days old and then final (D2, BR13).
- **`read_slo_history`**, on `slo_history`, one target: its past days, back as far as
  the source holds, when it is created or what it counts changes.

**Who gets a job** is read from the targets themselves, not from detection's active
projects, which drop a project silent for a day (C8): a 27-day silence is still judged
(AC8). Projects go longest-waiting first, so whoever was skipped leads the next minute
(AC65). A failure to queue is the caller's to log; it must not stop detection (AC95).

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
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from nexovue_platform import slo
from nexovue_platform.detection import DEFAULT_TIMEZONE
from nexovue_platform.jobs import JobRequest
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.detection import holds
from nexovue_scheduler.detection.projects import session_lock
from nexovue_scheduler.detection.store import ProjectBinding, bind_project
from nexovue_scheduler.jobs.queue import enqueue_all
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings
from nexovue_scheduler.slos import counts
from nexovue_scheduler.slos.store import MinuteCounts, SloReader, SloStore, connect_store

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
# A span's own time is trusted 7 days back, so a day older than that is final (BR13).
FINAL_AFTER_DAYS: Final = 7
# The 7 days FR10a's attention limit reads the longest quiet stretch from.
QUIET_LOOKBACK_DAYS: Final = 7
# `service_stats_1m` holds at most 35 days on any plan (FR27): how far a history reaches.
SERVICE_ROLLUP_DAYS: Final = 35
# `intake_refusals` and `detection_events` are kept 29 days (`detection/retention.py`): a
# day older than that cannot say what nexovue lost in it (FR28).
LOSS_RECORDS_DAYS: Final = 29
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
    SELECT t.id, t.kind, t.service_name, t.first_event_at, t.definition_version,
           s.evaluated_minute, s.last_event_minute
    FROM slo_targets AS t
    LEFT JOIN slo_status AS s ON s.org_id = t.org_id AND s.slo_id = t.id
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


async def _targets(connection: AsyncConnection, binding: ProjectBinding) -> list[Target]:
    rows = await connection.execute(_TARGETS, _keys(binding))
    return [
        Target(
            id=row.id,
            kind=row.kind,
            service_name=row.service_name,
            first_event_at=row.first_event_at,
            definition_version=row.definition_version,
            evaluated_minute=row.evaluated_minute,
            last_event_minute=row.last_event_minute,
        )
        for row in rows
    ]


async def _zone(connection: AsyncConnection, binding: ProjectBinding) -> ZoneInfo:
    zone = await connection.scalar(_SETTINGS, _keys(binding))
    return ZoneInfo(zone or DEFAULT_TIMEZONE)


def _keys(binding: ProjectBinding) -> dict[str, uuid.UUID]:
    return {"org_id": uuid.UUID(binding.org_id), "project_id": uuid.UUID(binding.project_id)}


def _services(targets: Sequence[Target]) -> list[str]:
    """The services Step 1's one kind reads; other kinds' sources join in Step 2."""
    return sorted(
        {
            target.service_name
            for target in targets
            if target.kind == slo.SERVICE_AVAILABILITY and target.service_name
        }
    )


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
            minutes = reader.service_minutes(
                _services(targets), start=settled - JUDGE_LOOKBACK + _MINUTE, end=settled + _MINUTE
            )
            days = await _stored_days(connection, binding, targets, zone=zone, settled=settled)
            for target in targets:
                await _save_status(connection, binding, target, minutes, days, zone, settled)
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
    minutes: Mapping[str, Mapping[datetime, MinuteCounts]],
    days: StoredDays,
    zone: ZoneInfo,
    settled: datetime,
) -> None:
    source = minutes.get(target.service_name or "", {})
    first = counts.first_event(source)
    if first is not None and (target.first_event_at is None or first < target.first_event_at):
        await connection.execute(
            _FIRST_EVENT, {"org_id": uuid.UUID(binding.org_id), "id": target.id, "first": first}
        )
    last = _latest(
        target.last_event_minute,
        counts.last_event(source),
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
            "longest_quiet": counts.longest_quiet(days.recent.get(target.id, [])),
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
# A final day is never rewritten here (AC7); a definition change or an exclusion is Step 3's.
_SAVE_DAY: Final = text(
    """
    INSERT INTO slo_daily_counts (
        org_id, slo_id, day, good, total, failures, quiet_minutes, lost_minutes,
        quiet_leading_minutes, quiet_trailing_minutes, longest_quiet_minutes, loss_known,
        definition_version, final, computed_at)
    VALUES (
        :org_id, :id, :day, :good, :total, :failures, :quiet, :lost, :leading, :trailing,
        :longest, :loss_known, :version, :final, :computed_at)
    ON CONFLICT (org_id, slo_id, day) DO UPDATE
    SET good = EXCLUDED.good, total = EXCLUDED.total, failures = EXCLUDED.failures,
        quiet_minutes = EXCLUDED.quiet_minutes, lost_minutes = EXCLUDED.lost_minutes,
        quiet_leading_minutes = EXCLUDED.quiet_leading_minutes,
        quiet_trailing_minutes = EXCLUDED.quiet_trailing_minutes,
        longest_quiet_minutes = EXCLUDED.longest_quiet_minutes,
        loss_known = EXCLUDED.loss_known, definition_version = EXCLUDED.definition_version,
        final = EXCLUDED.final, computed_at = EXCLUDED.computed_at
    WHERE slo_daily_counts.org_id = :org_id AND NOT slo_daily_counts.final
    """
)
_MARK_FINAL: Final = text(
    """
    UPDATE slo_daily_counts SET final = true
    WHERE org_id = :org_id AND slo_id = ANY(:ids) AND day < :cutoff AND NOT final
    """
)


async def count_slos(
    engine: AsyncEngine, store: SloStore, project_id: uuid.UUID, *, now: datetime
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
            cutoff = today - timedelta(days=FINAL_AFTER_DAYS)
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
) -> int:
    """Count each day for every target, one read per day for the project, and save the
    rows. Returns how many were written."""
    written = 0
    for day in days:
        start, end = slo.day_bounds(day, zone)
        if start >= through:
            continue
        minutes = reader.service_minutes(_services(targets), start=start, end=min(end, through))
        lost = await holds.lost_minutes(connection, start=start, end=min(end, through))
        loss_known = (today - day).days <= LOSS_RECORDS_DAYS
        for target in targets:
            if target.first_event_at is None or end <= target.first_event_at:
                continue
            row = counts.day_row(
                minutes.get(target.service_name or "", {}),
                day_start=start,
                day_end=end,
                through=through,
                first_event_at=target.first_event_at,
                lost=lost,
            )
            await _save_day(
                connection,
                binding,
                target,
                day,
                row,
                loss_known=loss_known,
                final=(today - day).days > FINAL_AFTER_DAYS,
                now=now,
            )
            written += 1
        await connection.commit()
    return written


async def _save_day(
    connection: AsyncConnection,
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
        _SAVE_DAY,
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
            "loss_known": loss_known,
            "version": target.definition_version,
            "final": final,
            "computed_at": now,
        },
    )


# --------------------------------------------------------------------- history

# What establishes the tenant: a history job is given a target id and reads its
# organization and project from the target's own row, never from the caller.
_TARGET_PROJECT: Final = text("SELECT project_id FROM slo_targets WHERE id = :id")
_HISTORY_TARGET: Final = text(
    """
    SELECT id, kind, service_name, first_event_at, definition_version, created_at
    FROM slo_targets
    WHERE org_id = :org_id AND id = :id AND deleted_at IS NULL
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
    engine: AsyncEngine, store: SloStore, target_id: uuid.UUID, *, now: datetime
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
                read = await _read_history_once(connection, store, binding, target_id, now=now)
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
    reach = min(binding.derived_retention_days, SERVICE_ROLLUP_DAYS)
    days = [today - timedelta(days=offset) for offset in range(reach, -1, -1)]
    reader = store.for_project(binding.org_id, binding.project_id)
    target = Target(
        id=row.id,
        kind=row.kind,
        service_name=row.service_name,
        first_event_at=row.first_event_at,
        definition_version=row.definition_version,
        evaluated_minute=None,
        last_event_minute=None,
    )
    first = target.first_event_at
    written = 0
    for day in days:
        start, end = slo.day_bounds(day, zone)
        if first is None:
            found = counts.first_event(
                reader.service_minutes(_services([target]), start=start, end=min(end, through)).get(
                    target.service_name or "", {}
                )
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
        )
    await connection.execute(
        _HISTORY_READ, keys | {"version": target.definition_version, "through": today}
    )
    await connection.commit()
    return target.definition_version, written


def _with_first(target: Target, first: datetime) -> Target:
    return Target(
        id=target.id,
        kind=target.kind,
        service_name=target.service_name,
        first_event_at=first,
        definition_version=target.definition_version,
        evaluated_minute=target.evaluated_minute,
        last_event_minute=target.last_event_minute,
    )


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
    with_engine(lambda engine: count_slos(engine, store, uuid.UUID(project_id), now=now))


@task(slo.READ_SLO_HISTORY_TASK)
def read_slo_history_task(target_id: str) -> None:
    store = _the_store()
    if store is None:
        return
    now = datetime.now(UTC)
    with_engine(lambda engine: read_slo_history(engine, store, uuid.UUID(target_id), now=now))
