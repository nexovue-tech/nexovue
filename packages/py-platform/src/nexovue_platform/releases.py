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

**What other readers may take** (FR39, AC45; plan T5.1). A deploy leaves the webhook as a
`DeployFact`, which carries ids, times, a state and a commit and never the sender's free
text, and `DeployHistory` answers "the deploy before this" from such facts. The
comparison's ClickHouse statements are spliced from the fragments below, so the API's
`releases/queries.py` and the scheduler's `releases/store.py` read one definition.
"""

from __future__ import annotations

import math
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_platform.jobs import JobRequest
from nexovue_platform.slo import BUCKET_COUNT, LOG_EDGES

# ------------------------------------------------------------------- versions

# The worker's fold for versions past its caps or failing the name check
# (`derive.OtherVersion`), and a span with no `service.version`. Neither is a release.
OTHER_VERSION: Final = "__other__"
NO_VERSION: Final = ""
# `releases.service_name` and `releases.version` (`models.NAME_MAX_LENGTH`).
NAME_MAX_LENGTH: Final = 200
# One cap on both doors (FR56, a starting guess): past it, a project evicts before it
# creates, so a new real version always appears.
RELEASES_PER_PROJECT: Final = 50_000

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
# The before-and-after methods also show the same stretch this long earlier (FR25).
LAST_WEEK: Final = timedelta(days=7)
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
# The states of a deploy that went out (founder, 2026-11-09): only these open a period,
# end one, bring a version back, or count as a report that came before traffic. A
# `failed` or `rolled_back` deploy did not replace what was running.
OPENING_STATES: Final = frozenset({"started", "finished"})

# A service's coverage, as of a moment (spec, *A service's coverage*; FR38).
DEPLOYS_AND_VERSIONS: Final = "deploys_and_versions"
DEPLOYS_ONLY: Final = "deploys_only"
VERSIONS_ONLY: Final = "versions_only"
COVERAGE_NONE: Final = "none"
COVERAGES: Final = (DEPLOYS_AND_VERSIONS, DEPLOYS_ONLY, VERSIONS_ONLY, COVERAGE_NONE)

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
class ServiceMinute:
    """One service's minute over every version, as `service_stats_1m` holds it."""

    minute: datetime
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
    # When a sender reported this period began, if it did: the time method's moment.
    # `reported_moment` gives it.
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
    """One comparison. `start` to `end` is this version's stretch, and `previous_start`
    to `previous_end` the previous side's: the same minutes side by side, the equal
    stretch before the period otherwise."""

    kind: str
    method: str
    start: datetime
    end: datetime
    minutes_compared: int
    this_version: Side
    previous_version: Side
    failure_share: Reading
    p95: Reading
    previous_start: datetime
    previous_end: datetime


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


@dataclass(frozen=True, slots=True)
class Coverage:
    """What we hear about one service's deploys, as of a moment."""

    value: str
    # The last report at or before the moment; its age always shows with the value.
    last_reported_at: datetime | None
    # Set only when nothing was heard by the moment and something was afterwards:
    # "We first heard about deploys on this service on {date}, after this started."
    first_heard_at: datetime | None


def coverage_of(
    first_reported_at: datetime | None,
    first_seen_at: datetime | None,
    *,
    at: datetime,
    last_reported_at: datetime | None,
) -> Coverage:
    """A service's coverage as of `at`, from its earliest report and earliest counted
    version (any state counts: a failed report is still deploy information). There is
    no age limit inside the record's retention, so a stopped CI shows as a stale last
    report, never as a silent change of value (AC73)."""
    reported = first_reported_at is not None and first_reported_at <= at
    counted = first_seen_at is not None and first_seen_at <= at
    if reported and counted:
        value = DEPLOYS_AND_VERSIONS
    elif reported:
        value = DEPLOYS_ONLY
    elif counted:
        value = VERSIONS_ONLY
    else:
        value = COVERAGE_NONE
    heard = [moment for moment in (first_reported_at, first_seen_at) if moment is not None]
    first_heard = min(heard) if heard and value == COVERAGE_NONE else None
    return Coverage(value, last_reported_at, first_heard)


