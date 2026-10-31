"""The hourly baseline: each service's normal for every minute of the next two
hours, built once from its history so the minute-by-minute job reads one small
row per service (design, "The normal"; spec FR4 to FR8).

For each judged minute M, in the project's time zone:

- **The days.** Weekly mode is the same weekday over the last 4 weeks, when at
  least 3 of those days have history. Otherwise daily mode: earlier days of the
  same class, weekday or weekend, when at least 3 have history. Otherwise the
  signal is *learning*, and says until when. Days are counted from the service's
  first counted minute, so a day of history is a day the service existed, busy or
  not (FR4's "distinct past days").
- **The level pool** is M ± 15 minutes on each chosen day; L, the pool's share
  above L and its p95 come from it. **The dispersion pool**, M ± 180 minutes on the
  same days, gives the wander only. Neither reaches into M's own local day.
- **Like with like** (FR8): both pools are made of windows as long as the one
  judged, so each baseline is for one window length, the project's sensitivity's.

Ported from `tools/detection-sim/detect_sim.py`'s `Pools` and `judge_slower`, and
held to them by `test_detection_baselines.py`.

**Not yet here** (plan T3.2): minutes inside findings, delayed or snoozed minutes
left out of the pools (BR3); *failing more* and *losing traffic*.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import batched
from typing import Any, Final
from zoneinfo import ZoneInfo

from nexovue_platform.jobs import JobRequest
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.detection import (
    COMPUTE_BASELINES_TASK,
    DETECTION_QUEUE,
    SWEEP_BASELINES_TASK,
)
from nexovue_scheduler.detection.method import (
    BUCKET_COUNT,
    Minute,
    Mode,
    SlowerBaseline,
    bucket_p95,
    moment_wander,
    threshold_share,
)
from nexovue_scheduler.detection.projects import (
    active_projects,
    fair_order,
    project_settings,
    try_lock,
)
from nexovue_scheduler.detection.store import (
    DetectionStore,
    ProjectDetection,
    bind_project,
    store_for_process,
)
from nexovue_scheduler.jobs.queue import enqueue_all
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine

logger = get_logger(__name__)

MINUTE: Final = timedelta(minutes=1)
LEVEL_HALF: Final = timedelta(minutes=15)
DISPERSION_HALF: Final = timedelta(minutes=180)
LEARNING_DAYS: Final = 3
POOL_DAYS: Final = 28
WEEK_DAYS: Final = 7
_WEEKEND_FROM: Final = 5  # date.weekday(): Saturday and Sunday
# Every service counted this long ago or since has a baseline: the 4 weeks of pools.
HISTORY: Final = timedelta(days=POOL_DAYS + 1)
# A run covers from a little before now, so a minute judged late still has its
# normal, to two hours ahead, so one late hourly run leaves no gap.
BEHIND: Final = timedelta(minutes=15)
AHEAD: Final = timedelta(hours=2)
# Older rows than this are deleted by the next run; nothing judges them again.
KEPT: Final = timedelta(hours=1)
# Services read together: about 50 services, 4 days and 500 minutes, well inside a read's rows.
SERVICE_BATCH: Final = 50
SLOWER: Final = "slower"
_LOCK_PURPOSE: Final = "baselines"
# Pure Python over up to two hours of minutes for every service of a project.
BASELINE_SOFT_LIMIT_SECONDS: Final = 300
BASELINE_HARD_LIMIT_SECONDS: Final = 330

_EMPTY: Final = Minute.empty()

_UPSERT: Final = text(
    """
    INSERT INTO detection_baselines
        (org_id, project_id, service_name, signal, minute, baseline, computed_at)
    VALUES
        (:org_id, :project_id, :service_name, :signal, :minute, CAST(:baseline AS jsonb), now())
    ON CONFLICT (org_id, project_id, service_name, signal, minute) DO UPDATE
    SET baseline = EXCLUDED.baseline, computed_at = EXCLUDED.computed_at
    """
)
_PRUNE: Final = text(
    """
    DELETE FROM detection_baselines
    WHERE org_id = :org_id AND project_id = :project_id AND minute < :before
    """
)


# ------------------------------------------------------------------- the days


@dataclass(frozen=True, slots=True)
class PoolDay:
    """One past day a minute is compared with: its centre, M less `offset` local
    days, and the start of M's own local day, which no pool reaches."""

    offset: int
    centre: datetime
    day_start: datetime

    @property
    def level(self) -> tuple[datetime, datetime]:
        """The level pool's window ends, first and last inclusive."""
        return self.centre - LEVEL_HALF, min(self.centre + LEVEL_HALF, self.day_start - MINUTE)

    @property
    def dispersion(self) -> tuple[datetime, datetime]:
        return self.centre - DISPERSION_HALF, min(
            self.centre + DISPERSION_HALF, self.day_start - MINUTE
        )


