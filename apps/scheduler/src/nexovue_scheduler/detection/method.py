"""The statistical method: whether one window of a service is unusual against its
normal (design, "The statistical method"; spec FR9 to FR14, FR32).

Pure: no store, no clock. The project job reads the window and the hourly
baseline, and this decides. Ported from the simulation the design was approved on
(`tools/detection-sim/detect_sim.py`: `judge_slower`, `judge_failing`,
`judge_endpoints`, `judge_traffic`, `burst_profile`, `bb_sf`, `mom_rho`), and held
to it by `test_detection_method.py`'s parity cases.

A window is **unusual** only if every condition holds, and also with its single
worst minute removed; and at least 80% of its minutes lean the same way. So one
minute, however extreme, never opens a finding (FR12).

Three decisions taken after the simulation are applied here on top of it: burst
tolerance never applies at or above critical (D26), an endpoint failing at the
critical share of its own requests is critical (D27), and a stop is judged at any
expected traffic from day 3 (D30).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final, Literal

# --------------------------------------------------------------------- buckets

# `service_stats_1m.latency_buckets`: 64 counts over boundaries log-spaced from
# 1 ms to 60 s (apps/workers/internal/pipeline/derive/services.go). Edge i is
# 60000^(i/64) ms; bucket i lies between edges i and i + 1, and a duration outside
# the range folds into the end bucket on its side.
BUCKET_COUNT: Final = 64
_CEILING_MS: Final = 60_000.0
LOG_EDGES: Final = tuple(
    math.log(_CEILING_MS) * index / BUCKET_COUNT for index in range(BUCKET_COUNT + 1)
)

# ------------------------------------------------------------------ parameters

# Four spreads, one-sided: about 3.2e-5 at every sensitivity (design, k = 4).
SPREADS: Final = 4
ALPHA: Final = 0.5 * math.erfc(SPREADS / math.sqrt(2))
# A signal reads normal, for closing, when its tail probability is above this (FR13).
NORMAL_TAIL: Final = 0.05
# The share of a window's minutes that must each lean the same way.
MOST: Final = 0.8
# Fewer requests give an unreliable p95: ±30% at 20 (spec, *Product values*).
REQUESTS_TO_JUDGE_SLOWER: Final = 200
CRITICAL_SLOWER: Final = 4.0
# Judged minutes that must read normal before a finding closes (FR13).
RECOVERY_MINUTES: Final = 10
# *Failing more* (spec, *Product values*): requests a window needs to be judged, and
# the share of requests failing that is critical.
REQUESTS_TO_JUDGE_FAILING: Final = 20
CRITICAL_FAILING: Final = 0.20
# Per endpoint (FR2): the request floor, and the failures a window needs (design).
ENDPOINT_REQUESTS_TO_JUDGE: Final = 20
ENDPOINT_FAILURES_TO_JUDGE: Final = 10
# The normal rate is (failures + 0.5) / (requests + 250), so a service with no
# failures in its history is not alarmed by one (FR10, design).
PRIOR_FAILURES: Final = 0.5
PRIOR_REQUESTS: Final = 250.0
# Below this many spreads the exact tail is far above alpha, so it is not computed.
PRESCREEN_SPREADS: Final = 1.5
# D18 as amended by D21: a history is bursty at one excursion a week or more.
BURSTY_EXCURSIONS_PER_DAY: Final = 1 / 7
# A burst height needs this many like-volume windows; fewer leaves no height.
PROFILE_MIN_WINDOWS: Final = 60
# *Losing traffic*: the share of its normal that is critical, the Anscombe shift,
# the MAD's scale to a standard deviation, and the spread's floor (Poisson's).
CRITICAL_TRAFFIC: Final = 0.10
ANSCOMBE_SHIFT: Final = 0.375
_MAD_SCALE: Final = 1.4826
_SPREAD_FLOOR: Final = 1.0
# A traffic window reads normal, for closing, above this many spreads (the simulation's).
NORMAL_TRAFFIC_SPREADS: Final = -2.0
# The share of a pool's requests that its threshold L sits nearest to: the p95.
_THRESHOLD_SHARE: Final = 0.95
# What a pool with no requests is taken to have above L; it is never judged.
_EMPTY_POOL_SHARE: Final = 0.05
# The sim's guards: a share is kept inside (0, 1), a wander under 1, and a wander
# this small is binomial.
_SHARE_FLOOR: Final = 1e-12
_WANDER_CEILING: Final = 0.999
_BINOMIAL_WANDER: Final = 1e-9
# Half a request: a pool with none above L is not taken to have a share of zero.
_HALF_REQUEST: Final = 0.5
# The tail sum stops once a term, past the mode, is this far below the total.
_NEGLIGIBLE_LOG: Final = -40.0
_DECREASING: Final = 0.99


@dataclass(frozen=True, slots=True)
class Sensitivity:
    """One level of FR18: its window, which is also its sustain time, and each
    signal's minimum change (spec, *Product values*)."""

    name: Literal["low", "normal", "high"]
    window_minutes: int
    slower_ratio: float
    failing_points: float
    failing_minimum: int
    traffic_ratio: float

    @property
    def most_minutes(self) -> int:
        """How many of the window's minutes must lean the same way."""
        return math.ceil(MOST * self.window_minutes)

    @property
    def traffic_floor(self) -> float:
        """The expected window count at which a fall to the minimum ratio reaches 4
        spreads on the square-root scale: about 20, 47 and 150 (spec, FR11)."""
        return (SPREADS / (2 * (1 - math.sqrt(self.traffic_ratio)))) ** 2