def reported_moment(started_at: datetime, start_basis: str) -> datetime | None:
    """The time method's moment (design 4, step 6): the reported start of the period
    being compared. Not the release's first report: that may be a deploy that failed, or
    an earlier period's, and neither began what is compared (founder, 2026-11-09)."""
    return started_at if start_basis == REPORTED else None


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
    totals: Sequence[ServiceMinute] = (),
) -> Comparison:
    """The design's ordered list. `minutes` are the service's rows from `read_start` to
    `to`, and `totals` its `service_stats_1m` minutes over the same span, which the time
    method reads (design 4, step 6); `to` is exclusive and never past the settled
    minute; numbers older than `expired_before` are gone."""
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
        whole = [_as_unversioned(total) for total in totals if total.minute < to] or rows
        return _by_time(whole, period.reported_at, min(period.reported_at + window, to))
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
                previous_start=shared[0],
                previous_end=shared[-1] + _MINUTE,
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
                previous_start=before_from,
                previous_end=period.started_at,
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
        previous_start=moment - stretch,
        previous_end=moment,
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
    previous_start: datetime,
    previous_end: datetime,
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
        previous_start=previous_start,
        previous_end=previous_end,
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


def _as_unversioned(total: ServiceMinute) -> VersionMinute:
    return VersionMinute(total.minute, NO_VERSION, total.requests, total.errors, total.buckets)


# ------------------------------------------------- what a comparison also says

Span = tuple[datetime, datetime]


def compared_spans(comparison: Comparison) -> tuple[Span, ...]:
    """Every stretch a comparison's numbers came from, each side of each block."""
    return tuple(
        span
        for block in comparison.blocks
        for span in ((block.start, block.end), (block.previous_start, block.previous_end))
    )


def _within(minute: datetime, spans: Iterable[Span]) -> bool:
    return any(start <= minute < end for start, end in spans)


def minutes_differing(
    versions: Iterable[VersionMinute], totals: Iterable[ServiceMinute], spans: Sequence[Span]
) -> int:
    """FR28, per minute (D33): how many compared minutes' requests, summed over every
    version, differ from `service_stats_1m`'s. A minute one table holds and the other
    does not differs too, so a dropped write never reads as healthy numbers."""
    by_version: dict[datetime, int] = defaultdict(int)
    for row in versions:
        if _within(row.minute, spans):
            by_version[row.minute] += row.requests
    by_service: dict[datetime, int] = defaultdict(int)
    for total in totals:
        if _within(total.minute, spans):
            by_service[total.minute] += total.requests
    return sum(
        1
        for minute in by_version.keys() | by_service.keys()
        if by_version.get(minute, 0) != by_service.get(minute, 0)
    )


def versions_combined(versions: Iterable[VersionMinute], spans: Sequence[Span]) -> bool:
    """FR17: the worker folded versions into `__other__` in a compared minute, so a
    version's count may be short and the comparison is inexact."""
    return any(
        row.version == OTHER_VERSION and row.requests > 0 and _within(row.minute, spans)
        for row in versions
    )


def last_week_of(block: Block, totals: Iterable[ServiceMinute]) -> Side | None:
    """FR25: the whole service over this block's stretch one week earlier, for the two
    before-and-after methods only; None for side by side, and where nothing was kept."""
    if block.method == SIDE_BY_SIDE:
        return None
    start, end = block.start - LAST_WEEK, block.end - LAST_WEEK
    rows = [_as_unversioned(total) for total in totals if start <= total.minute < end]
    if not rows:
        return None
    side, _ = _side(NO_VERSION, rows, None)
    return side


