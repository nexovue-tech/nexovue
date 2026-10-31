"""The statistical method's *slower* signal (design, "The statistical method"; plan T2c.2).

Two kinds of evidence. **Parity**: every window the simulation judged, frozen in
`fixtures/detection_parity.json` by `fixtures/make_detection_parity.py`, gets the
same verdict here. **Acceptance**: synthetic series for AC2, AC5, AC6 and AC8.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import pytest

from nexovue_scheduler.detection.method import (
    ALPHA,
    BUCKET_COUNT,
    REQUESTS_TO_JUDGE_SLOWER,
    SENSITIVITIES,
    Minute,
    Sensitivity,
    SlowerBaseline,
    Window,
    beta_binomial_tail,
    bucket_p95,
    judge_slower,
    moment_wander,
    threshold_share,
)

PARITY: Final = json.loads(
    (Path(__file__).parent / "fixtures" / "detection_parity.json").read_text(encoding="utf-8")
)
WINDOW_OF: Final = {level.name: level.window_minutes for level in SENSITIVITIES.values()}

# A steady service's latency shape over 11 adjacent buckets, in percent: its p95
# falls near bucket 27, about 120 ms.
SHAPE: Final = (1, 3, 8, 15, 22, 20, 14, 9, 5, 2, 1)
FIRST_BUCKET: Final = 20
# One bucket is 60000^(1/64), about 1.19x: 5 buckets is about 2.4x, past even
# low's 2x; 9 is about 4.7x, past critical.
PAST_THE_MINIMUM: Final = 5
PAST_CRITICAL: Final = 9
TEN_TIMES: Final = 14
REQUESTS_A_MINUTE: Final = 100


def _buckets(sparse: Sequence[int]) -> tuple[int, ...]:
    buckets = [0] * BUCKET_COUNT
    for index in range(0, len(sparse), 2):
        buckets[sparse[index]] = sparse[index + 1]
    return tuple(buckets)


def _minute(requests: int = REQUESTS_A_MINUTE, *, slower_by: int = 0) -> Minute:
    """A minute of the steady shape, moved up `slower_by` buckets."""
    buckets = [0] * BUCKET_COUNT
    for offset, percent in enumerate(SHAPE):
        buckets[FIRST_BUCKET + offset + slower_by] += requests * percent // 100
    return Minute(sum(buckets), 0, tuple(buckets))


def _baseline(sensitivity: Sensitivity, *, wander: float = 0.002) -> SlowerBaseline:
    """The steady shape's normal: a pool of 4 weeks of its windows."""
    pool = [count * 4 * 31 * sensitivity.window_minutes for count in _minute().buckets]
    index, share = threshold_share(pool)
    return SlowerBaseline(
        mode="weekly",
        days=4,
        window_minutes=sensitivity.window_minutes,
        pool_requests=sum(pool),
        threshold_index=index,
        share_above=share,
        wander=wander,
        normal_p95_ms=bucket_p95(pool),
    )


def _verdicts(series: Sequence[Minute], sensitivity: Sensitivity) -> list[str]:
    """The verdict of every full window over the series, as the job judges them."""
    width = sensitivity.window_minutes
    baseline = _baseline(sensitivity)
    return [
        judge_slower(Window(tuple(series[end - width : end])), baseline, sensitivity).verdict
        for end in range(width, len(series) + 1)
    ]


def _rise(minutes: int) -> list[Minute]:
    """Twenty normal minutes, a rise past the normal minimum, then twenty normal."""
    normal = [_minute()] * 20
    return [*normal, *[_minute(slower_by=PAST_THE_MINIMUM)] * minutes, *normal]


# ------------------------------------------------------------------- parity


def _parity_cases() -> list[Any]:
    cases = []
    for case in PARITY["windows"]:
        minutes = {
            int(t): Minute(requests, errors, _buckets(sparse))
            for t, (requests, errors, sparse) in case["minutes"].items()
        }
        for judged in case["judgements"]:
            label = f"{case['service']}-{judged['sensitivity']}-{judged['minute']}"
            cases.append(pytest.param(minutes, judged, id=label))
    return cases


@pytest.mark.parametrize(("minutes", "expected"), _parity_cases())
def test_each_window_gets_the_simulation_s_verdict(
    minutes: dict[int, Minute], expected: dict[str, Any]
) -> None:
    sensitivity = SENSITIVITIES[expected["sensitivity"]]
    end = expected["minute"]
    window = Window(tuple(minutes[t] for t in range(end - sensitivity.window_minutes + 1, end + 1)))
    baseline = SlowerBaseline(**expected["baseline"], window_minutes=sensitivity.window_minutes)

    judged = judge_slower(window, baseline, sensitivity)

    assert judged.judged == expected["judged"]
    assert (judged.verdict == "unusual") == expected["unusual"]
    assert judged.above_threshold == expected["above_threshold"]
    if expected["judged"]:
        assert judged.critical == expected["critical"]
        # Normal here is the simulation's normal, or no longer past the minimum (AC30).
        assert judged.ratio is not None
        below_minimum = judged.ratio < sensitivity.slower_ratio
        assert (judged.verdict == "normal") == (
            not expected["unusual"] and (expected["normal"] or below_minimum)
        )