@dataclass(frozen=True, slots=True)
class Pool:
    mode: Mode
    days: tuple[PoolDay, ...]


def _is_weekend(day: date) -> bool:
    return day.weekday() >= _WEEKEND_FROM


def _local_midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time(), tzinfo=zone).astimezone(UTC)


def select_pool(minute: datetime, zone: ZoneInfo, first_seen: datetime) -> Pool:
    """FR4 and FR5's days for one minute. The same local clock time on each past
    day, so a daylight-saving change never shifts the normal (FR7)."""
    local = minute.astimezone(zone)
    day_start = _local_midnight(local.date(), zone)
    wall = local.replace(tzinfo=None)
    weekly: list[PoolDay] = []
    same_class: list[PoolDay] = []
    for offset in range(1, POOL_DAYS + 1):
        past = wall - timedelta(days=offset)
        centre = past.replace(tzinfo=zone).astimezone(UTC)
        if centre < first_seen:
            continue
        day = PoolDay(offset, centre, day_start)
        if offset % WEEK_DAYS == 0:
            weekly.append(day)
        if _is_weekend(past.date()) == _is_weekend(local.date()):
            same_class.append(day)
    if len(weekly) >= LEARNING_DAYS:
        return Pool("weekly", tuple(weekly))
    if len(same_class) >= LEARNING_DAYS:
        return Pool("daily", tuple(same_class))
    return Pool("learning", ())


def learning_until(minute: datetime, zone: ZoneInfo, first_seen: datetime) -> date:
    """The first local date, from M's, on which M's class of day has 3 days of
    history: a weekend can leave learning later than a weekday (design)."""
    first = first_seen.astimezone(zone).date()
    candidate = minute.astimezone(zone).date()
    weekend = _is_weekend(candidate)
    while True:
        had = sum(
            1
            for back in range((candidate - first).days)
            if _is_weekend(first + timedelta(days=back)) == weekend
        )
        if had >= LEARNING_DAYS and _is_weekend(candidate) == weekend:
            return candidate
        candidate += timedelta(days=1)


def history_spans(pools: Iterable[Pool], width: int) -> dict[int, tuple[datetime, datetime]]:
    """For each day offset any pool uses, the minutes its windows read, first and
    last inclusive: the dispersion pool's, and the window's lead before it."""
    spans: dict[int, tuple[datetime, datetime]] = {}
    lead = (width - 1) * MINUTE
    for pool in pools:
        for day in pool.days:
            first, last = day.dispersion
            first -= lead
            known = spans.get(day.offset)
            if known is not None:
                first, last = min(first, known[0]), max(last, known[1])
            spans[day.offset] = (first, last)
    return spans


# ----------------------------------------------------------------- the sums