@dataclass(frozen=True, slots=True)
class FindingWindow:
    """FR22: the window a comparison opened from a finding takes, and whether it
    reaches the finding's start."""

    window: timedelta
    reaches: bool


def window_for_finding(period_start: datetime, finding_start: datetime) -> FindingWindow:
    """The smallest offered window from the period's start that reaches the finding,
    or the largest when none does. A release 5 hours before a finding compares over 6,
    so a healthy first hour cannot read "about the same" while the service fails later."""
    for window in WINDOWS:
        if period_start + window >= finding_start:
            return FindingWindow(window, True)
    return FindingWindow(WINDOWS[-1], False)


def reaches(period_start: datetime, window: timedelta, finding_start: datetime) -> bool:
    return period_start + window >= finding_start


# ------------------------------------------------- the facts other readers take


@dataclass(frozen=True, slots=True)
class DeployFact:
    """One signed deploy event, as every reader outside the webhook takes it (FR39, AC45).

    Ids, times, a state and a commit. The sender's `metadata`, `url`, `source` and
    `delivery_id` are free text and are never carried: `test_release_facts.py` holds
    these fields to an exact list."""

    deploy_id: uuid.UUID
    release_id: uuid.UUID | None
    service_name: str
    version: str
    # The sender's claim, bounded at the door (FR8).
    deployed_at: datetime
    # When it arrived: ours, never the sender's.
    received_at: datetime
    state: str
    # Lower-case hex, checked at the door, so it cannot hold words.
    commit_sha: str | None
    # A signed record (D54: F3's `verified`). Never a seen version.
    basis: str = field(default=REPORTED, init=False)


class DeployHistory:
    """The deploys that could precede anything inside one window, ready to ask.

    Built once per request and asked once per row. The alternative is a statement
    per row, and a page of error groups holds fifty.
    """

    def __init__(
        self, deploys: Sequence[DeployFact], *, known_from: datetime | None = None
    ) -> None:
        # Most recent first, so `preceding` is the first match rather than a
        # search for a maximum.
        self._deploys = sorted(deploys, key=lambda deploy: deploy.deployed_at, reverse=True)
        self._known_from = known_from

    def preceding(self, *, services: Sequence[str], at: datetime) -> DeployFact | None:
        """The last deploy of one of these services at or before this instant.

        **This is the deploy *before* the thing, and never the deploy that caused
        it.** All this knows is that a change shipped and then something happened;
        an error four minutes later may have been triggered by traffic, by a
        dependency, or by a change nobody notified us of at all. The order is a
        fact and the cause is not, so every caller — and every screen reading
        what a caller returns — has to say the first and never imply the second.

        A group with no services matches nothing: there is no service whose
        deploys could have come first. An instant before `known_from` matches
        nothing either: the reading did not reach back that far, and the deploy
        it would name is provably not the last.
        """
        if self._known_from is not None and at < self._known_from:
            return None

        wanted = set(services)
        return next(
            (
                deploy
                for deploy in self._deploys
                if deploy.deployed_at <= at and deploy.service_name in wanted
            ),
            None,
        )


EMPTY_DEPLOY_HISTORY: Final = DeployHistory([])

# ClickHouse fragments the comparison's statements are spliced from (design 4, D26).
# Each service owns its full statement under its own tenant chokepoint; these are the
# parts whose meaning must not differ between them.
VERSION_MINUTE_COLUMNS: Final = """toUnixTimestamp(minute) AS minute_epoch,
       version,
       sum(request_count) AS requests,
       sum(error_count) AS errors,
       sumForEachMerge(latency_buckets) AS buckets"""
SERVICE_MINUTE_COLUMNS: Final = """toUnixTimestamp(minute) AS minute_epoch,
       sum(request_count) AS requests,
       sum(error_count) AS errors,
       sumForEachMerge(latency_buckets) AS buckets"""
