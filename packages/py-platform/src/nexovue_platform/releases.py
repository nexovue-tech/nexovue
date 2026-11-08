"""F28: how a release is compared, pure, so the API and the scheduler use one definition
(design 4, D26; plan T1.9).

No store and no clock: each service reads its own rows (`releases/queries.py` in the
API, `releases/store.py` in the scheduler) and hands them here. This package may not
touch a database (`test_cross_service_contracts.py`).

**The ordered list** (design, *The algorithm*). `choose_method` takes one period, the
service's per-minute rows by version from before the period to the settled minute, and
answers with a reason, or with up to two blocks: *during the rollout*, both versions
in the same minutes (`side_by_side`), and *since the rollout finished*, the new version
against the previous version's equal stretch before it (`version_before_after`). A
service sending no version, with a deploy reported, compares by time around it.

**The words** (FR24). Failure share reads `higher` or `lower` at a two-proportion z of
3, `about_same` only when the traffic rules out a change of one percentage point, and
`not_clearly_different` otherwise. p95 reads `higher` or `lower` past a ratio of 1.2,
about one latency bucket. Every threshold is a starting guess, named below.

**The name check** (FR9). The webhook refuses, and the worker folds, a name holding a
control character, a bidirectional control, a Unicode tag character, or the reserved
`__other__`. Go cannot import this, so both read
`packages/contracts/fixtures/release-names.json`.
"""

from __future__ import annotations

import math
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from nexovue_platform.slo import BUCKET_COUNT, LOG_EDGES

# ------------------------------------------------------------------- versions

# The worker's fold for versions past its caps or failing the name check
# (`derive.OtherVersion`), and a span with no `service.version`. Neither is a release.
OTHER_VERSION: Final = "__other__"
NO_VERSION: Final = ""
# `releases.service_name` and `releases.version` (`models.NAME_MAX_LENGTH`).
NAME_MAX_LENGTH: Final = 200

# ---------------------------------------------------------- floors and rules

# Each side needs this many requests to read failure share, and to read p95 (FR23):
# F6's floors (`detection/method.py`), held equal by `test_release_sync.py`.
FAILURE_FLOOR: Final = 20
P95_FLOOR: Final = 200
# A change in failure share is read at this many standard errors (FR24).
Z_THRESHOLD: Final = 3.0
# `about_same` needs the difference plus three standard errors inside this.
ABOUT_SAME_MARGIN: Final = 0.01
# One latency bucket is about 19% wide (`derive/services.go`).
P95_RATIO: Final = 1.2
# Each version's first minutes are left out of its p95 (FR48): new instances start cold.
WARM_UP: Final = timedelta(minutes=5)
# A period needs this many settled minutes before anything is compared.
TOO_RECENT_UNDER: Final = timedelta(minutes=5)
# The rollout has finished once the previous version holds under this share of
# requests for this many minutes with traffic in a row (FR47).
ROLLOUT_DONE_SHARE: Final = 0.01
ROLLOUT_DONE_MINUTES: Final = 5
# Rows are read from at least this long before the period's start (design, step 4).
LOOKBACK_FLOOR: Final = timedelta(hours=1)
# The windows offered (FR22), the first the default.
WINDOWS: Final = (timedelta(hours=1), timedelta(hours=6), timedelta(hours=24))
DEFAULT_WINDOW: Final = WINDOWS[0]
# `service_versions_1m` keeps at most this many days on any plan (`000038`'s TTL).
VERSION_ROLLUP_DAYS: Final = 35
# With no settled minute yet, a read ends this long before now, marked "as of".
UNSETTLED_LAG: Final = timedelta(minutes=3)
_MINUTE: Final = timedelta(minutes=1)
_P95_SHARE: Final = 0.95
_SHARE_EPSILON: Final = 1e-9
_COUNT_EPSILON: Final = 1e-12

# ------------------------------------------------------------- the vocabulary