class _Block:
    """One day offset's minutes, dense, with the sums its pools take in O(1).

    `_level[j]` sums the window histograms ending before minute j. The dispersion
    sums, for one L, are built on first use: summed over the windows of a range,
    their requests, their counts above L, and the squares and product of those.
    """

    def __init__(
        self, first: datetime, last: datetime, history: Mapping[datetime, Minute], width: int
    ) -> None:
        self._first = first
        length = (last - first) // MINUTE + 1
        minutes = [history.get(first + index * MINUTE, _EMPTY) for index in range(length)]
        running = [0] * BUCKET_COUNT
        totals: list[list[int]] = [running[:]]
        requests = [0]
        for minute in minutes:
            running = [a + b for a, b in zip(running, minute.buckets, strict=True)]
            totals.append(running)
            requests.append(requests[-1] + minute.requests)
        self._windows: list[list[int]] = []
        self._window_requests: list[int] = []
        level = [0] * BUCKET_COUNT
        self._level: list[list[int]] = [level]
        for end in range(1, length + 1):
            start = max(end - width, 0)
            window = [a - b for a, b in zip(totals[end], totals[start], strict=True)]
            self._windows.append(window)
            self._window_requests.append(requests[end] - requests[start])
            level = [a + b for a, b in zip(level, window, strict=True)]
            self._level.append(level)
        self._dispersion: dict[int, list[tuple[int, int, int, int, int]]] = {}

    def _index(self, minute: datetime) -> int:
        return (minute - self._first) // MINUTE

    def level(self, first: datetime, last: datetime) -> list[int]:
        a, b = self._index(first), self._index(last) + 1
        return [high - low for high, low in zip(self._level[b], self._level[a], strict=True)]

    def dispersion(
        self, first: datetime, last: datetime, threshold: int
    ) -> tuple[int, int, int, int, int]:
        sums = self._dispersion.get(threshold)
        if sums is None:
            sums = [(0, 0, 0, 0, 0)]
            n_hist = above = nn = aa = na = 0
            for window, n in zip(self._windows, self._window_requests, strict=True):
                a = sum(window[threshold:])
                n_hist += sum(window)
                above += a
                nn += n * n
                aa += a * a
                na += n * a
                sums.append((n_hist, above, nn, aa, na))
            self._dispersion[threshold] = sums
        high, low = sums[self._index(last) + 1], sums[self._index(first)]
        n_hist, above, nn, aa, na = (h - lo for h, lo in zip(high, low, strict=True))
        return n_hist, above, nn, aa, na


def slower_baselines(
    history: Mapping[datetime, Minute],
    minutes: Sequence[datetime],
    *,
    zone: ZoneInfo,
    first_seen: datetime,
    width: int,
) -> dict[datetime, SlowerBaseline]:
    """*Slower*'s normal at each minute, for windows of `width` minutes.

    `history` holds the service's counted minutes over `history_spans`; a minute
    it lacks had no requests."""
    pools = {minute: select_pool(minute, zone, first_seen) for minute in minutes}
    blocks = {
        offset: _Block(first, last, history, width)
        for offset, (first, last) in history_spans(pools.values(), width).items()
    }
    found: dict[datetime, SlowerBaseline] = {}
    for minute, pool in pools.items():
        if pool.mode == "learning":
            found[minute] = SlowerBaseline(
                mode="learning",
                days=0,
                window_minutes=width,
                pool_requests=0,
                threshold_index=1,
                share_above=0.0,
                wander=0.0,
                normal_p95_ms=None,
                learning_until=learning_until(minute, zone, first_seen),
            )
            continue
        level = [0] * BUCKET_COUNT
        for day in pool.days:
            part = blocks[day.offset].level(*day.level)
            level = [a + b for a, b in zip(level, part, strict=True)]
        threshold, share = threshold_share(level)
        moments = [0, 0, 0, 0, 0]
        for day in pool.days:
            part5 = blocks[day.offset].dispersion(*day.dispersion, threshold)
            moments = [a + b for a, b in zip(moments, part5, strict=True)]
        found[minute] = SlowerBaseline(
            mode=pool.mode,
            days=len(pool.days),
            window_minutes=width,
            pool_requests=sum(level),
            threshold_index=threshold,
            share_above=share,
            wander=moment_wander(*(float(value) for value in moments)),
            normal_p95_ms=bucket_p95(level),
        )
    return found


# -------------------------------------------------------------------- stored


def baseline_json(baseline: SlowerBaseline) -> dict[str, Any]:
    """`detection_baselines.baseline` for *slower*."""
    return {
        "mode": baseline.mode,
        "days": baseline.days,
        "window_minutes": baseline.window_minutes,
        "pool_requests": baseline.pool_requests,
        "threshold_index": baseline.threshold_index,
        "share_above": baseline.share_above,
        "wander": baseline.wander,
        "normal_p95_ms": baseline.normal_p95_ms,
        "learning_until": None
        if baseline.learning_until is None
        else baseline.learning_until.isoformat(),
    }


def baseline_from_json(stored: Mapping[str, Any]) -> SlowerBaseline:
    until = stored.get("learning_until")
    return SlowerBaseline(
        mode=stored["mode"],
        days=int(stored["days"]),
        window_minutes=int(stored["window_minutes"]),
        pool_requests=int(stored["pool_requests"]),
        threshold_index=int(stored["threshold_index"]),
        share_above=float(stored["share_above"]),
        wander=float(stored["wander"]),
        normal_p95_ms=None if stored["normal_p95_ms"] is None else float(stored["normal_p95_ms"]),
        learning_until=None if until is None else date.fromisoformat(until),
    )