def test_the_parity_cases_cover_every_verdict_and_mode() -> None:
    """A fixture that held only quiet windows would prove nothing about opening."""
    judgements = [j for case in PARITY["windows"] for j in case["judgements"]]
    assert {j["baseline"]["mode"] for j in judgements} == {"weekly", "daily", "learning"}
    assert {j["sensitivity"] for j in judgements} == set(SENSITIVITIES)
    assert sum(j["unusual"] for j in judgements) >= 50
    assert sum(j["normal"] for j in judgements) >= 50
    assert sum(j["judged"] and j["critical"] for j in judgements) >= 10
    assert sum(not j["judged"] and j["baseline"]["mode"] != "learning" for j in judgements) >= 10


# --------------------------------------------------------------- acceptance


@pytest.mark.parametrize("level", sorted(SENSITIVITIES))
def test_a_single_minute_of_any_size_opens_nothing(level: str) -> None:
    """AC2: one minute 10x slower, with normal minutes around it."""
    sensitivity = SENSITIVITIES[level]
    normal = [_minute()] * 20
    series = [*normal, _minute(slower_by=TEN_TIMES), *normal]

    assert "unusual" not in _verdicts(series, sensitivity)


@pytest.mark.parametrize("level", sorted(SENSITIVITIES))
def test_too_few_requests_read_too_little_traffic_however_slow(level: str) -> None:
    """AC5: below the requests needed to judge *slower*, nothing is judged."""
    sensitivity = SENSITIVITIES[level]
    per_minute = (REQUESTS_TO_JUDGE_SLOWER - 1) // sensitivity.window_minutes
    series = [_minute(per_minute, slower_by=PAST_CRITICAL)] * sensitivity.window_minutes

    verdicts = _verdicts(series, sensitivity)

    assert verdicts == ["too_little_traffic"]


@pytest.mark.parametrize("level", sorted(SENSITIVITIES))
def test_a_tiny_change_on_a_steady_service_opens_nothing(level: str) -> None:
    """AC6: a p95 moving about 5% on a very steady service is far beyond chance,
    and still below every sensitivity's minimum change."""
    sensitivity = SENSITIVITIES[level]
    baseline = _baseline(sensitivity, wander=0.0)
    steady = _minute(10 * REQUESTS_A_MINUTE)
    # A third of each bucket's requests one bucket slower.
    nudged = [0] * BUCKET_COUNT
    for index, count in enumerate(steady.buckets):
        nudged[index] += count - count // 3
        if count:
            nudged[index + 1] += count // 3
    minute = Minute(sum(nudged), 0, tuple(nudged))
    window = Window((minute,) * sensitivity.window_minutes)

    judged = judge_slower(window, baseline, sensitivity)

    assert judged.ratio is not None
    assert 1.02 < judged.ratio < min(level.slower_ratio for level in SENSITIVITIES.values())
    assert judged.tail is not None and judged.tail < ALPHA, "the change is real, not noise"
    assert judged.verdict == "normal"


@pytest.mark.parametrize("level", sorted(SENSITIVITIES))
def test_a_rise_past_the_minimum_for_the_sustain_time_is_unusual(level: str) -> None:
    """AC8, the first half: *sustain* minutes past the minimum change."""
    sensitivity = SENSITIVITIES[level]

    verdicts = _verdicts(_rise(sensitivity.window_minutes), sensitivity)

    assert "unusual" in verdicts


def test_a_rise_one_minute_short_of_the_sustain_time_is_not_unusual_at_high() -> None:
    """AC8, the second half, where the design's 80% rule and the spec agree: at
    high sensitivity a 3-minute window needs all 3 minutes."""
    high = SENSITIVITIES["high"]

    assert "unusual" not in _verdicts(_rise(high.window_minutes - 1), high)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AC8 says one minute short of sustain opens nothing; the approved design's "
        "80% rule (ceil(0.8 x window) minutes leaning) accepts 4 of 5 at normal and "
        "8 of 10 at low, as the simulation does. Founder decision pending."
    ),
)
@pytest.mark.parametrize("level", ["low", "normal"])
def test_a_rise_one_minute_short_of_the_sustain_time_is_not_unusual(level: str) -> None:
    sensitivity = SENSITIVITIES[level]

    assert "unusual" not in _verdicts(_rise(sensitivity.window_minutes - 1), sensitivity)


def test_a_rise_shorter_than_the_leaning_minutes_is_never_unusual() -> None:
    """What the method does guarantee: fewer than ceil(0.8 x window) minutes past the
    minimum is never unusual, at any sensitivity (3 of 3, 4 of 5, 8 of 10)."""
    for sensitivity in SENSITIVITIES.values():
        verdicts = _verdicts(_rise(sensitivity.most_minutes - 1), sensitivity)
        assert "unusual" not in verdicts, sensitivity.name