# A span's environment label (FR45, D39): the current name first, the older
# `deployment.environment` where a span has only that, as the profile writer reads them
# (`profilewriting/rows.go`). Presence only, so sampling does not matter.
SPAN_ENVIRONMENT: Final = """if(
               ifNull(resource_attrs.deployment.environment.name.:String, '') != '',
               ifNull(resource_attrs.deployment.environment.name.:String, ''),
               ifNull(resource_attrs.deployment.environment.:String, '')
           )"""
# Two labels are enough to warn.
ENVIRONMENTS_ENOUGH: Final = 2


def version_minute_of(row: Mapping[str, Any]) -> VersionMinute:
    """A `VERSION_MINUTE_COLUMNS` row."""
    return VersionMinute(
        minute=datetime.fromtimestamp(int(row["minute_epoch"]), UTC),
        version=str(row["version"]),
        requests=int(row["requests"]),
        errors=int(row["errors"]),
        buckets=tuple(int(count) for count in row["buckets"]),
    )


def service_minute_of(row: Mapping[str, Any]) -> ServiceMinute:
    """A `SERVICE_MINUTE_COLUMNS` row."""
    return ServiceMinute(
        minute=datetime.fromtimestamp(int(row["minute_epoch"]), UTC),
        requests=int(row["requests"]),
        errors=int(row["errors"]),
        buckets=tuple(int(count) for count in row["buckets"]),
    )


# ------------------------------------------------------------ the stored reading

# How long after a period starts its reading is stored, and its summary sent (FR55; a
# starting guess).
SUMMARY_AFTER: Final = timedelta(minutes=30)
# The methods whose `higher` the list flags (FR55): by version, never by time alone.
FLAGGED_METHODS: Final = frozenset({SIDE_BY_SIDE, VERSION_BEFORE_AFTER})
FAILURE_SHARE: Final = "failure_share"
P95: Final = "p95"


def stored_reading(
    *,
    reason: str | None,
    blocks: Sequence[Block],
    lead: str | None,
    traffic: tuple[Side, Side] | None,
    versions_combined: bool,
    minutes_differing: int,
) -> dict[str, object]:
    """`release_periods.last_reading` (FR55, AC81): the leading block's method, words and
    counts, or the reason there is none. Never a name, so a version planted in telemetry
    is never stored here, and the words are read again at send time."""
    led = next((block for block in blocks if block.kind == lead), None)
    reading: dict[str, object] = {
        "reason": reason,
        "versions_combined": versions_combined,
        "minutes_differing": minutes_differing,
    }
    if traffic is not None:
        reading["traffic"] = {
            "this_requests": traffic[0].requests,
            "previous_requests": traffic[1].requests,
        }
    if led is None:
        return reading
    return reading | {
        "block": led.kind,
        "method": led.method,
        "minutes_compared": led.minutes_compared,
        "this": _counts(led.this_version),
        "previous": _counts(led.previous_version),
        FAILURE_SHARE: led.failure_share.reading,
        f"{FAILURE_SHARE}_note": led.failure_share.note,
        P95: led.p95.reading,
        f"{P95}_note": led.p95.note,
    }


def _counts(side: Side) -> dict[str, object]:
    return {"requests": side.requests, "errors": side.errors, "p95_ms": side.p95_ms}


def higher_measures(reading: Mapping[str, object] | None) -> tuple[str, ...]:
    """The list's flags (FR55, AC81): each measure a version method read as `higher`,
    in the order the list shows them; nothing for any other reading, or none."""
    if not reading or reading.get("method") not in FLAGGED_METHODS:
        return ()
    return tuple(measure for measure in (FAILURE_SHARE, P95) if reading.get(measure) == HIGHER)


# ----------------------------------------------------------- the summary's job

