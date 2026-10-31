"""The hourly baseline: each service's normal for every minute of the next two
hours, built once from its history so the minute-by-minute job reads one small
row per service and signal (design, "The normal"; spec FR4 to FR8).

For each judged minute M, in the project's time zone:

- **The days.** Weekly mode is the same weekday over the last 4 weeks, when at
  least 3 of those days have history. Otherwise daily mode: earlier days of the
  same class, weekday or weekend, when at least 3 have history. Otherwise the
  signal is *learning*, and says until when. A day counts when its level pool has
  a minute the service existed and that is not kept out, so learning is counted
  in distinct days, and a weekly pattern that loses its days to exclusions falls
  back to the daily one for that minute (FR4, AC13).
- **The level pool** is M ± 15 minutes on each chosen day; **the dispersion
  pool**, M ± 180 minutes on the same days, gives the wander only. Neither reaches
  into M's own local day. In the autumn fall-back hour both UTC minutes of a
  local minute are pooled (FR7).
- **Like with like** (FR8): both pools are made of windows as long as the one
  judged, so each baseline is for one window length, the project's sensitivity's.
- **Kept out** (BR3): minutes inside a finding, unless an admin marked it a false
  alarm (D28); snoozed minutes (D25); minutes nexovue's intake refused for lack of
  a store, which read *data delayed*; and before 3 weeks, minutes the traffic
  shadow test found far below normal (FR6). A window counts by its last minute,
  as the simulation's do.
- **Losing traffic** waits for 21 days of history; before then only a stop is
  judged (FR6, D30). **Failing more** carries the service's burst profile over its
  last 28 days (D17, D18), which never holds a false alarm's minutes (D26).

Ported from `tools/detection-sim/detect_sim.py`'s `Pools`, `judge_*` and
`burst_profile`, and held to them by `test_detection_baselines.py`.
"""

from __future__ import annotations

import bisect
import json
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from itertools import batched
from typing import Any, Final
from zoneinfo import ZoneInfo

from nexovue_platform.jobs import JobRequest
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.detection import (
    COMPUTE_BASELINES_TASK,
    DETECTION_QUEUE,
    SWEEP_BASELINES_TASK,
)
from nexovue_scheduler.detection.method import (
    BUCKET_COUNT,
    BurstProfile,
    EndpointCounts,
    EndpointNormal,
    FailingBaseline,
    Minute,
    Mode,
    SlowerBaseline,
    TrafficBaseline,
    bucket_p95,
    burst_profile,
    moment_wander,
    runs_profile,
    threshold_share,
    traffic_spread,
    traffic_value,
    window_above,
)
from nexovue_scheduler.detection.projects import (
    active_projects,
    fair_order,
    project_settings,
    try_lock,
)
from nexovue_scheduler.detection.store import (
    DetectionStore,
    ProjectBinding,
    ProjectDetection,
    bind_project,
    store_for_process,
)
from nexovue_scheduler.jobs.queue import enqueue_all
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine

logger = get_logger(__name__)

MINUTE: Final = timedelta(minutes=1)
DAY: Final = timedelta(days=1)
LEVEL_HALF: Final = timedelta(minutes=15)
DISPERSION_HALF: Final = timedelta(minutes=180)
LEARNING_DAYS: Final = 3
POOL_DAYS: Final = 28
WEEK_DAYS: Final = 7
# FR6: *losing traffic* is judged once the weekly pattern exists, 3 weeks in.
TRAFFIC_HISTORY_DAYS: Final = 21
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
# A burst profile reads 28 days of a service's counts: fewer services at once.
PROFILE_BATCH: Final = 10
# A finding this short is one of the service's own bursts, which the burst profile
# keeps (the simulation's `SHORT_FINDING`, "history-volume" profile), unless it was
# marked a false alarm (D26).
SHORT_FINDING: Final = timedelta(minutes=30)
SLOWER: Final = "slower"
FAILING: Final = "failing"
TRAFFIC: Final = "traffic"
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
# The project's findings that reach into the pools: service and project scope.
_FINDINGS: Final = text(
    """
    SELECT scope, service_name, started_at, closed_at, false_alarm_at,
           false_alarm_returns_minutes
    FROM findings
    WHERE org_id = :org_id AND project_id = :project_id
      AND (closed_at IS NULL OR closed_at >= :since)
    """
)
# Each endpoint's excursions, recorded by the project job as it judged them (D18).
_ENDPOINT_RUNS: Final = text(
    """
    SELECT service_name, signals -> 'failing' -> 'endpoints' AS endpoints FROM detection_state
    WHERE org_id = :org_id AND project_id = :project_id
      AND signals -> 'failing' ? 'endpoints'
    """
)
# The minutes the project job recorded as snoozed or shadowed, per service.
_RECORDED: Final = text(
    """
    SELECT service_name, signals -> 'excluded' AS excluded FROM detection_state
    WHERE org_id = :org_id AND project_id = :project_id AND signals ? 'excluded'
    """
)
# Install-wide on purpose: a refused minute is delayed for every project (design,
# "When a minute is settled"). It names no tenant.
_REFUSED: Final = text("SELECT DISTINCT minute FROM intake_refusals WHERE minute >= :since")