def test_a_rise_past_critical_is_critical_and_a_smaller_one_is_not() -> None:
    normal = SENSITIVITIES["normal"]
    baseline = _baseline(normal)

    def judged(slower_by: int) -> tuple[str, bool]:
        window = Window((_minute(slower_by=slower_by),) * normal.window_minutes)
        verdict = judge_slower(window, baseline, normal)
        return verdict.verdict, verdict.critical

    assert judged(PAST_CRITICAL) == ("unusual", True)
    assert judged(PAST_THE_MINIMUM) == ("unusual", False)


def test_a_learning_or_missing_normal_judges_nothing() -> None:
    """AC14's half that the method holds: a learning normal opens nothing."""
    normal = SENSITIVITIES["normal"]
    window = Window((_minute(slower_by=PAST_CRITICAL),) * normal.window_minutes)
    learning = SlowerBaseline("learning", 2, normal.window_minutes, 0, 1, 0.05, 0.0, None)
    other_window = _baseline(SENSITIVITIES["low"])

    assert judge_slower(window, learning, normal).verdict == "learning"
    assert judge_slower(window, None, normal).verdict == "no_baseline"
    assert judge_slower(window, other_window, normal).verdict == "no_baseline"


def test_a_window_of_the_wrong_length_is_refused() -> None:
    normal = SENSITIVITIES["normal"]
    with pytest.raises(ValueError, match="5 minutes"):
        judge_slower(Window((_minute(),) * 3), _baseline(normal), normal)


# ---------------------------------------------------------------- statistics

# Exact tails, summed term by term at 60 digits with mpmath 1.4.1, computed once:
# `uv run --with mpmath --no-project python` over the beta-binomial pmf
# C(n, k) B(k + a, n - k + b) / B(a, b), a = p k, b = (1 - p) k, k = 1/rho - 1, or the
# binomial pmf when rho is 0. Not scipy: its `betabinom.sf` is 1 - cdf, and was
# measured wrong below about 1e-12 (6.9e-13 where the sum is 6.1e-32).
EXACT_TAILS: Final = (
    (30, 200, 0.05, 0.0, 8.709270943980006e-08),
    (25, 400, 0.05, 0.003, 0.23015446165432313),
    (60, 1000, 0.05, 0.01, 0.2985199290798074),
    (5, 10, 0.3, 0.2, 0.261469151232),
    (1, 50, 0.01, 0.0, 0.39499393286246337),
    (10, 200, 0.05, 0.003, 0.517358419520674),
    (120, 500, 0.05, 0.5, 0.0723586583222144),
    (0, 10, 0.5, 0.1, 1.0),
    (11, 10, 0.5, 0.1, 0.0),
    (400, 1000, 0.3, 0.05, 0.17155167713972796),
    (2, 1000, 0.0001, 0.0, 0.004674767851740149),
    (60, 400, 0.05, 0.003, 2.390256607238034e-06),
    (90, 500, 0.05, 0.01, 0.00010506790810770334),
    (250, 600, 0.05, 0.004, 6.058252666359481e-32),
    (3600, 60000, 0.05, 0.002, 0.15321799863736538),
)


@pytest.mark.parametrize(("at_least", "trials", "share", "wander", "expected"), EXACT_TAILS)
def test_the_tail_matches_an_exact_sum(
    at_least: int, trials: int, share: float, wander: float, expected: float
) -> None:
    assert beta_binomial_tail(at_least, trials, share, wander) == pytest.approx(
        expected, rel=1e-9, abs=1e-300
    )


def test_a_tail_too_small_for_a_float_is_zero_not_an_error() -> None:
    assert 0.0 <= beta_binomial_tail(900, 1000, 0.05, 0.0) < 1e-300


def test_the_wander_is_zero_for_binomial_counts_and_positive_for_overdispersed() -> None:
    """Method of moments over windows: equal shares in every window is no wander."""
    windows = [(1000, 50)] * 20
    flat = moment_wander(*_moments(windows))
    wandering = moment_wander(*_moments([(1000, 20), (1000, 80)] * 10))

    assert flat == 0.0
    assert 0.0 < wandering < 1.0
    assert moment_wander(0, 0, 0, 0, 0) == 0.0


def _moments(windows: Sequence[tuple[int, int]]) -> tuple[float, float, float, float, float]:
    return (
        float(sum(n for n, _ in windows)),
        float(sum(a for _, a in windows)),
        float(sum(n * n for n, _ in windows)),
        float(sum(a * a for _, a in windows)),
        float(sum(n * a for n, a in windows)),
    )


def test_the_p95_interpolates_inside_its_bucket() -> None:
    buckets = [0] * BUCKET_COUNT
    buckets[10] = 100
    edge = 60_000 ** (1 / BUCKET_COUNT)

    p95 = bucket_p95(buckets)

    assert p95 is not None
    assert edge**10 < p95 < edge**11
    assert math.log(p95) == pytest.approx(math.log(edge) * 10.95)
    assert bucket_p95([0] * BUCKET_COUNT) is None


def test_the_threshold_is_the_edge_nearest_the_pool_s_p95() -> None:
    buckets = [0] * BUCKET_COUNT
    buckets[5], buckets[6] = 94, 6

    index, share = threshold_share(buckets)

    assert index == 6
    assert share == pytest.approx(0.06)
    assert threshold_share([0] * BUCKET_COUNT) == (1, 0.05)
