"""The statistical method: whether one window of a service is unusual against its
normal (design, "The statistical method"; spec FR9 to FR14).

Pure: no store, no clock. The project job reads the window and the hourly
baseline, and this decides. Ported from the simulation the design was approved on
(`tools/detection-sim/detect_sim.py`, `judge_slower`, `bb_sf`, `mom_rho`,
`hist_p95`), and held to it by `test_detection_method.py`'s parity cases.

*Slower* only for now (plan T2c.2). *Failing more* and *losing traffic* join in
T3.1 beside it, sharing `Window`, `Sensitivity` and the verdict vocabulary.

A window is **unusual** only if every condition holds, and also with its single
worst minute removed; and at least 80% of its minutes lean the same way. So one
minute, however extreme, never opens a finding (FR12).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class Minute:
    """One minute of one service, from `service_stats_1m`. A minute with no row
    is a minute with no requests."""

    requests: int
    errors: int
    buckets: tuple[int, ...]

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
    if len(window.minutes) != sensitivity.window_minutes:
        raise ValueError(
            f"a {sensitivity.name} window is {sensitivity.window_minutes} minutes, "
            f"not {len(window.minutes)}"
        )
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