# Under `detection_state.signals`, the minutes the project job keeps out (BR3).
EXCLUDED_KEY: Final = "excluded"
SNOOZED: Final = "snoozed"
SHADOW: Final = "shadow"


# ---------------------------------------------------------------- kept out


def merge_ranges(
    ranges: Iterable[tuple[datetime, datetime]],
) -> tuple[tuple[datetime, datetime], ...]:
    merged: list[tuple[datetime, datetime]] = []
    for first, last in sorted(ranges):
        if merged and first <= merged[-1][1] + MINUTE:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return tuple(merged)


class Exclusions:
    """Minutes kept out of a normal: merged, inclusive UTC ranges."""

    def __init__(self, ranges: Iterable[tuple[datetime, datetime]] = ()) -> None:
        self._ranges = merge_ranges(ranges)
        self._firsts = [first for first, _ in self._ranges]

    @property
    def ranges(self) -> tuple[tuple[datetime, datetime], ...]:
        return self._ranges

    def __or__(self, other: Exclusions) -> Exclusions:
        return Exclusions((*self._ranges, *other._ranges))

    def kept_out(self, minute: datetime) -> bool:
        index = bisect.bisect_right(self._firsts, minute) - 1
        return index >= 0 and self._ranges[index][1] >= minute

    def covers(self, first: datetime, last: datetime) -> bool:
        """Whether every minute of [first, last] is kept out."""
        index = bisect.bisect_right(self._firsts, first) - 1
        return index >= 0 and self._ranges[index][1] >= last


NO_EXCLUSIONS: Final = Exclusions()


@dataclass(frozen=True, slots=True)
class ServiceExclusions:
    """What one service's normal leaves out, and what its burst profile leaves out."""

    normal: Exclusions = NO_EXCLUSIONS
    profile: Exclusions = NO_EXCLUSIONS


@dataclass(frozen=True, slots=True)
class FindingSpan:
    """A finding's minutes, as `exclusions` reads them."""

    service_name: str | None
    first: datetime
    last: datetime
    false_alarm: bool
    # An admin's false alarm: its minutes return to the normal (D28).
    returns_minutes: bool


def exclusions(
    service: str,
    findings: Sequence[FindingSpan],
    *,
    recorded: Mapping[str, Sequence[tuple[datetime, datetime]]],
    refused: Sequence[datetime],
) -> ServiceExclusions:
    """BR3 for one service. A project finding (FR15) keeps every service's minutes out."""
    own = [f for f in findings if f.service_name in (service, None)]
    delayed = [(minute, minute) for minute in refused]
    recorded_ranges = [span for spans in recorded.values() for span in spans]
    normal = [(f.first, f.last) for f in own if not f.returns_minutes]
    # The profile keeps a short finding, which is one of the service's own bursts,
    # and never a false alarm's minutes (D26).
    profile = [(f.first, f.last) for f in own if f.false_alarm or f.last - f.first > SHORT_FINDING]
    return ServiceExclusions(
        normal=Exclusions([*normal, *delayed, *recorded_ranges]),
        profile=Exclusions([*profile, *delayed, *recorded_ranges]),
    )


# ------------------------------------------------------------------- the days


@dataclass(frozen=True, slots=True)
class PoolDay:
    """One past day a minute is compared with: M's local clock time on it, once,
    or twice in an autumn fall-back hour, and the start of M's own local day, which
    no pool reaches."""

    offset: int
    centres: tuple[datetime, ...]
    day_start: datetime

    @property
    def centre(self) -> datetime:
        return self.centres[0]

    @property
    def level_ranges(self) -> tuple[tuple[datetime, datetime], ...]:
        """The level pool's minutes, first and last inclusive."""
        return self._around(LEVEL_HALF)

    @property
    def dispersion_ranges(self) -> tuple[tuple[datetime, datetime], ...]:
        return self._around(DISPERSION_HALF)

    @property
    def level(self) -> tuple[datetime, datetime]:
        """The level pool's first and last minutes."""
        return _span(self.level_ranges)

    @property
    def dispersion(self) -> tuple[datetime, datetime]:
        return _span(self.dispersion_ranges)

    def _around(self, half: timedelta) -> tuple[tuple[datetime, datetime], ...]:
        stop = self.day_start - MINUTE
        return merge_ranges(
            (centre - half, min(centre + half, stop))
            for centre in self.centres
            if centre - half <= stop
        )