SENSITIVITIES: Final = {
    "low": Sensitivity("low", 10, 2.0, 0.05, 10, 0.30),
    "normal": Sensitivity("normal", 5, 1.5, 0.02, 5, 0.50),
    "high": Sensitivity("high", 3, 1.25, 0.01, 5, 0.70),
}
WINDOW_MINUTES: Final = tuple(sorted({level.window_minutes for level in SENSITIVITIES.values()}))

# ------------------------------------------------------------------ vocabulary

# How a baseline was pooled (design, "The normal"): the same weekday over the last
# 4 weeks, earlier days of the same class, or too few days yet.
Mode = Literal["weekly", "daily", "learning"]
MODES: Final[tuple[Mode, ...]] = ("weekly", "daily", "learning")
# What a reading says of one signal. The first three are judged: unusual opens or
# extends a finding, normal counts towards closing it, and elevated is neither, so
# it restarts the count. The rest are not judged, and pause the count (FR11).
Verdict = Literal["unusual", "elevated", "normal", "learning", "too_little_traffic", "no_baseline"]
JUDGED: Final[frozenset[Verdict]] = frozenset({"unusual", "elevated", "normal"})


# One endpoint's requests and failures in a minute or a window.
EndpointCounts = tuple[int, int]


@dataclass(frozen=True, slots=True)
class Minute:
    """One minute of one service, from `service_stats_1m`, and its endpoints' counts
    from `endpoint_stats_1m`. A minute with no row is a minute with no requests."""

    requests: int
    errors: int
    buckets: tuple[int, ...]
    endpoints: Mapping[str, EndpointCounts] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> Minute:
        return cls(0, 0, (0,) * BUCKET_COUNT)


@dataclass(frozen=True, slots=True)
class Window:
    """The minutes judged together, oldest first, ending at the judged minute."""

    minutes: tuple[Minute, ...]

    @property
    def requests(self) -> int:
        return sum(minute.requests for minute in self.minutes)

    @property
    def errors(self) -> int:
        return sum(minute.errors for minute in self.minutes)

    @property
    def buckets(self) -> list[int]:
        return [sum(column) for column in zip(*(m.buckets for m in self.minutes), strict=True)]

    def endpoint(self, name: str) -> list[EndpointCounts]:
        """One endpoint's counts in each minute, oldest first; (0, 0) where it had none."""
        return [minute.endpoints.get(name, (0, 0)) for minute in self.minutes]

    @property
    def endpoint_names(self) -> set[str]:
        return {name for minute in self.minutes for name in minute.endpoints}


@dataclass(frozen=True, slots=True)
class SlowerBaseline:
    """The normal one minute of *slower* is judged by, from the hourly job.

    `threshold_index` is L's edge in `LOG_EDGES`: the boundary nearest the level
    pool's p95. `share_above` is the pool's share of requests above it, and
    `wander` the beta-binomial intra-class correlation from the ±180-minute pool.
    """

    mode: Mode
    days: int
    window_minutes: int
    pool_requests: int
    threshold_index: int
    share_above: float
    wander: float
    normal_p95_ms: float | None
    # The first local date the signal's class reaches 3 days, while learning.
    learning_until: date | None = None

    @property
    def threshold_ms(self) -> float:
        return math.exp(LOG_EDGES[self.threshold_index])


@dataclass(frozen=True, slots=True)
class SlowerJudgement:
    """One minute's *slower* verdict and the numbers a finding and a reading show."""

    verdict: Verdict
    # The window crossed the critical size (4 times its normal p95). Severity, never a
    # verdict: only an unusual window can raise a finding to critical.
    critical: bool
    window_requests: int
    window_p95_ms: float | None
    # Requests in the window above L; 0 without a baseline.
    above_threshold: int
    # The window's p95 over the normal p95.
    ratio: float | None
    # P(this many or more above L) if nothing were wrong; None when not computed.
    tail: float | None
    # For an unusual window: how many of its minutes, from the first, came before
    # the first one leaning slower. A finding dates from that minute (FR17).
    calm_minutes: int = 0

    @property
    def judged(self) -> bool:
        return self.verdict in JUDGED