# The scheduler's queue for release work (D31), named here so the API's webhook can add
# a summary job to it without importing the scheduler.
RELEASES_QUEUE: Final = "releases"
SUMMARIZE_PERIOD_TASK: Final = "nexovue_scheduler.releases.summarize_period"
# A failed ClickHouse read is tried twice more; the job reads three statements of at
# most 10 seconds each (`releases/store.py`).
SUMMARY_MAX_ATTEMPTS: Final = 3
SUMMARY_SOFT_LIMIT_SECONDS: Final = 40
SUMMARY_HARD_LIMIT_SECONDS: Final = 55


def summarize_request(
    org_id: Any, project_id: Any, period_id: Any, *, countdown: float = 0
) -> JobRequest:
    """One period's stored reading and summary (FR55). Ids only, as job arguments are
    stored readably."""
    return JobRequest(
        task=SUMMARIZE_PERIOD_TASK,
        queue=RELEASES_QUEUE,
        kwargs={
            "org_id": str(org_id),
            "project_id": str(project_id),
            "period_id": str(period_id),
        },
        org_id=str(org_id),
        countdown_seconds=countdown,
        max_attempts=SUMMARY_MAX_ATTEMPTS,
        soft_limit_seconds=SUMMARY_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=SUMMARY_HARD_LIMIT_SECONDS,
    )


def summary_countdown(started_at: datetime, now: datetime) -> float:
    """Seconds until a period's reading is due: `SUMMARY_AFTER` past its start, at once
    for one that opened late, as a first sync's periods do."""
    return max(0.0, (started_at + SUMMARY_AFTER - now).total_seconds())


# --------------------------------------------------------- the summary's words

# FR24's plain words, as the release page says them.
READING_WORDS: Final = {
    HIGHER: "higher",
    LOWER: "lower",
    ABOUT_SAME: "about the same",
    NOT_CLEARLY_DIFFERENT: "not clearly different",
}
NO_SAMPLING_WORDS: Final = "Counts assume you do not sample before sending."
ORDER_NOT_CAUSE: Final = "Order only, not cause."
VERSIONS_COMBINED_WORDS: Final = (
    "Versions were combined: too many in one minute, so a version's count may be short."
)
_REASON_WORDS: Final = {
    TOO_RECENT: "Under 5 settled minutes were read.",
    NO_PREVIOUS_VERSION: (
        "This is the first version we know of on this service, so there is nothing to "
        "compare it with."
    ),
    NUMBERS_EXPIRED: "Numbers for this release have expired.",
    NO_VERSION_NO_DEPLOY: (
        "No version in your traffic, and no deploy reported, so we cannot compare."
    ),
    NEW_VERSION_NOT_SEEN: (
        "Reported, never seen in traffic. The rollout may have failed, or the version "
        "text may differ."
    ),
    NO_REQUEST_TRAFFIC: (
        "We count requests from server traces and queue consumers. This service sends "
        "none, so we can mark its deploys but cannot compare them."
    ),
}
_METHOD_LEADS: Final = {
    SIDE_BY_SIDE: "Side by side over {over}.",
    VERSION_BEFORE_AFTER: (
        "Before and after, by version, over {over}. Different times of day can move these numbers."
    ),
    TIME_BEFORE_AFTER: (
        "Before and after, by time, over {over}. No version in your traffic, so a rolling "
        "deploy mixes old and new here."
    ),
}
_SMALLEST_SHOWN_PERCENT: Final = 0.1
_MINUTES_PER_HOUR: Final = 60


def summary_lines(
    reading: Mapping[str, Any] | None, *, version: str, previous_version: str | None
) -> list[str]:
    """A release summary's lines below its headline (FR55, D52): the method, a line per
    measure with counts and the plain word, or the reason's words. No verdict, and no
    word of cause (AC37, AC82). The names are the caller's to flatten and escape."""
    if not reading:
        return ["No reading was stored for this release; open it to compare."]
    method = reading.get("method")
    if method not in _METHOD_LEADS:
        return [_reason_line(reading, version=version, previous_version=previous_version)]
    by_version = method != TIME_BEFORE_AFTER
    this, previous = _sides(reading)
    over = _stretch(int(_number(reading.get("minutes_compared"))))
    lines = [
        _METHOD_LEADS[str(method)].format(over=over),
        _failure_line(reading, this, previous, version, previous_version, by_version),
    ]
    if by_version:
        lines.append(_p95_line(reading, this, previous))
    if reading.get("versions_combined"):
        lines.append(VERSIONS_COMBINED_WORDS)
    lines.append(NO_SAMPLING_WORDS)
    return lines