def _span(ranges: Sequence[tuple[datetime, datetime]]) -> tuple[datetime, datetime]:
    return ranges[0][0], ranges[-1][1]


@dataclass(frozen=True, slots=True)
class Pool:
    mode: Mode
    days: tuple[PoolDay, ...]


def _is_weekend(day: date) -> bool:
    return day.weekday() >= _WEEKEND_FROM


def _local_midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time(), tzinfo=zone).astimezone(UTC)


def _instants(wall: datetime, zone: ZoneInfo) -> tuple[datetime, ...]:
    """The UTC instants of a local clock time: two in a fall-back hour (FR7). A time
    a spring-forward skips has none, and keeps the earlier offset's instant."""
    found = []
    for fold in (0, 1):
        instant = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == wall and instant not in found:
            found.append(instant)
    return tuple(found) or (wall.replace(tzinfo=zone).astimezone(UTC),)


def select_pool(
    minute: datetime,
    zone: ZoneInfo,
    first_seen: datetime,
    kept_out: Exclusions = NO_EXCLUSIONS,
) -> Pool:
    """FR4 and FR5's days for one minute. The same local clock time on each past
    day, so a daylight-saving change never shifts the normal (FR7). A day counts
    when its level pool holds a minute the service existed that is not kept out."""
    local = minute.astimezone(zone)
    day_start = _local_midnight(local.date(), zone)
    wall = local.replace(tzinfo=None)
    weekly: list[PoolDay] = []
    same_class: list[PoolDay] = []
    for offset in range(1, POOL_DAYS + 1):
        past = wall - timedelta(days=offset)
        day = PoolDay(offset, _instants(past, zone), day_start)
        if day.centre < first_seen or not _has_history(day, first_seen, kept_out):
            continue
        if offset % WEEK_DAYS == 0:
            weekly.append(day)
        if _is_weekend(past.date()) == _is_weekend(local.date()):
            same_class.append(day)
    if len(weekly) >= LEARNING_DAYS:
        return Pool("weekly", tuple(weekly))
    if len(same_class) >= LEARNING_DAYS:
        return Pool("daily", tuple(same_class))
    return Pool("learning", ())


def _has_history(day: PoolDay, first_seen: datetime, kept_out: Exclusions) -> bool:
    return any(
        not kept_out.covers(max(first, first_seen), last)
        for first, last in day.level_ranges
        if last >= first_seen
    )


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


def traffic_judged_from(zone: ZoneInfo, first_seen: datetime) -> date:
    """The local date *losing traffic* is judged from: 3 weeks of history (FR6)."""
    return first_seen.astimezone(zone).date() + timedelta(days=TRAFFIC_HISTORY_DAYS)


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


Moments = tuple[int, int, int, int, int]


class _Counts:
    """One series of window (requests, failures), with the sums its pools take in
    O(1): the level pool's two sums, and the dispersion pool's five moments. A
    window that ends at a minute kept out adds nothing."""

    def __init__(self, windows: Sequence[EndpointCounts], usable: Sequence[bool]) -> None:
        sums: list[Moments] = [(0, 0, 0, 0, 0)]
        n_sum = e_sum = nn = ee = ne = 0
        for (n, e), ok in zip(windows, usable, strict=True):
            if ok:
                n_sum, e_sum = n_sum + n, e_sum + e
                nn, ee, ne = nn + n * n, ee + e * e, ne + n * e
            sums.append((n_sum, e_sum, nn, ee, ne))
        self._sums = sums

    def moments(self, a: int, b: int) -> Moments:
        high, low = self._sums[b], self._sums[a]
        return (
            high[0] - low[0],
            high[1] - low[1],
            high[2] - low[2],
            high[3] - low[3],
            high[4] - low[4],
        )