# ---------------------------------------------------------------------- jobs


def horizon(now: datetime) -> list[datetime]:
    """The minutes one run writes: from `BEHIND` before now to `AHEAD` after."""
    start = now.replace(second=0, microsecond=0) - BEHIND
    return [start + index * MINUTE for index in range((BEHIND + AHEAD) // MINUTE)]


async def compute_project(
    engine: AsyncEngine, store: DetectionStore, project_id: uuid.UUID, *, now: datetime
) -> int | None:
    """Write the project's baselines for `horizon(now)`. How many services, or
    None when another run holds the project or the project is gone."""
    async with engine.connect() as connection, connection.begin():
        if not await try_lock(connection, _LOCK_PURPOSE, project_id):
            return None
        binding = await bind_project(connection, project_id)
        if binding is None:
            return None
        settings = await project_settings(connection, binding)
        detection = store.for_project(binding)
        width = settings.sensitivity.window_minutes
        minutes = horizon(now)
        first_seen = detection.first_seen(since=now - HISTORY)
        rows: list[dict[str, Any]] = []
        for batch in batched(sorted(first_seen), SERVICE_BATCH, strict=False):
            pools = {
                service: [
                    select_pool(minute, settings.zone, first_seen[service]) for minute in minutes
                ]
                for service in batch
            }
            history = _read_history(detection, pools, width)
            for service in batch:
                computed = slower_baselines(
                    history.get(service, {}),
                    minutes,
                    zone=settings.zone,
                    first_seen=first_seen[service],
                    width=width,
                )
                rows.extend(
                    {
                        "org_id": uuid.UUID(binding.org_id),
                        "project_id": project_id,
                        "service_name": service,
                        "signal": SLOWER,
                        "minute": minute,
                        "baseline": json.dumps(baseline_json(baseline)),
                    }
                    for minute, baseline in computed.items()
                )
        if rows:
            await connection.execute(_UPSERT, rows)
        await connection.execute(
            _PRUNE,
            {
                "org_id": uuid.UUID(binding.org_id),
                "project_id": project_id,
                "before": now - KEPT,
            },
        )
    return len(first_seen)


def _read_history(
    detection: ProjectDetection, pools: Mapping[str, Sequence[Pool]], width: int
) -> dict[str, dict[datetime, Minute]]:
    """One read per day offset, of the services whose pools use it."""
    history: dict[str, dict[datetime, Minute]] = {}
    users: dict[int, list[str]] = {}
    for service, service_pools in pools.items():
        for offset in history_spans(service_pools, width):
            users.setdefault(offset, []).append(service)
    spans = history_spans((pool for each in pools.values() for pool in each), width)
    for offset, services in sorted(users.items()):
        first, last = spans[offset]
        found = detection.service_minutes(services, start=first, end=last + MINUTE)
        for service, minutes in found.items():
            history.setdefault(service, {}).update(minutes)
    return history


def compute_request(project_id: uuid.UUID, org_id: uuid.UUID) -> JobRequest:
    """One project's baseline job. A second queued while one runs finds the project
    locked and does nothing."""
    return JobRequest(
        task=COMPUTE_BASELINES_TASK,
        queue=DETECTION_QUEUE,
        kwargs={"project_id": str(project_id)},
        org_id=str(org_id),
        expires_seconds=int(KEPT.total_seconds()),
        soft_limit_seconds=BASELINE_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=BASELINE_HARD_LIMIT_SECONDS,
    )


@task(COMPUTE_BASELINES_TASK)
def compute_baselines(*, project_id: str) -> None:
    store = store_for_process()
    if store is None:
        return
    project = uuid.UUID(project_id)
    services = with_engine(
        lambda engine: compute_project(engine, store, project, now=datetime.now(UTC))
    )
    if services is not None:
        logger.info("detection baselines written", project_id=project_id, services=services)


@task(SWEEP_BASELINES_TASK)
def sweep_baselines() -> None:
    """Hourly: one baseline job for each project detection judges."""

    async def queue_all(engine: AsyncEngine) -> int:
        async with engine.connect() as connection:
            projects = fair_order(await active_projects(connection, now=datetime.now(UTC)))
        await enqueue_all(
            engine, [compute_request(each.project_id, each.org_id) for each in projects]
        )
        return len(projects)

    queued = with_engine(queue_all)
    logger.info("detection baseline jobs queued", projects=queued)