# -------------------------------------------------------------------- judging


def judge_slower(
    window: Window, baseline: SlowerBaseline | None, sensitivity: Sensitivity
) -> SlowerJudgement:
    """*Slower*, as a count: the window's requests above L, beta-binomial against the
    pool's share above L, with the p95's minimum change and the 80% rule."""
    _check_width(window, sensitivity)
    requests = window.requests
    p95 = bucket_p95(window.buckets)
    if baseline is None or baseline.window_minutes != sensitivity.window_minutes:
        return SlowerJudgement("no_baseline", False, requests, p95, 0, None, None)
    above = _above(window.buckets, baseline.threshold_index)
    if baseline.mode == "learning":
        return SlowerJudgement("learning", False, requests, p95, above, None, None)
    normal = baseline.normal_p95_ms
    if requests < REQUESTS_TO_JUDGE_SLOWER or baseline.pool_requests <= 0 or normal is None:
        return SlowerJudgement("too_little_traffic", False, requests, p95, above, None, None)
    ratio = (p95 or 0.0) / normal
    critical = ratio >= CRITICAL_SLOWER
    tail = _tail_if_raised(above, requests, baseline)

    def judged(verdict: Verdict, calm: int = 0) -> SlowerJudgement:
        return SlowerJudgement(verdict, critical, requests, p95, above, ratio, tail, calm)

    leaning = _leaning(window, baseline, sensitivity)
    if _is_unusual(window, baseline, sensitivity, ratio, above, leaning):
        return judged("unusual", leaning.index(True))
    # Clear for closing: normal, or no longer past the minimum change (design 2.1, AC30).
    if tail is None or tail > NORMAL_TAIL or ratio < sensitivity.slower_ratio:
        return judged("normal")
    return judged("elevated")


def _tail_if_raised(above: int, requests: int, baseline: SlowerBaseline) -> float | None:
    """The tail probability, or None when no more requests than expected are above L,
    which is normal without computing it."""
    if above <= requests * baseline.share_above:
        return None
    return beta_binomial_tail(above, requests, baseline.share_above, baseline.wander)


def _is_unusual(
    window: Window,
    baseline: SlowerBaseline,
    sensitivity: Sensitivity,
    ratio: float,
    above: int,
    leaning: Sequence[bool],
) -> bool:
    if ratio < sensitivity.slower_ratio or sum(leaning) < sensitivity.most_minutes:
        return False
    requests = window.requests
    share, wander = baseline.share_above, baseline.wander
    if beta_binomial_tail(above, requests, share, wander) >= ALPHA:
        return False
    # Leave-one-out: without the minute holding the most requests above L.
    tops = [_above(minute.buckets, baseline.threshold_index) for minute in window.minutes]
    worst = max(range(len(tops)), key=tops.__getitem__)
    rest_requests = requests - window.minutes[worst].requests
    return beta_binomial_tail(above - tops[worst], rest_requests, share, wander) < ALPHA


def _leaning(window: Window, baseline: SlowerBaseline, sensitivity: Sensitivity) -> list[bool]:
    """Each minute's own p95 past the minimum change: the 80% rule's minutes."""
    floor = sensitivity.slower_ratio * (baseline.normal_p95_ms or 0.0)
    return [(bucket_p95(minute.buckets) or 0.0) >= floor for minute in window.minutes]


def _above(buckets: Sequence[int], threshold_index: int) -> int:
    """Requests in buckets at or above L's edge: slower than L."""
    return sum(buckets[threshold_index:])


def _check_width(window: Window, sensitivity: Sensitivity) -> None:
    if len(window.minutes) != sensitivity.window_minutes:
        raise ValueError(
            f"a {sensitivity.name} window is {sensitivity.window_minutes} minutes, "
            f"not {len(window.minutes)}"
        )


# --------------------------------------------------------------- failing more


@dataclass(frozen=True, slots=True)
class BurstProfile:
    """A service's own failure bursts over its last 28 days (D17, D18): how high and
    how long they run, and how often.

    `heights` maps a volume level, round(log2(window requests)), to the highest
    failure rate among windows of like volume, within a factor of two; a level
    with too few such windows has none. `minutes` is the longest excursion above
    the base test, never shorter than the window.
    """

    heights: Mapping[int, float]
    minutes: int
    excursions: int
    # Judged days of history the excursions come from; learning days hold none.
    days: float

    @property
    def bursty(self) -> bool:
        """D18: at least one excursion a week of judged history, on average."""
        return self.days > 0 and self.excursions / self.days >= BURSTY_EXCURSIONS_PER_DAY

    def height(self, requests: float) -> float:
        """The height a window of `requests` must pass; -inf when none was learned."""
        return self.heights.get(volume_level(requests), -math.inf)