class _Block:
    """One day offset's minutes, dense, with the sums its pools take in O(1).

    `_level[j]` sums the window histograms ending before minute j. The dispersion
    sums, for one L, are built on first use: summed over the windows of a range,
    their requests, their counts above L, and the squares and product of those.
    Windows ending at a minute kept out, or before the service existed, add nothing.
    """

    def __init__(
        self,
        first: datetime,
        last: datetime,
        history: Mapping[datetime, Minute],
        width: int,
        usable: Callable[[datetime], bool],
    ) -> None:
        self._first = first
        length = (last - first) // MINUTE + 1
        stamps = [first + index * MINUTE for index in range(length)]
        minutes = [history.get(stamp, _EMPTY) for stamp in stamps]
        self._minutes = minutes
        self._width = width
        self._usable = [usable(stamp) for stamp in stamps]
        running = [0] * BUCKET_COUNT
        totals: list[list[int]] = [running[:]]
        requests, errors = [0], [0]
        for minute in minutes:
            running = [a + b for a, b in zip(running, minute.buckets, strict=True)]
            totals.append(running)
            requests.append(requests[-1] + minute.requests)
            errors.append(errors[-1] + minute.errors)
        self._windows: list[list[int]] = []
        self._dispersion_requests: list[int] = []
        self.window_counts: list[EndpointCounts] = []
        level = [0] * BUCKET_COUNT
        self._level: list[list[int]] = [level]
        for end in range(1, length + 1):
            start = max(end - width, 0)
            window = [a - b for a, b in zip(totals[end], totals[start], strict=True)]
            if not self._usable[end - 1]:
                window = [0] * BUCKET_COUNT
            self._windows.append(window)
            counted = (requests[end] - requests[start], errors[end] - errors[start])
            self.window_counts.append(counted)
            self._dispersion_requests.append(counted[0] if self._usable[end - 1] else 0)
            level = [a + b for a, b in zip(level, window, strict=True)]
            self._level.append(level)
        self._dispersion: dict[int, list[Moments]] = {}
        self.failing = _Counts(self.window_counts, self._usable)
        self._endpoints: dict[str, _Counts] = {}
        self.endpoint_names = sorted({name for m in minutes for name in m.endpoints})

    def index(self, minute: datetime) -> int:
        return (minute - self._first) // MINUTE

    def usable(self, minute: datetime) -> bool:
        index = self.index(minute)
        return 0 <= index < len(self._usable) and self._usable[index]

    def level(self, first: datetime, last: datetime) -> list[int]:
        a, b = self.index(first), self.index(last) + 1
        return [high - low for high, low in zip(self._level[b], self._level[a], strict=True)]

    def dispersion(self, first: datetime, last: datetime, threshold: int) -> Moments:
        sums = self._dispersion.get(threshold)
        if sums is None:
            sums = [(0, 0, 0, 0, 0)]
            n_hist = above = nn = aa = na = 0
            for window, n in zip(self._windows, self._dispersion_requests, strict=True):
                a = sum(window[threshold:])
                n_hist += sum(window)
                above += a
                nn += n * n
                aa += a * a
                na += n * a
                sums.append((n_hist, above, nn, aa, na))
            self._dispersion[threshold] = sums
        high, low = sums[self.index(last) + 1], sums[self.index(first)]
        n_hist, above, nn, aa, na = (h - lo for h, lo in zip(high, low, strict=True))
        return n_hist, above, nn, aa, na

    def endpoint(self, name: str) -> _Counts:
        counts = self._endpoints.get(name)
        if counts is None:
            per_minute = [m.endpoints.get(name, (0, 0)) for m in self._minutes]
            windows: list[EndpointCounts] = []
            n_run = e_run = 0
            for index, (n, e) in enumerate(per_minute):
                n_run, e_run = n_run + n, e_run + e
                if index >= self._width:
                    old_n, old_e = per_minute[index - self._width]
                    n_run, e_run = n_run - old_n, e_run - old_e
                windows.append((n_run, e_run))
            counts = _Counts(windows, self._usable)
            self._endpoints[name] = counts
        return counts

    def traffic_values(self, first: datetime, last: datetime) -> list[float]:
        """Each usable window's count on the square-root scale."""
        return [
            traffic_value(self.window_counts[index][0])
            for index in range(self.index(first), self.index(last) + 1)
            if self._usable[index]
        ]


@dataclass(frozen=True, slots=True)
class Baselines:
    """One minute's normal for each signal."""

    slower: SlowerBaseline
    failing: FailingBaseline
    traffic: TrafficBaseline