HIGHER: Final = "higher"
LOWER: Final = "lower"
ABOUT_SAME: Final = "about_same"
NOT_CLEARLY_DIFFERENT: Final = "not_clearly_different"
READINGS: Final = (HIGHER, LOWER, ABOUT_SAME, NOT_CLEARLY_DIFFERENT)
# Why a cell has no reading.
TOO_LITTLE_TRAFFIC: Final = "too_little_traffic"
WARM_UP_NOTE: Final = "warm_up"

SIDE_BY_SIDE: Final = "side_by_side"
VERSION_BEFORE_AFTER: Final = "version_before_after"
TIME_BEFORE_AFTER: Final = "time_before_after"
METHODS: Final = (SIDE_BY_SIDE, VERSION_BEFORE_AFTER, TIME_BEFORE_AFTER)

# FR21's reasons, and FR43's flag, which reads as one.
NO_PREVIOUS_VERSION: Final = "no_previous_version"
ALREADY_RUNNING: Final = "already_running"
TOO_RECENT: Final = "too_recent"
NUMBERS_EXPIRED: Final = "numbers_expired"
NO_VERSION_NO_DEPLOY: Final = "no_version_no_deploy"
NEW_VERSION_NOT_SEEN: Final = "new_version_not_seen"
NO_REQUEST_TRAFFIC: Final = "no_request_traffic"
REASONS: Final = (
    NO_PREVIOUS_VERSION,
    ALREADY_RUNNING,
    TOO_LITTLE_TRAFFIC,
    TOO_RECENT,
    NUMBERS_EXPIRED,
    NO_VERSION_NO_DEPLOY,
    NEW_VERSION_NOT_SEEN,
    NO_REQUEST_TRAFFIC,
)

# The blocks (D34).
ROLLOUT: Final = "rollout"
AFTER_ROLLOUT: Final = "after_rollout"
AROUND_DEPLOY: Final = "around_deploy"

# How a period's start was learned (FR42): counted, or told by a sender.
SEEN: Final = "seen"
REPORTED: Final = "reported"
START_BASES: Final = (SEEN, REPORTED)
# What a sender says of a deploy (spec, *States*); a release keeps the last one.
DEPLOY_STATES: Final = ("started", "finished", "failed", "rolled_back")

# ----------------------------------------------------------------- the check

_BIDI_RANGES: Final = ((0x202A, 0x202E), (0x2066, 0x2069))
_TAG_RANGE: Final = (0xE0000, 0xE007F)
_CONTROL: Final = "Cc"


def name_allowed(name: str) -> bool:
    """Whether a version or service name passes the one name check (FR9)."""
    if name == OTHER_VERSION:
        return False
    return not any(_refused_character(character) for character in name)


def _refused_character(character: str) -> bool:
    point = ord(character)
    if unicodedata.category(character) == _CONTROL:
        return True
    if any(low <= point <= high for low, high in _BIDI_RANGES):
        return True
    return _TAG_RANGE[0] <= point <= _TAG_RANGE[1]


def storable(service_name: str, version: str) -> bool:
    """Whether a rollup row can become a release: both names fit Postgres, and the
    version is a real one (FR18). The sync skips, and counts, any other."""
    if version in (NO_VERSION, OTHER_VERSION) or not name_allowed(version):
        return False
    return all(
        len(name) <= NAME_MAX_LENGTH and "\x00" not in name for name in (service_name, version)
    )


# --------------------------------------------------------------- arithmetic


def bucket_p95(buckets: Sequence[int]) -> float | None:
    """The p95 of bucket counts, log-linear inside its bucket; None when empty."""
    total = sum(buckets)
    if total <= 0:
        return None
    target = _P95_SHARE * total
    running = 0
    for index, count in enumerate(buckets):
        running += count
        if running >= target - _SHARE_EPSILON:
            below = running - count
            fraction = min(max((target - below) / max(count, _COUNT_EPSILON), 0.0), 1.0)
            low, high = LOG_EDGES[index], LOG_EDGES[index + 1]
            return math.exp(low + fraction * (high - low))
    raise AssertionError("a positive total always reaches its 95th percentile")


@dataclass(frozen=True, slots=True)
class Reading:
    """One measure's word, or why it has none."""

    reading: str | None
    note: str | None = None