@dataclass(frozen=True, slots=True)
class EndpointNormal:
    """One endpoint's normal: its level pool's window sums, its wander, and its own
    burst profile over 28 days, None until it has judged history (D18 per endpoint)."""

    pool_requests: int
    pool_failures: int
    wander: float
    profile: BurstProfile | None = None

    @property
    def rate(self) -> float:
        return normal_failure_rate(self.pool_failures, self.pool_requests)


@dataclass(frozen=True, slots=True)
class FailingBaseline:
    """The normal one minute of *failing more* is judged by, from the hourly job.

    The level pool's window sums give the normal rate; `wander` is the
    beta-binomial intra-class correlation from the ±180-minute pool. `profile` is
    the service's burst profile, None until it has judged history.
    """

    mode: Mode
    days: int
    window_minutes: int
    pool_requests: int
    pool_failures: int
    wander: float
    profile: BurstProfile | None = None
    # Endpoints in the normal pool; only these are judged and named (design 2.1, m1).
    endpoints: Mapping[str, EndpointNormal] = field(default_factory=dict)
    learning_until: date | None = None

    @property
    def rate(self) -> float:
        return normal_failure_rate(self.pool_failures, self.pool_requests)


@dataclass(frozen=True, slots=True)
class EndpointJudgement:
    """One endpoint's *failing more* verdict (FR2)."""

    name: str
    verdict: Verdict
    # Its own share failing reached critical (D27); severity, never a verdict.
    critical: bool
    requests: int
    failures: int
    normal_rate: float
    tail: float | None
    # The base test passed at alpha / m: one minute of this endpoint's excursion.
    above: bool = False

    @property
    def judged(self) -> bool:
        return self.verdict in JUDGED

    @property
    def rate(self) -> float:
        return self.failures / self.requests if self.requests else 0.0


@dataclass(frozen=True, slots=True)
class FailingJudgement:
    verdict: Verdict
    critical: bool
    window_requests: int
    window_failures: int
    normal_rate: float | None
    wander: float | None
    tail: float | None
    # The base test passed, with and without the worst minute: one minute of an
    # excursion, whose run the burst length is measured against (D17).
    above: bool
    calm_minutes: int = 0
    endpoints: tuple[EndpointJudgement, ...] = ()

    @property
    def judged(self) -> bool:
        return self.verdict in JUDGED

    @property
    def rate(self) -> float:
        return self.window_failures / self.window_requests if self.window_requests else 0.0


def judge_failing(
    window: Window,
    baseline: FailingBaseline | None,
    sensitivity: Sensitivity,
    *,
    run_before: int = 0,
    endpoint_runs: Mapping[str, int] | None = None,
) -> FailingJudgement:
    """*Failing more* on counts (FR10): a beta-binomial tail against the normal rate,
    the minimum change and the 80% rule, the service's burst profile when its
    history is bursty, and each endpoint in the pool (FR2).

    `run_before` is how many windows in a row, up to the minute before, passed the
    base test: the excursion this window may continue; `endpoint_runs` is the same
    for each endpoint."""
    _check_width(window, sensitivity)
    requests, failures = window.requests, window.errors
    if baseline is None or baseline.window_minutes != sensitivity.window_minutes:
        return FailingJudgement("no_baseline", False, requests, failures, None, None, None, False)
    if baseline.mode == "learning":
        return FailingJudgement("learning", False, requests, failures, None, None, None, False)
    endpoints = _judge_endpoints(window, baseline, sensitivity, endpoint_runs or {})
    rate, wander = baseline.rate, baseline.wander
    if requests < REQUESTS_TO_JUDGE_FAILING:
        return FailingJudgement(
            "too_little_traffic", False, requests, failures, rate, wander, None, False, 0, endpoints
        )
    counts = [(minute.requests, minute.errors) for minute in window.minutes]
    leaning = _failing_leaning(counts, rate, sensitivity)
    candidate = (
        failures >= sensitivity.failing_minimum
        and failures / requests - rate >= sensitivity.failing_points
        and sum(leaning) >= sensitivity.most_minutes
    )
    above = _passes_tail(failures, requests, counts, rate, wander, ALPHA, forced=candidate)
    critical = failures / requests >= CRITICAL_FAILING
    tail = _tail_if_raised_counts(failures, requests, rate, wander)

    def judged(verdict: Verdict, calm: int = 0) -> FailingJudgement:
        return FailingJudgement(
            verdict, critical, requests, failures, rate, wander, tail, above, calm, endpoints
        )

    unusual = candidate and above
    profile = baseline.profile
    # D26: a window at or above critical is never tolerated as a burst.
    if unusual and profile is not None and profile.bursty and not critical:
        excursion = run_before + 1 + sensitivity.window_minutes - 1
        unusual = _without_worst(counts) > profile.height(requests) or excursion > profile.minutes
    if unusual:
        return judged("unusual", leaning.index(True))
    if _clear(failures, requests, rate, tail, sensitivity.failing_minimum, sensitivity):
        return judged("normal")
    return judged("elevated")