def service_baselines(
    history: Mapping[datetime, Minute],
    minutes: Sequence[datetime],
    *,
    zone: ZoneInfo,
    first_seen: datetime,
    width: int,
    kept_out: Exclusions = NO_EXCLUSIONS,
    profiles: Mapping[date, BurstProfile] | None = None,
    endpoint_profiles: Mapping[date, Mapping[str, BurstProfile]] | None = None,
) -> dict[datetime, Baselines]:
    """Each signal's normal at each minute, for windows of `width` minutes.

    `history` holds the service's counted minutes over `history_spans`, with their
    endpoints' counts; a minute it lacks had no requests. `profiles` holds the
    burst profile for each local day of `minutes`, and `endpoint_profiles` each
    endpoint's."""
    pools = {minute: select_pool(minute, zone, first_seen, kept_out) for minute in minutes}

    def usable(stamp: datetime) -> bool:
        return stamp >= first_seen and not kept_out.kept_out(stamp)

    blocks = {
        offset: _Block(first, last, history, width, usable)
        for offset, (first, last) in history_spans(pools.values(), width).items()
    }
    found: dict[datetime, Baselines] = {}
    for minute, pool in pools.items():
        local_date = minute.astimezone(zone).date()
        if pool.mode == "learning":
            until = learning_until(minute, zone, first_seen)
            # Traffic waits for its class's third day and for 3 weeks (FR6).
            traffic_until = max(until, traffic_judged_from(zone, first_seen))
            found[minute] = Baselines(
                slower=SlowerBaseline("learning", 0, width, 0, 1, 0.0, 0.0, None, until),
                failing=FailingBaseline("learning", 0, width, 0, 0, 0.0, learning_until=until),
                traffic=TrafficBaseline(
                    "learning", 0, width, None, 1.0, learning_until=traffic_until
                ),
            )
            continue
        failing = _failing(pool, blocks, width)
        endpoint_day = {} if endpoint_profiles is None else endpoint_profiles.get(local_date, {})
        found[minute] = Baselines(
            slower=_slower(pool, blocks, width),
            failing=replace(
                failing,
                profile=None if profiles is None else profiles.get(local_date),
                endpoints={
                    name: replace(normal, profile=endpoint_day.get(name))
                    for name, normal in failing.endpoints.items()
                },
            ),
            traffic=_traffic(pool, blocks, width, minute, zone, first_seen),
        )
    return found


def _slower(pool: Pool, blocks: Mapping[int, _Block], width: int) -> SlowerBaseline:
    level = [0] * BUCKET_COUNT
    for day in pool.days:
        for first, last in day.level_ranges:
            part = blocks[day.offset].level(first, last)
            level = [a + b for a, b in zip(level, part, strict=True)]
    threshold, share = threshold_share(level)
    moments = [0, 0, 0, 0, 0]
    for day in pool.days:
        for first, last in day.dispersion_ranges:
            spread = blocks[day.offset].dispersion(first, last, threshold)
            moments = [a + b for a, b in zip(moments, spread, strict=True)]
    return SlowerBaseline(
        mode=pool.mode,
        days=len(pool.days),
        window_minutes=width,
        pool_requests=sum(level),
        threshold_index=threshold,
        share_above=share,
        wander=moment_wander(*(float(value) for value in moments)),
        normal_p95_ms=bucket_p95(level),
    )


def _pooled(
    pool: Pool, blocks: Mapping[int, _Block], counts: Callable[[_Block], _Counts]
) -> tuple[int, int, float]:
    """A series' level pool sums and its wander from the dispersion pool."""
    requests = failures = 0
    moments = [0, 0, 0, 0, 0]
    for day in pool.days:
        block = blocks[day.offset]
        series = counts(block)
        for first, last in day.level_ranges:
            n, e, *_ = series.moments(block.index(first), block.index(last) + 1)
            requests, failures = requests + n, failures + e
        for first, last in day.dispersion_ranges:
            part = series.moments(block.index(first), block.index(last) + 1)
            moments = [a + b for a, b in zip(moments, part, strict=True)]
    return requests, failures, moment_wander(*(float(value) for value in moments))


def _failing(pool: Pool, blocks: Mapping[int, _Block], width: int) -> FailingBaseline:
    requests, failures, wander = _pooled(pool, blocks, lambda block: block.failing)
    names = sorted({name for day in pool.days for name in blocks[day.offset].endpoint_names})
    endpoints = {}
    for name in names:
        n, e, rho = _pooled(pool, blocks, _endpoint_series(name))
        if n > 0:
            endpoints[name] = EndpointNormal(n, e, rho)
    return FailingBaseline(
        mode=pool.mode,
        days=len(pool.days),
        window_minutes=width,
        pool_requests=requests,
        pool_failures=failures,
        wander=wander,
        endpoints=endpoints,
    )


def _endpoint_series(name: str) -> Callable[[_Block], _Counts]:
    return lambda block: block.endpoint(name)


def _traffic(
    pool: Pool,
    blocks: Mapping[int, _Block],
    width: int,
    minute: datetime,
    zone: ZoneInfo,
    first_seen: datetime,
) -> TrafficBaseline:
    values = [
        value
        for day in pool.days
        for first, last in day.level_ranges
        for value in blocks[day.offset].traffic_values(max(first, first_seen), last)
        if last >= first_seen
    ]
    median, spread = traffic_spread(values)
    judged_from = traffic_judged_from(zone, first_seen)
    stops_only = minute.astimezone(zone).date() < judged_from
    return TrafficBaseline(
        mode=pool.mode,
        days=len(pool.days),
        window_minutes=width,
        median=median,
        spread=spread,
        stops_only=stops_only,
        learning_until=judged_from if stops_only else None,
    )


# ------------------------------------------------------------ burst profile