def compare_failure_share(
    this_errors: int, this_requests: int, previous_errors: int, previous_requests: int
) -> Reading:
    """FR24's two-proportion rule. The pooled share is held at least one request away
    from 0 and from 1, so neither no failures nor every request failing gives no spread."""
    if min(this_requests, previous_requests) < FAILURE_FLOOR:
        return Reading(None, TOO_LITTLE_TRAFFIC)
    total = this_requests + previous_requests
    pooled = min(max((this_errors + previous_errors) / total, 1 / total), 1 - 1 / total)
    spread = math.sqrt(pooled * (1 - pooled) * (1 / this_requests + 1 / previous_requests))
    difference = this_errors / this_requests - previous_errors / previous_requests
    if abs(difference) >= Z_THRESHOLD * spread:
        return Reading(HIGHER if difference > 0 else LOWER)
    if abs(difference) + Z_THRESHOLD * spread <= ABOUT_SAME_MARGIN:
        return Reading(ABOUT_SAME)
    return Reading(NOT_CLEARLY_DIFFERENT)


def compare_p95(
    this_p95: float | None, this_requests: int, previous_p95: float | None, previous_requests: int
) -> Reading:
    """FR24's ratio rule, read only when both sides reach the floor (FR23)."""
    if (
        min(this_requests, previous_requests) < P95_FLOOR
        or this_p95 is None
        or previous_p95 is None
    ):
        return Reading(None, TOO_LITTLE_TRAFFIC)
    if this_p95 > previous_p95 * P95_RATIO:
        return Reading(HIGHER)
    if previous_p95 > this_p95 * P95_RATIO:
        return Reading(LOWER)
    return Reading(ABOUT_SAME)


# ------------------------------------------------------------------ the inputs


@dataclass(frozen=True, slots=True)
class VersionMinute:
    """One service's minute under one version, as `service_versions_1m` holds it."""

    minute: datetime
    version: str
    requests: int
    errors: int
    buckets: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PeriodToCompare:
    """What the comparison needs of a stored period (`release_periods`)."""

    started_at: datetime
    version: str
    # What this period replaced, set when it opened (FR20). '' and `__other__` are
    # never one, and are treated as none.
    previous_version: str | None
    already_running: bool
    # When the previous version's own latest period started, for its warm-up.
    previous_started_at: datetime | None
    # The sender's first reported time, if any: the time method's moment.
    reported_at: datetime | None


@dataclass(frozen=True, slots=True)
class Side:
    """One version's counts in a block; `p95_ms` only where it reached the floor."""

    version: str
    requests: int
    errors: int
    p95_ms: float | None


@dataclass(frozen=True, slots=True)
class Block:
    kind: str
    method: str
    start: datetime
    end: datetime
    minutes_compared: int
    this_version: Side
    previous_version: Side
    failure_share: Reading
    p95: Reading


@dataclass(frozen=True, slots=True)
class Comparison:
    """A reason, or one or two blocks with the one that leads."""

    reason: str | None
    lead: str | None = None
    blocks: tuple[Block, ...] = ()
    # Too little traffic: the counts of the largest block tried (AC31).
    traffic: tuple[Side, Side] | None = None
    # A version never seen: what the service sends instead, busiest first.
    other_versions: tuple[str, ...] = ()


def comparison_end(
    window_end: datetime, settled_minute: datetime | None, now: datetime
) -> tuple[datetime, bool]:
    """Where a comparison stops, exclusive, and whether it reads "as of": the window's
    end or the minute after the settled minute, whichever comes first (design, step 1)."""
    if settled_minute is None:
        return min(window_end, (now - UNSETTLED_LAG).replace(second=0, microsecond=0)), True
    return min(window_end, settled_minute + _MINUTE), False


def read_start(started_at: datetime, window: timedelta) -> datetime:
    """How far before the period rows are read: the larger of an hour and the window."""
    return started_at - max(LOOKBACK_FLOOR, window)


# --------------------------------------------------------------- the decision