def _judge_endpoints(
    window: Window,
    baseline: FailingBaseline,
    sensitivity: Sensitivity,
    runs: Mapping[str, int],
) -> tuple[EndpointJudgement, ...]:
    """FR2, at alpha / m for the m endpoints over the request floor in this window, with
    at least 10 failures; an endpoint whose own history is bursty tolerates its usual
    bursts, as the service does (D18), never at or above critical (D26)."""
    counted = {
        name: window.endpoint(name)
        for name in sorted(window.endpoint_names & set(baseline.endpoints))
    }
    totals = {name: (sum(n for n, _ in c), sum(e for _, e in c)) for name, c in counted.items()}
    judged_count = sum(1 for n, _ in totals.values() if n >= ENDPOINT_REQUESTS_TO_JUDGE)
    alpha = ALPHA / max(judged_count, 1)
    found = []
    for name, counts in counted.items():
        normal = baseline.endpoints[name]
        requests, failures = totals[name]
        rate = normal.rate
        if requests < ENDPOINT_REQUESTS_TO_JUDGE:
            found.append(
                EndpointJudgement(name, "too_little_traffic", False, requests, failures, rate, None)
            )
            continue
        candidate = (
            failures >= ENDPOINT_FAILURES_TO_JUDGE
            and failures / requests - rate >= sensitivity.failing_points
            and sum(_failing_leaning(counts, rate, sensitivity)) >= sensitivity.most_minutes
        )
        above = _passes_tail(
            failures, requests, counts, rate, normal.wander, alpha, forced=candidate
        )
        critical = failures / requests >= CRITICAL_FAILING
        unusual = candidate and above
        profile = normal.profile
        if unusual and profile is not None and profile.bursty and not critical:
            excursion = runs.get(name, 0) + 1 + sensitivity.window_minutes - 1
            unusual = (
                _without_worst(counts) > profile.height(requests) or excursion > profile.minutes
            )
        tail = _tail_if_raised_counts(failures, requests, rate, normal.wander)
        if unusual:
            verdict: Verdict = "unusual"
        elif _clear(failures, requests, rate, tail, ENDPOINT_FAILURES_TO_JUDGE, sensitivity):
            verdict = "normal"
        else:
            verdict = "elevated"
        found.append(
            EndpointJudgement(name, verdict, critical, requests, failures, rate, tail, above)
        )
    return tuple(found)


def _failing_leaning(
    counts: Sequence[EndpointCounts], rate: float, sensitivity: Sensitivity
) -> list[bool]:
    """Each minute's own failure share past the minimum change: the 80% rule's minutes."""
    return [n > 0 and e / n >= rate + sensitivity.failing_points for n, e in counts]


def _passes_tail(
    failures: int,
    requests: int,
    counts: Sequence[EndpointCounts],
    rate: float,
    wander: float,
    alpha: float,
    *,
    forced: bool,
) -> bool:
    """The base test: the tail below alpha, and again without the minute with the most
    failures. Windows under 1.5 spreads skip it unless `forced`, as the simulation's."""
    if not forced and _spreads(failures, requests, rate, wander) < PRESCREEN_SPREADS:
        return False
    if beta_binomial_tail(failures, requests, rate, wander) >= alpha:
        return False
    worst_requests, worst_failures = max(counts, key=lambda pair: pair[1])
    rest = beta_binomial_tail(failures - worst_failures, requests - worst_requests, rate, wander)
    return rest < alpha


def _spreads(failures: int, requests: int, rate: float, wander: float) -> float:
    variance = requests * rate * (1 - rate) * (1 + (requests - 1) * wander)
    return (failures - requests * rate) / math.sqrt(variance) if variance > 0 else math.inf


def _without_worst(counts: Sequence[EndpointCounts]) -> float:
    """The window's failure rate without its worst minute: the height a burst is
    compared at, as the tail test is (FR12)."""
    worst_requests, worst_failures = max(counts, key=lambda pair: pair[1])
    rest = sum(n for n, _ in counts) - worst_requests
    return (sum(e for _, e in counts) - worst_failures) / rest if rest > 0 else 0.0


def _tail_if_raised_counts(
    failures: int, requests: int, rate: float, wander: float
) -> float | None:
    if failures <= requests * rate:
        return None
    return beta_binomial_tail(failures, requests, rate, wander)