def failing_profile(
    counts: Mapping[datetime, EndpointCounts],
    normals: Mapping[datetime, tuple[float, float]],
    *,
    day_start: datetime,
    width: int,
    kept_out: Exclusions,
) -> BurstProfile:
    """The burst profile for one local day: the 28 days before its midnight (D17).

    Whether each past window passed the base test is rebuilt from its minutes and
    the normal its reading was judged by. Judged days run from the first such
    reading; learning days hold none, as in the simulation."""
    start = day_start - POOL_DAYS * DAY
    length = (day_start - start) // MINUTE
    per_minute = [
        counts.get(start + (index - width + 1) * MINUTE, (0, 0))
        for index in range(length + width - 1)
    ]
    windows: list[EndpointCounts | None] = []
    above: list[bool] = []
    for index in range(length):
        minute = start + index * MINUTE
        span = per_minute[index : index + width]
        normal = normals.get(minute)
        above.append(normal is not None and window_above(span, *normal))
        if kept_out.kept_out(minute):
            windows.append(None)
        else:
            windows.append((sum(n for n, _ in span), sum(e for _, e in span)))
    return burst_profile(windows, above, width, judged_days(normals, day_start))


def judged_days(normals: Mapping[datetime, tuple[float, float]], day_start: datetime) -> float:
    """Days of the profile's 28 that were judged: learning days hold no record."""
    start = day_start - POOL_DAYS * DAY
    judged = [minute for minute in normals if start <= minute < day_start]
    return (day_start - min(judged)) / DAY if judged else 0.0