def _reason_line(reading: Mapping[str, Any], *, version: str, previous_version: str | None) -> str:
    reason = reading.get("reason")
    if reason == TOO_LITTLE_TRAFFIC:
        traffic = reading.get("traffic")
        if not isinstance(traffic, Mapping):
            return "Too little traffic to compare."
        mine, theirs = (
            _count(traffic.get("this_requests")),
            _count(traffic.get("previous_requests")),
        )
        return (
            f"Too little traffic to compare: {mine} on {version}, "
            f"{theirs} on {previous_version or 'the previous version'}."
        )
    return _REASON_WORDS.get(str(reason), "Open the release to read its comparison.")


def _sides(reading: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    this, previous = reading.get("this"), reading.get("previous")
    return (
        this if isinstance(this, Mapping) else {},
        previous if isinstance(previous, Mapping) else {},
    )


def _failure_line(
    reading: Mapping[str, Any],
    this: Mapping[str, Any],
    previous: Mapping[str, Any],
    version: str,
    previous_version: str | None,
    by_version: bool,
) -> str:
    word = READING_WORDS.get(str(reading.get(FAILURE_SHARE)))
    if word is None:
        return "Failure share: too little traffic to read."
    mine, theirs = _failure_share(this), _failure_share(previous)
    if by_version:
        sides = (
            f"{mine} on {version} against {theirs} on {previous_version or 'the previous version'}"
        )
    else:
        sides = f"{mine} after against {theirs} before"
    return f"Failure share: {sides}, {word}."


def _p95_line(
    reading: Mapping[str, Any], this: Mapping[str, Any], previous: Mapping[str, Any]
) -> str:
    if reading.get(f"{P95}_note") == WARM_UP_NOTE:
        return f"p95: left out for each version's first {_whole_minutes(WARM_UP)} minutes."
    word = READING_WORDS.get(str(reading.get(P95)))
    if word is None:
        return "p95: too little traffic to read."
    return (
        f"p95: {_milliseconds(this.get('p95_ms'))} against "
        f"{_milliseconds(previous.get('p95_ms'))}, {word}."
    )


def _failure_share(side: Mapping[str, Any]) -> str:
    requests, errors = int(_number(side.get("requests"))), int(_number(side.get("errors")))
    if requests <= 0:
        return "—"
    percent = 100 * errors / requests
    shown = (
        f"<{_SMALLEST_SHOWN_PERCENT}%"
        if 0 < percent < _SMALLEST_SHOWN_PERCENT
        else f"{percent:.1f}%"
    )
    return f"{shown} ({errors:,} of {requests:,})"


def _milliseconds(value: object) -> str:
    return f"{round(_number(value)):,} ms" if isinstance(value, int | float) else "—"


def _count(value: object) -> str:
    requests = int(_number(value))
    return f"{requests:,} {'request' if requests == 1 else 'requests'}"


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


def _stretch(minutes: int) -> str:
    """ "42 minutes", or "1 hour" on the hour, as the page's `stretchWords`."""
    if minutes >= _MINUTES_PER_HOUR and minutes % _MINUTES_PER_HOUR == 0:
        hours = minutes // _MINUTES_PER_HOUR
        return f"{hours} {'hour' if hours == 1 else 'hours'}"
    return f"{minutes} {'minute' if minutes == 1 else 'minutes'}"