def _clear(
    failures: int,
    requests: int,
    rate: float,
    tail: float | None,
    minimum: int,
    sensitivity: Sensitivity,
) -> bool:
    """Clear for closing: normal, or no longer past the minimum change (design 2.1, AC30)."""
    return (
        tail is None
        or tail > NORMAL_TAIL
        or failures < minimum
        or failures / requests - rate < sensitivity.failing_points
    )


def normal_failure_rate(pool_failures: float, pool_requests: float) -> float:
    return (pool_failures + PRIOR_FAILURES) / (pool_requests + PRIOR_REQUESTS)


def volume_level(requests: float) -> int:
    """The power of two nearest a window's requests: which burst height applies.
    Python's round, half to even, as numpy's in the simulation."""
    return round(math.log2(max(requests, 1.0)))


def burst_profile(
    windows: Sequence[EndpointCounts | None],
    above: Sequence[bool],
    width: int,
    days: float,
) -> BurstProfile:
    """The burst profile over a run of consecutive window-end minutes, oldest first.

    `windows` holds each minute's window (requests, failures), None where the
    minute is kept out (BR3, D26). `above` says whether that window passed the base
    test when it was judged. A height is the highest rate of the usable windows
    over the request floor within a factor of two of its level's volume; the
    length is the longest run of usable windows above the base test, plus the
    window's lead, never under the window."""
    rates = [
        (each[0], each[1] / each[0])
        for each in windows
        if each is not None and each[0] >= REQUESTS_TO_JUDGE_FAILING
    ]
    heights: dict[int, float] = {}
    levels = {volume_level(n) + step for n, _ in rates for step in (-1, 0, 1)}
    for level in sorted(levels):
        volume = float(2**level)
        alike = [rate for n, rate in rates if volume / 2 <= n <= volume * 2]
        if len(alike) >= PROFILE_MIN_WINDOWS:
            heights[level] = max(alike)
    runs: list[int] = []
    run = 0
    for each, passed in zip(windows, above, strict=True):
        if each is not None and passed:
            run += 1
        elif run:
            runs.append(run)
            run = 0
    if run:
        runs.append(run)
    longest = max((length + width - 1 for length in runs), default=width)
    return BurstProfile(heights, max(longest, width), len(runs), days)


def runs_profile(
    windows: Sequence[EndpointCounts | None],
    runs: Sequence[int],
    width: int,
    days: float,
) -> BurstProfile:
    """A burst profile whose excursions were recorded as they were judged, as an
    endpoint's are: `runs` holds each one's length in windows, already cut where
    minutes are kept out. Heights come from `windows` as `burst_profile`'s do."""
    heights = burst_profile(windows, [False] * len(windows), width, days).heights
    longest = max((length + width - 1 for length in runs), default=width)
    return BurstProfile(heights, max(longest, width), len(runs), days)


def window_above(counts: Sequence[EndpointCounts], rate: float, wander: float) -> bool:
    """Whether a past window passed the base test at the normal it was judged by:
    rebuilt from its minutes and its reading's rate and wander, for the profile."""
    failures = sum(e for _, e in counts)
    requests = sum(n for n, _ in counts)
    if requests < REQUESTS_TO_JUDGE_FAILING:
        return False
    return _passes_tail(failures, requests, counts, rate, wander, ALPHA, forced=False)


# ------------------------------------------------------------- losing traffic


@dataclass(frozen=True, slots=True)
class TrafficBaseline:
    """The normal one minute of *losing traffic* is judged by, from the hourly job.

    `median` and `spread` are the level pool's window counts on the square-root
    (Anscombe) scale: its median and its MAD as a standard deviation, floored at 1.
    `stops_only` is a history under 3 weeks (FR6): only a stop is judged (D30), and
    a clear drop is kept out of the normal without a finding.
    """

    mode: Mode
    days: int
    window_minutes: int
    median: float | None
    spread: float
    stops_only: bool = False
    # Until when *losing traffic* waits: its class's third day, or day 21 (FR6).
    learning_until: date | None = None

    @property
    def expected(self) -> float:
        """The window's expected count, back from the square-root scale."""
        if self.median is None:
            return 0.0
        return max((self.median / 2) ** 2 - ANSCOMBE_SHIFT, 0.0)


@dataclass(frozen=True, slots=True)
class TrafficJudgement:
    verdict: Verdict
    # The window fell to the critical share of its normal; severity, never a verdict.
    critical: bool
    window_requests: int
    expected: float | None
    z: float | None
    # Counts towards closing an open finding (FR13). A window with no requests never
    # does: a stopped service is not recovered by a quiet hour (FR32).
    clears: bool = False
    # Nothing arrived in the whole window, against a normal that expects some (D30).
    stop: bool = False
    # Before 3 weeks, a clear drop: kept out of the normal, never a finding (FR6).
    shadow: bool = False
    calm_minutes: int = 0

    @property
    def judged(self) -> bool:
        return self.verdict in JUDGED