def endpoint_profiles(
    windows: Mapping[datetime, Mapping[str, EndpointCounts]],
    runs: Mapping[str, Sequence[tuple[datetime, datetime]]],
    *,
    day_start: datetime,
    width: int,
    kept_out: Exclusions,
    days: float,
) -> dict[str, BurstProfile]:
    """Each endpoint's own burst profile for one local day (D18 per endpoint, FR2).

    Heights come from its window counts in the readings, which hold an endpoint
    only over the request floor. Its excursions were recorded as judged, since a
    reading keeps no endpoint's normal to rebuild them from; each is cut where
    minutes are kept out, as the simulation's runs are."""
    start = day_start - POOL_DAYS * DAY
    minutes = [start + index * MINUTE for index in range((day_start - start) // MINUTE)]
    names = sorted({name for counted in windows.values() for name in counted} | set(runs))
    found = {}
    for name in names:
        series = [
            None if kept_out.kept_out(minute) else windows.get(minute, {}).get(name)
            for minute in minutes
        ]
        lengths = [
            length
            for first, last in runs.get(name, ())
            for length in _usable_lengths(
                max(first, start), min(last, day_start - MINUTE), kept_out
            )
        ]
        found[name] = runs_profile(series, lengths, width, days)
    return found


def _usable_lengths(first: datetime, last: datetime, kept_out: Exclusions) -> list[int]:
    """The lengths, in minutes, of the runs of [first, last] that are not kept out."""
    lengths, run = [], 0
    for minute in span_minutes(first, last):
        if kept_out.kept_out(minute):
            if run:
                lengths.append(run)
            run = 0
        else:
            run += 1
    if run:
        lengths.append(run)
    return lengths


def span_minutes(first: datetime, last: datetime) -> list[datetime]:
    return [first + index * MINUTE for index in range((last - first) // MINUTE + 1)]


# -------------------------------------------------------------------- stored


def _iso(day: date | None) -> str | None:
    return None if day is None else day.isoformat()


def _day(stored: str | None) -> date | None:
    return None if stored is None else date.fromisoformat(stored)


def slower_json(baseline: SlowerBaseline) -> dict[str, Any]:
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
        "learning_until": _iso(baseline.learning_until),
    }


def slower_from_json(stored: Mapping[str, Any]) -> SlowerBaseline:
    return SlowerBaseline(
        mode=stored["mode"],
        days=int(stored["days"]),
        window_minutes=int(stored["window_minutes"]),
        pool_requests=int(stored["pool_requests"]),
        threshold_index=int(stored["threshold_index"]),
        share_above=float(stored["share_above"]),
        wander=float(stored["wander"]),
        normal_p95_ms=None if stored["normal_p95_ms"] is None else float(stored["normal_p95_ms"]),
        learning_until=_day(stored.get("learning_until")),
    )


def failing_json(baseline: FailingBaseline) -> dict[str, Any]:
    """`detection_baselines.baseline` for *failing more*, its endpoints and its profile."""
    profile = baseline.profile
    return {
        "mode": baseline.mode,
        "days": baseline.days,
        "window_minutes": baseline.window_minutes,
        "pool_requests": baseline.pool_requests,
        "pool_failures": baseline.pool_failures,
        "wander": baseline.wander,
        "profile": None if profile is None else _profile_json(profile),
        "endpoints": {
            name: [
                normal.pool_requests,
                normal.pool_failures,
                normal.wander,
                None if normal.profile is None else _profile_json(normal.profile),
            ]
            for name, normal in baseline.endpoints.items()
        },
        "learning_until": _iso(baseline.learning_until),
    }


def _profile_json(profile: BurstProfile) -> dict[str, Any]:
    return {
        "heights": {str(level): height for level, height in profile.heights.items()},
        "minutes": profile.minutes,
        "excursions": profile.excursions,
        "days": profile.days,
    }


def _profile_from_json(stored: Mapping[str, Any] | None) -> BurstProfile | None:
    if stored is None:
        return None
    return BurstProfile(
        heights={int(level): float(h) for level, h in stored["heights"].items()},
        minutes=int(stored["minutes"]),
        excursions=int(stored["excursions"]),
        days=float(stored["days"]),
    )


def failing_from_json(stored: Mapping[str, Any]) -> FailingBaseline:
    return FailingBaseline(
        mode=stored["mode"],
        days=int(stored["days"]),
        window_minutes=int(stored["window_minutes"]),
        pool_requests=int(stored["pool_requests"]),
        pool_failures=int(stored["pool_failures"]),
        wander=float(stored["wander"]),
        profile=_profile_from_json(stored.get("profile")),
        endpoints={
            name: EndpointNormal(int(n), int(e), float(rho), _profile_from_json(profile))
            for name, (n, e, rho, profile) in stored.get("endpoints", {}).items()
        },
        learning_until=_day(stored.get("learning_until")),
    )


def traffic_json(baseline: TrafficBaseline) -> dict[str, Any]:
    """`detection_baselines.baseline` for *losing traffic*, on the square-root scale."""
    return {
        "mode": baseline.mode,
        "days": baseline.days,
        "window_minutes": baseline.window_minutes,
        "median": baseline.median,
        "spread": baseline.spread,
        "stops_only": baseline.stops_only,
        "learning_until": _iso(baseline.learning_until),
    }


def traffic_from_json(stored: Mapping[str, Any]) -> TrafficBaseline:
    return TrafficBaseline(
        mode=stored["mode"],
        days=int(stored["days"]),
        window_minutes=int(stored["window_minutes"]),
        median=None if stored["median"] is None else float(stored["median"]),
        spread=float(stored["spread"]),
        stops_only=bool(stored["stops_only"]),
        learning_until=_day(stored.get("learning_until")),
    )


def baselines_json(baselines: Baselines) -> dict[str, dict[str, Any]]:
    """One minute's three rows, by signal."""
    return {
        SLOWER: slower_json(baselines.slower),
        FAILING: failing_json(baselines.failing),
        TRAFFIC: traffic_json(baselines.traffic),
    }


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
        zone = settings.zone
        minutes = horizon(now)
        first_seen = detection.first_seen(since=now - HISTORY)
        kept = await project_exclusions(
            connection, binding, sorted(first_seen), now=now, width=width
        )
        days = sorted({minute.astimezone(zone).date() for minute in minutes})
        endpoint_runs = await recorded_endpoint_runs(connection, binding)
        rows: list[dict[str, Any]] = []
        for batch in batched(sorted(first_seen), SERVICE_BATCH, strict=False):
            pools = {
                service: [
                    select_pool(minute, zone, first_seen[service], kept[service].normal)
                    for minute in minutes
                ]
                for service in batch
            }
            history = _read_history(detection, pools, width)
            profiles, endpoint_day_profiles = _read_profiles(
                detection, batch, days, zone, width, kept, endpoint_runs
            )
            for service in batch:
                computed = service_baselines(
                    history.get(service, {}),
                    minutes,
                    zone=zone,
                    first_seen=first_seen[service],
                    width=width,
                    kept_out=kept[service].normal,
                    profiles=profiles.get(service, {}),
                    endpoint_profiles=endpoint_day_profiles.get(service, {}),
                )
                rows.extend(
                    {
                        "org_id": uuid.UUID(binding.org_id),
                        "project_id": project_id,
                        "service_name": service,
                        "signal": signal,
                        "minute": minute,
                        "baseline": json.dumps(stored),
                    }
                    for minute, baselines in computed.items()
                    for signal, stored in baselines_json(baselines).items()
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


async def project_exclusions(
    connection: AsyncConnection,
    binding: ProjectBinding,
    services: Sequence[str],
    *,
    now: datetime,
    width: int,
) -> dict[str, ServiceExclusions]:
    """Each service's minutes kept out of its normal and its burst profile (BR3)."""
    since = now - HISTORY - DAY
    key = {"org_id": uuid.UUID(binding.org_id), "project_id": uuid.UUID(binding.project_id)}
    lead = (width - 1) * MINUTE
    findings = [
        FindingSpan(
            service_name=row.service_name if row.scope == "service" else None,
            first=_floor(row.started_at) - lead,
            last=_floor(row.closed_at or now),
            false_alarm=row.false_alarm_at is not None,
            returns_minutes=bool(row.false_alarm_returns_minutes),
        )
        for row in await connection.execute(_FINDINGS, {**key, "since": since})
    ]
    recorded = {
        row.service_name: recorded_ranges(row.excluded)
        for row in await connection.execute(_RECORDED, key)
    }
    refused = [_floor(row.minute) for row in await connection.execute(_REFUSED, {"since": since})]
    return {
        service: exclusions(service, findings, recorded=recorded.get(service, {}), refused=refused)
        for service in services
    }


async def recorded_endpoint_runs(
    connection: AsyncConnection, binding: ProjectBinding
) -> dict[str, dict[str, list[tuple[datetime, datetime]]]]:
    """Each service's endpoints' recorded excursions: their first and last minutes."""
    key = {"org_id": uuid.UUID(binding.org_id), "project_id": uuid.UUID(binding.project_id)}
    return {
        row.service_name: {
            name: [
                (datetime.fromisoformat(first), datetime.fromisoformat(last))
                for first, last in record.get("runs", [])
            ]
            for name, record in (row.endpoints or {}).items()
        }
        for row in await connection.execute(_ENDPOINT_RUNS, key)
    }


def recorded_ranges(stored: Mapping[str, Any] | None) -> dict[str, list[tuple[datetime, datetime]]]:
    """`detection_state.signals["excluded"]`: ISO ranges by why they were kept out."""
    return {
        reason: [
            (datetime.fromisoformat(first), datetime.fromisoformat(last)) for first, last in spans
        ]
        for reason, spans in (stored or {}).items()
    }


def _floor(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(second=0, microsecond=0)


def _read_history(
    detection: ProjectDetection, pools: Mapping[str, Sequence[Pool]], width: int
) -> dict[str, dict[datetime, Minute]]:
    """One read per day offset, of the services whose pools use it, with their
    endpoints' counts."""
    history: dict[str, dict[datetime, Minute]] = {}
    users: dict[int, list[str]] = {}
    for service, service_pools in pools.items():
        for offset in history_spans(service_pools, width):
            users.setdefault(offset, []).append(service)
    spans = history_spans((pool for each in pools.values() for pool in each), width)
    for offset, services in sorted(users.items()):
        first, last = spans[offset]
        found = detection.service_minutes(services, start=first, end=last + MINUTE)
        endpoints = detection.endpoint_minutes(services, start=first, end=last + MINUTE)
        for service, minutes in found.items():
            counted = endpoints.get(service, {})
            history.setdefault(service, {}).update(
                {
                    minute: replace(each, endpoints=counted.get(minute, {}))
                    for minute, each in minutes.items()
                }
            )
    return history


def _read_profiles(
    detection: ProjectDetection,
    services: Sequence[str],
    days: Sequence[date],
    zone: ZoneInfo,
    width: int,
    kept: Mapping[str, ServiceExclusions],
    endpoint_runs: Mapping[str, Mapping[str, Sequence[tuple[datetime, datetime]]]],
) -> tuple[dict[str, dict[date, BurstProfile]], dict[str, dict[date, dict[str, BurstProfile]]]]:
    """Each service's burst profile, and its endpoints', for each local day the run's
    minutes fall on. A service with no judged reading in 28 days has no history to
    profile."""
    starts = {day: _local_midnight(day, zone) for day in days}
    first = min(starts.values()) - POOL_DAYS * DAY
    last = max(starts.values())
    found: dict[str, dict[date, BurstProfile]] = {}
    endpoints: dict[str, dict[date, dict[str, BurstProfile]]] = {}
    for batch in batched(services, PROFILE_BATCH, strict=False):
        normals = detection.failing_normals(
            list(batch), start=first, end=last, window_minutes=width
        )
        judged = sorted(normals)
        counts = detection.service_counts(judged, start=first - (width - 1) * MINUTE, end=last)
        windows = detection.endpoint_windows(judged, start=first, end=last, window_minutes=width)
        for service in judged:
            profile_kept = kept[service].profile
            found[service] = {
                day: failing_profile(
                    counts.get(service, {}),
                    normals[service],
                    day_start=start,
                    width=width,
                    kept_out=profile_kept,
                )
                for day, start in starts.items()
            }
            endpoints[service] = {
                day: endpoint_profiles(
                    windows.get(service, {}),
                    endpoint_runs.get(service, {}),
                    day_start=start,
                    width=width,
                    kept_out=profile_kept,
                    days=judged_days(normals[service], start),
                )
                for day, start in starts.items()
            }
    return found, endpoints


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