def choose_method(
    period: PeriodToCompare,
    minutes: Sequence[VersionMinute],
    *,
    to: datetime,
    window: timedelta,
    expired_before: datetime,
) -> Comparison:
    """The design's ordered list. `minutes` are the service's rows from `read_start` to
    `to`; `to` is exclusive and never past the settled minute; numbers older than
    `expired_before` are gone."""
    if period.started_at < expired_before:
        return Comparison(NUMBERS_EXPIRED)
    if to - period.started_at < TOO_RECENT_UNDER:
        return Comparison(TOO_RECENT)
    end = min(period.started_at + window, to)
    rows = [row for row in minutes if row.minute < to and row.requests > 0]
    if not rows:
        return Comparison(NO_REQUEST_TRAFFIC)
    if all(row.version == NO_VERSION for row in rows):
        if period.reported_at is None:
            return Comparison(NO_VERSION_NO_DEPLOY)
        return _by_time(rows, period.reported_at, min(period.reported_at + window, to))
    if not any(row.version == period.version for row in rows):
        return Comparison(NEW_VERSION_NOT_SEEN, other_versions=_busiest_first(rows))
    if period.already_running:
        return Comparison(ALREADY_RUNNING)
    previous = period.previous_version
    if previous in (None, NO_VERSION, OTHER_VERSION):
        return Comparison(NO_PREVIOUS_VERSION)
    return _by_version(period, previous, rows, end)


def _by_version(
    period: PeriodToCompare, previous: str, rows: Sequence[VersionMinute], end: datetime
) -> Comparison:
    grid = _grid(row for row in rows if row.minute < end)
    shared = [
        minute
        for minute in sorted(grid)
        if minute >= period.started_at
        and grid[minute].get(period.version, 0) > 0
        and grid[minute].get(previous, 0) > 0
    ]
    by_key = {(row.minute, row.version): row for row in rows}
    tried: list[Block] = []
    if shared:
        tried.append(
            _block(
                ROLLOUT,
                SIDE_BY_SIDE,
                this=[by_key[(m, period.version)] for m in shared],
                other=[by_key[(m, previous)] for m in shared],
                start=shared[0],
                end=shared[-1] + _MINUTE,
                minutes_compared=len(shared),
                period=period,
                previous=previous,
            )
        )
    finished = _rollout_finished(grid, period.started_at, previous, overlapped=bool(shared))
    if finished is not None and finished < end:
        stretch = end - finished
        before_from = period.started_at - stretch
        tried.append(
            _block(
                AFTER_ROLLOUT,
                VERSION_BEFORE_AFTER,
                this=[
                    r for r in rows if r.version == period.version and finished <= r.minute < end
                ],
                other=[
                    r
                    for r in rows
                    if r.version == previous and before_from <= r.minute < period.started_at
                ],
                start=finished,
                end=end,
                minutes_compared=_whole_minutes(stretch),
                period=period,
                previous=previous,
            )
        )
    return _answer(tried, lead_order=(AFTER_ROLLOUT, ROLLOUT))


def _by_time(rows: Sequence[VersionMinute], moment: datetime, end: datetime) -> Comparison:
    """Every row has no version: the service before and after a reported deploy."""
    stretch = end - moment
    after = [row for row in rows if moment <= row.minute < end]
    before = [row for row in rows if moment - stretch <= row.minute < moment]
    block = _block(
        AROUND_DEPLOY,
        TIME_BEFORE_AFTER,
        this=after,
        other=before,
        start=moment,
        end=end,
        minutes_compared=_whole_minutes(stretch),
        period=None,
        previous=NO_VERSION,
    )
    return _answer([block], lead_order=(AROUND_DEPLOY,))


def _answer(tried: Sequence[Block], *, lead_order: Sequence[str]) -> Comparison:
    """The blocks that reached the failure floor, leading with the most recent; or
    too little traffic, with the counts of the largest block tried."""
    kept = tuple(block for block in tried if block.failure_share.reading is not None)
    if kept:
        kinds = {block.kind for block in kept}
        lead = next(kind for kind in lead_order if kind in kinds)
        return Comparison(None, lead=lead, blocks=kept)
    if not tried:
        return Comparison(TOO_LITTLE_TRAFFIC)
    largest = max(tried, key=lambda b: b.this_version.requests + b.previous_version.requests)
    return Comparison(TOO_LITTLE_TRAFFIC, traffic=(largest.this_version, largest.previous_version))