def judge_traffic(
    window: Window, baseline: TrafficBaseline | None, sensitivity: Sensitivity
) -> TrafficJudgement:
    """*Losing traffic* on the square-root scale: at least 4 spreads below the level
    pool's median, with and without the window's lowest minute, at most the
    minimum ratio of the normal, and 80% of the minutes low. Judged only when the
    normal expects the sensitivity's floor (FR11), except a stop (D30)."""
    _check_width(window, sensitivity)
    requests = window.requests
    if baseline is None or baseline.window_minutes != sensitivity.window_minutes:
        return TrafficJudgement("no_baseline", False, requests, None, None)
    if baseline.mode == "learning":
        return TrafficJudgement("learning", False, requests, None, None)
    if baseline.median is None:
        return TrafficJudgement("too_little_traffic", False, requests, 0.0, None)
    width = sensitivity.window_minutes
    expected, spread = baseline.expected, baseline.spread
    z = (_anscombe(requests) - baseline.median) / spread
    lowest = min(minute.requests for minute in window.minutes)
    z_without = (_anscombe((requests - lowest) * width / (width - 1)) - baseline.median) / spread
    ceiling = sensitivity.traffic_ratio * expected
    leaning = [minute.requests <= ceiling / width for minute in window.minutes]
    low = (
        z <= -SPREADS
        and z_without <= -SPREADS
        and requests <= ceiling
        and sum(leaning) >= sensitivity.most_minutes
    )
    stop = requests == 0 and expected > 0 and low
    eligible = expected >= sensitivity.traffic_floor
    critical = requests <= CRITICAL_TRAFFIC * expected
    recovered = requests > 0 and (z > NORMAL_TRAFFIC_SPREADS or requests > ceiling)

    def judged(verdict: Verdict, **flags: bool) -> TrafficJudgement:
        calm = leaning.index(True) if verdict == "unusual" else 0
        clears = flags.pop("clears", verdict == "normal" and recovered)
        return TrafficJudgement(
            verdict, critical, requests, expected, z, clears, stop, calm_minutes=calm, **flags
        )

    if stop or (low and eligible and not baseline.stops_only):
        return judged("unusual")
    if baseline.stops_only:
        return judged("learning", clears=recovered, shadow=low and eligible)
    if not eligible:
        return judged("too_little_traffic", clears=recovered)
    return judged("normal" if recovered else "elevated")


def _anscombe(count: float) -> float:
    return 2 * math.sqrt(count + ANSCOMBE_SHIFT)


def traffic_spread(values: Sequence[float]) -> tuple[float | None, float]:
    """A pool's median and spread on the square-root scale: the MAD as a standard
    deviation, floored at Poisson's 1. No values: no median."""
    if not values:
        return None, _SPREAD_FLOOR
    ordered = sorted(values)
    middle = _sorted_median(ordered)
    deviations = sorted(abs(value - middle) for value in ordered)
    return middle, max(_MAD_SCALE * _sorted_median(deviations), _SPREAD_FLOOR)


def traffic_value(window_requests: float) -> float:
    """A pool's value for one window: its count on the square-root scale."""
    return _anscombe(window_requests)


def _sorted_median(ordered: Sequence[float]) -> float:
    count = len(ordered)
    return 0.5 * (ordered[(count - 1) // 2] + ordered[count // 2])


# ------------------------------------------------------------------ statistics


def bucket_p95(buckets: Sequence[int]) -> float | None:
    """The p95 of bucket counts, log-linear inside its bucket; None when empty."""
    total = sum(buckets)
    if total <= 0:
        return None
    target = 0.95 * total
    running = 0
    for index, count in enumerate(buckets):
        running += count
        if running >= target - 1e-9:
            below = running - count
            fraction = min(max((target - below) / max(count, 1e-12), 0.0), 1.0)
            low, high = LOG_EDGES[index], LOG_EDGES[index + 1]
            return math.exp(low + fraction * (high - low))
    raise AssertionError("a positive total always reaches its 95th percentile")


def threshold_share(buckets: Sequence[int]) -> tuple[int, float]:
    """L's edge, nearest the pool's p95, and the pool's share above it.

    An empty pool has no p95: the first inner edge and a 5% share, never judged."""
    total = sum(buckets)
    if total <= 0:
        return 1, _EMPTY_POOL_SHARE
    running = 0
    best_index, best_distance = 1, math.inf
    for index in range(1, BUCKET_COUNT):
        running += buckets[index - 1]
        distance = abs(running / total - _THRESHOLD_SHARE)
        if distance < best_distance:
            best_index, best_distance = index, distance
    above = total - sum(buckets[:best_index])
    return best_index, max(above, _HALF_REQUEST) / max(total, 1)


def moment_wander(
    requests: float, above: float, requests_squared: float, above_squared: float, both: float
) -> float:
    """The beta-binomial intra-class correlation by the method of moments, over the
    windows of a pool: their summed requests, above-L counts, squares and product.
    Floored at 0 (binomial), and 0 wherever it cannot be estimated."""
    if requests <= 0:
        return 0.0
    share = above / requests
    spread = requests_squared - requests
    if not 0 < share < 1 or spread <= 0:
        return 0.0
    scatter = above_squared - 2 * share * both + share * share * requests_squared
    wander = (scatter / (share * (1 - share)) - (requests - requests_squared / requests)) / spread
    if not math.isfinite(wander):
        return 0.0
    return min(max(wander, 0.0), _WANDER_CEILING)


def beta_binomial_tail(at_least: int, trials: int, share: float, wander: float) -> float:
    """P(X ≥ at_least) for X beta-binomial(trials, share, intra-class correlation
    `wander`); binomial when the wander is 0.

    Summed exactly in log space by the ratio of consecutive terms, so it neither
    overflows nor underflows. The upper tail is summed when it is the smaller side,
    and stops once its terms, falling past the mode, no longer change the total.
    """
    if at_least <= 0:
        return 1.0
    if at_least > trials:
        return 0.0
    share = min(max(share, _SHARE_FLOOR), 1 - _SHARE_FLOOR)
    shapes = _beta_shapes(share, wander)
    if at_least > trials * share:
        return min(1.0, math.exp(_log_sum_up(at_least, trials, share, shapes)))
    below = math.exp(_log_sum_down(at_least - 1, trials, share, shapes))
    return min(max(1.0 - below, 0.0), 1.0)


def _beta_shapes(share: float, wander: float) -> tuple[float, float] | None:
    if wander <= _BINOMIAL_WANDER:
        return None
    scale = 1.0 / min(wander, _WANDER_CEILING) - 1.0
    return share * scale, (1 - share) * scale


def _log_term(k: int, trials: int, share: float, shapes: tuple[float, float] | None) -> float:
    choose = math.lgamma(trials + 1) - math.lgamma(k + 1) - math.lgamma(trials - k + 1)
    if shapes is None:
        return choose + k * math.log(share) + (trials - k) * math.log1p(-share)
    a, b = shapes
    return (
        choose
        + math.lgamma(k + a)
        + math.lgamma(trials - k + b)
        - math.lgamma(trials + a + b)
        - (math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b))
    )


def _log_ratio(k: int, trials: int, share: float, shapes: tuple[float, float] | None) -> float:
    """log(P(X = k + 1) / P(X = k))."""
    if shapes is None:
        return math.log((trials - k) / (k + 1)) + math.log(share) - math.log1p(-share)
    a, b = shapes
    return math.log((trials - k) * (k + a)) - math.log((k + 1) * (trials - k - 1 + b))


def _log_concave(shapes: tuple[float, float] | None) -> bool:
    """Single-peaked, so a term falling past the mode stays falling."""
    return shapes is None or (shapes[0] >= 1 and shapes[1] >= 1)


def _log_sum_up(start: int, trials: int, share: float, shapes: tuple[float, float] | None) -> float:
    """log Σ P(X = k) for k from `start` to `trials`."""
    term = _log_term(start, trials, share, shapes)
    peak, scaled = term, 1.0
    stops = _log_concave(shapes)
    for k in range(start, trials):
        step = _log_ratio(k, trials, share, shapes)
        term += step
        peak, scaled = _accumulate(peak, scaled, term)
        if stops and step < math.log(_DECREASING) and term - peak < _NEGLIGIBLE_LOG:
            break
    return peak + math.log(scaled)


def _log_sum_down(
    start: int, trials: int, share: float, shapes: tuple[float, float] | None
) -> float:
    """log Σ P(X = k) for k from 0 to `start`."""
    term = _log_term(start, trials, share, shapes)
    peak, scaled = term, 1.0
    stops = _log_concave(shapes)
    for k in range(start, 0, -1):
        step = -_log_ratio(k - 1, trials, share, shapes)
        term += step
        peak, scaled = _accumulate(peak, scaled, term)
        if stops and step < math.log(_DECREASING) and term - peak < _NEGLIGIBLE_LOG:
            break
    return peak + math.log(scaled)


def _accumulate(peak: float, scaled: float, term: float) -> tuple[float, float]:
    """Add exp(term) to exp(peak) * scaled, keeping the largest exponent as the scale."""
    if term > peak:
        return term, scaled * math.exp(peak - term) + 1.0
    return peak, scaled + math.exp(term - peak)