def _block(
    kind: str,
    method: str,
    *,
    this: Sequence[VersionMinute],
    other: Sequence[VersionMinute],
    start: datetime,
    end: datetime,
    minutes_compared: int,
    period: PeriodToCompare | None,
    previous: str,
) -> Block:
    this_version = period.version if period is not None else NO_VERSION
    this_warm = _warm_from(period.started_at if period is not None else None)
    other_warm = _warm_from(period.previous_started_at if period is not None else None)
    this_side, this_cell = _side(this_version, this, this_warm)
    other_side, other_cell = _side(previous, other, other_warm)
    failure = compare_failure_share(
        this_side.errors, this_side.requests, other_side.errors, other_side.requests
    )
    p95 = compare_p95(this_side.p95_ms, this_cell.counted, other_side.p95_ms, other_cell.counted)
    if p95.reading is None and (this_cell.warm_up_cost or other_cell.warm_up_cost):
        p95 = Reading(None, WARM_UP_NOTE)
    return Block(
        kind=kind,
        method=method,
        start=start,
        end=end,
        minutes_compared=minutes_compared,
        this_version=this_side,
        previous_version=other_side,
        failure_share=failure,
        p95=p95,
    )


@dataclass(frozen=True, slots=True)
class _P95Cell:
    # Requests whose latency counts towards p95, after the warm-up.
    counted: int
    # Whether the warm-up alone took this side below the floor.
    warm_up_cost: bool


def _side(
    version: str, rows: Sequence[VersionMinute], warm_until: datetime | None
) -> tuple[Side, _P95Cell]:
    buckets = [0] * BUCKET_COUNT
    counted = 0
    for row in rows:
        if warm_until is not None and row.minute < warm_until:
            continue
        counted += row.requests
        for index, count in enumerate(row.buckets):
            buckets[index] += count
    requests = sum(row.requests for row in rows)
    p95 = bucket_p95(buckets) if counted >= P95_FLOOR else None
    side = Side(version, requests, sum(row.errors for row in rows), p95)
    return side, _P95Cell(counted, counted < P95_FLOOR <= requests)


def _warm_from(started_at: datetime | None) -> datetime | None:
    return None if started_at is None else started_at + WARM_UP


def _rollout_finished(
    grid: Mapping[datetime, Mapping[str, int]],
    started_at: datetime,
    previous: str,
    *,
    overlapped: bool,
) -> datetime | None:
    """The first minute of the first run of minutes with traffic in which the previous
    version held under 1%; the period's start when the two never overlapped."""
    if not overlapped:
        return started_at
    run_start: datetime | None = None
    run = 0
    for minute in sorted(m for m in grid if m >= started_at):
        counts = grid[minute]
        total = sum(counts.values())
        if total <= 0:
            continue
        if counts.get(previous, 0) / total < ROLLOUT_DONE_SHARE:
            run_start = minute if run == 0 else run_start
            run += 1
            if run >= ROLLOUT_DONE_MINUTES:
                return run_start
        else:
            run, run_start = 0, None
    return None


def _grid(rows: Iterable[VersionMinute]) -> dict[datetime, dict[str, int]]:
    grid: dict[datetime, dict[str, int]] = defaultdict(dict)
    for row in rows:
        grid[row.minute][row.version] = grid[row.minute].get(row.version, 0) + row.requests
    return grid


def _busiest_first(rows: Iterable[VersionMinute]) -> tuple[str, ...]:
    totals: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.version not in (NO_VERSION, OTHER_VERSION):
            totals[row.version] += row.requests
    return tuple(sorted(totals, key=lambda version: (-totals[version], version)))


def _whole_minutes(stretch: timedelta) -> int:
    return int(stretch.total_seconds() // _MINUTE.total_seconds())
