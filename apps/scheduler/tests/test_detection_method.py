"""The statistical method's three signals (design, "The statistical method"; plan
T2c.2, T3.1).

Two kinds of evidence. **Parity**: every window the simulation judged, frozen in
`fixtures/detection_parity.json` by `fixtures/make_detection_parity.py`, gets the
same verdict here, apart from the three decisions taken after it (D26, D27, D30),
each asserted where it applies. **Acceptance**: synthetic series for AC2, AC4 to
AC12, AC41's small service and AC46.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from detection_series import daily, poisson, series, span
from detection_series import minute as series_minute
from detection_series import window as series_window

from nexovue_scheduler.detection.baselines import Baselines, service_baselines
from nexovue_scheduler.detection.method import (
    ALPHA,
    BUCKET_COUNT,
    CRITICAL_FAILING,
    REQUESTS_TO_JUDGE_SLOWER,
    SENSITIVITIES,
    BurstProfile,
    EndpointNormal,
    FailingBaseline,
    Minute,
    Sensitivity,
    SlowerBaseline,
    TrafficBaseline,
    Window,
    beta_binomial_tail,
    bucket_p95,
    burst_profile,
    judge_failing,
    judge_slower,
    judge_traffic,
    moment_wander,
    runs_profile,
    threshold_share,
    traffic_spread,
    traffic_value,
    window_above,
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


def _signal_minutes(case: dict[str, Any]) -> dict[int, Minute]:
    """A case's minutes: [requests, errors, [endpoint, requests, errors, …]]."""
    minutes = {}
    for t, (requests, errors, endpoints) in case["minutes"].items():
        counts = {
            f"ep{endpoints[i]}": (endpoints[i + 1], endpoints[i + 2])
            for i in range(0, len(endpoints), 3)
        }
        minutes[int(t)] = Minute(requests, errors, (0,) * BUCKET_COUNT, counts)
    return minutes


def _signal_cases(signal: str) -> list[Any]:
    return [
        pytest.param(
            _signal_minutes(case),
            judged,
            id=f"{case['service']}-{judged['sensitivity']}-{judged['minute']}",
        )
        for case in PARITY["signals"]
        for judged in case[signal]
    ]


def _window(minutes: dict[int, Minute], end: int, sensitivity: Sensitivity) -> Window:
    return Window(tuple(minutes[t] for t in range(end - sensitivity.window_minutes + 1, end + 1)))


def _profile(stored: dict[str, Any]) -> BurstProfile:
    return BurstProfile(
        heights={int(level): height for level, height in stored["heights"].items()},
        minutes=stored["minutes"],
        excursions=stored["excursions"],
        days=stored["days"],
    )


def _failing_baseline(summary: dict[str, Any], sensitivity: Sensitivity) -> FailingBaseline:
    return FailingBaseline(
        mode=summary["mode"],
        days=summary["days"],
        window_minutes=sensitivity.window_minutes,
        pool_requests=summary["pool_requests"],
        pool_failures=summary["pool_failures"],
        wander=summary["wander"],
        profile=_profile(summary["profile"]),
        endpoints={
            name: EndpointNormal(requests, failures, wander, _profile(profile))
            for name, (requests, failures, wander, profile) in summary["endpoints"].items()
        },
    )


@pytest.mark.parametrize(("minutes", "expected"), _signal_cases("failing"))
def test_each_failing_window_gets_the_simulation_s_verdict(
    minutes: dict[int, Minute], expected: dict[str, Any]
) -> None:
    sensitivity = SENSITIVITIES[expected["sensitivity"]]
    baseline = _failing_baseline(expected["baseline"], sensitivity)

    judged = judge_failing(
        _window(minutes, expected["minute"], sensitivity),
        baseline,
        sensitivity,
        run_before=expected["run_before"],
        endpoint_runs={name: each[5] for name, each in expected["endpoints"].items()},
    )

    assert judged.judged == expected["judged"]
    assert judged.above == expected["above"]
    # D26: at or above critical the burst profile never tolerates the window.
    tolerated = expected["unusual"] or (expected["plain"] and expected["critical"])
    assert (judged.verdict == "unusual") == tolerated
    if expected["judged"]:
        assert judged.critical == expected["critical"]
        below_minimum = (
            judged.window_failures < sensitivity.failing_minimum
            or judged.rate - baseline.rate < sensitivity.failing_points
        )
        assert (judged.verdict == "normal") == (
            not tolerated and (expected["normal"] or below_minimum)
        )
    endpoints = {each.name: each for each in judged.endpoints if each.judged}
    assert set(endpoints) == set(expected["endpoints"])
    for name, (adaptive, critical, normal, plain, above, _) in expected["endpoints"].items():
        each = endpoints[name]
        assert each.above == bool(above), name
        # D18 per endpoint, and D26: never tolerated at or above critical.
        unusual = adaptive or (plain and critical)
        assert (each.verdict == "unusual") == bool(unusual), name
        assert each.critical == bool(critical), name
        below_minimum = each.failures < 10 or each.rate - each.normal_rate < (
            sensitivity.failing_points
        )
        assert (each.verdict == "normal") == (not unusual and (bool(normal) or below_minimum))


@pytest.mark.parametrize(("minutes", "expected"), _signal_cases("traffic"))
def test_each_traffic_window_gets_the_simulation_s_verdict(
    minutes: dict[int, Minute], expected: dict[str, Any]
) -> None:
    sensitivity = SENSITIVITIES[expected["sensitivity"]]
    window = _window(minutes, expected["minute"], sensitivity)
    baseline = TrafficBaseline(**expected["baseline"], window_minutes=sensitivity.window_minutes)

    judged = judge_traffic(window, baseline, sensitivity)

    assert judged.z == pytest.approx(expected["z"], rel=1e-12)
    # D30: a stop is judged at any expected traffic and from day 3, so before 3
    # weeks it opens a finding where the simulation only kept it out of the normal.
    assert not judged.stop or window.requests == 0
    assert judged.shadow == (expected["shadow"] and not judged.stop)
    assert (judged.verdict == "unusual") == (expected["unusual"] or judged.stop)
    if expected["judged"]:
        assert judged.critical == expected["critical"]
        recovered = window.requests > sensitivity.traffic_ratio * baseline.expected
        assert (judged.verdict == "normal") == (
            not expected["unusual"] and (expected["normal"] or recovered)
        )
    elif not judged.stop:
        assert judged.verdict == ("learning" if baseline.stops_only else "too_little_traffic")


def test_the_signal_cases_cover_every_path() -> None:
    """Calm and bursty histories, tolerance used and refused, endpoints, stops and
    the shadow before 3 weeks; a fixture of quiet windows would prove nothing."""
    failing = [j for case in PARITY["signals"] for j in case["failing"]]
    traffic = [j for case in PARITY["signals"] for j in case["traffic"]]
    assert sum(j["unusual"] for j in failing) >= 50
    assert any(j["plain"] and not j["unusual"] for j in failing), "a burst tolerated"
    assert any(j["plain"] and j["critical"] for j in failing), "critical, D26"
    assert any(j["baseline"]["profile"]["heights"] for j in failing)
    endpoints = [e for j in failing for e in j["endpoints"].values()]
    assert sum(e[0] for e in endpoints) >= 50
    assert any(e[0] and not j["unusual"] for j in failing for e in j["endpoints"].values())
    assert any(e[3] and not e[0] for e in endpoints), "an endpoint's burst tolerated"
    assert any(
        _profile(normal[3]).bursty
        for j in failing
        for normal in j["baseline"]["endpoints"].values()
    )
    assert sum(j["unusual"] for j in traffic) >= 20
    assert sum(j["shadow"] for j in traffic) >= 10
    assert {j["baseline"]["mode"] for j in traffic} == {"daily", "weekly"}
    assert any(j["baseline"]["stops_only"] for j in traffic)


def test_the_excursion_record_and_the_burst_profile_are_the_simulation_s() -> None:
    """D17's profile from the bursty service's own history: whether each past window
    passed the base test, rebuilt from its minutes and the normal it was judged by,
    and the heights and length taken from that record."""
    (case,) = PARITY["profiles"]
    sensitivity = SENSITIVITIES[case["sensitivity"]]
    width = sensitivity.window_minutes
    counts = [tuple(pair) for pair in case["minutes"]]
    windows = [counts[i : i + width] for i in range(len(counts) - width + 1)]

    rebuilt = [
        window_above(window, rate, wander)
        for window, (rate, wander) in zip(windows, case["normals"], strict=True)
    ]
    sums = [(sum(n for n, _ in w), sum(e for _, e in w)) for w in windows]
    profile = burst_profile(sums, rebuilt, width, days=2.0)

    assert rebuilt == case["above"]
    assert any(case["above"])
    endpoint = case["endpoint"]
    runs, run = [], 0
    for passed in [*endpoint["above"], False]:
        if passed:
            run += 1
        elif run:
            runs.append(run)
            run = 0
    endpoint_windows: list[tuple[int, int] | None] = [
        (int(n), int(e)) for n, e in endpoint["windows"]
    ]
    endpoint_profile = runs_profile(endpoint_windows, runs, width, days=2.0)
    assert endpoint["excursions"] > 0
    assert endpoint_profile.heights.get(endpoint["level"]) == endpoint["height"]
    assert endpoint_profile.minutes == endpoint["minutes"]
    assert endpoint_profile.excursions == endpoint["excursions"]
    for level, expected in case["expected"].items():
        assert profile.heights.get(int(level)) == expected["height"]
        assert profile.minutes == expected["minutes"]
        assert profile.excursions == expected["excursions"]


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


@pytest.mark.parametrize(
    ("level", "leaning"),
    [("high", 3), ("normal", 4), ("low", 8)],
    ids=["3 of 3", "4 of 5", "8 of 10"],
)
def test_a_rise_for_80_percent_of_the_sustain_window_opens_and_a_shorter_one_does_not(
    level: str, leaning: int
) -> None:
    """AC8 as the founder decided it: the 80% rule stands. A rise past the minimum for
    ceil(0.8 x window) minutes is unusual; one minute fewer is not."""
    sensitivity = SENSITIVITIES[level]
    assert sensitivity.most_minutes == leaning

    assert "unusual" in _verdicts(_rise(leaning), sensitivity)
    assert "unusual" not in _verdicts(_rise(leaning - 1), sensitivity)


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


# ------------------------------------------- failing more and losing traffic

NORMAL: Final = SENSITIVITIES["normal"]
UTC_ZONE: Final = ZoneInfo("UTC")
# Days in a pool, and windows on each: 4 weeks of the same weekday at ±15 minutes.
POOL_WINDOWS: Final = 4 * 31
# The settled minute is judged at least 3 minutes after it ends (design, *When a
# minute is settled*): the rest of *time to a finding* is the method's.
SETTLING: Final = timedelta(minutes=3)
TIME_TO_A_FINDING: Final = timedelta(minutes=12)


def _failing(
    rate: float,
    per_window: int,
    *,
    endpoints: Mapping[str, tuple[int, float]] | None = None,
    profile: BurstProfile | None = None,
    sensitivity: Sensitivity = NORMAL,
) -> FailingBaseline:
    """A normal of `rate` failing over windows of `per_window` requests; endpoints by
    their requests per window and rate. No wander: the binomial case."""
    pool = per_window * POOL_WINDOWS
    return FailingBaseline(
        mode="weekly",
        days=4,
        window_minutes=sensitivity.window_minutes,
        pool_requests=pool,
        pool_failures=round(pool * rate),
        wander=0.0,
        profile=profile,
        endpoints={
            name: EndpointNormal(n * POOL_WINDOWS, round(n * POOL_WINDOWS * share), 0.0)
            for name, (n, share) in (endpoints or {}).items()
        },
    )


def _traffic(
    per_minute: float, sensitivity: Sensitivity = NORMAL, *, stops_only: bool = False
) -> TrafficBaseline:
    """A normal pooled from Poisson windows at `per_minute`, as the hourly job pools."""
    rng = random.Random(7)
    width = sensitivity.window_minutes
    values = [
        traffic_value(sum(poisson(rng, per_minute) for _ in range(width)))
        for _ in range(POOL_WINDOWS)
    ]
    median, spread = traffic_spread(values)
    return TrafficBaseline(
        "daily" if stops_only else "weekly",
        4,
        width,
        median,
        spread,
        stops_only=stops_only,
    )


def _first_unusual(
    minutes: Sequence[Minute],
    judge: Callable[[Window], Any],
    width: int,
    *,
    critical: bool = False,
) -> tuple[int, Any] | None:
    """The index of the first minute whose window is unusual, and critical when asked
    (a finding opened earlier rises to critical there), and its judgement."""
    for end in range(width, len(minutes) + 1):
        judged = judge(Window(tuple(minutes[end - width : end])))
        if judged.verdict == "unusual" and (judged.critical or not critical):
            return end - 1, judged
    return None


def test_two_failures_in_four_requests_is_not_a_fifty_percent_failure() -> None:
    """AC4, FR10: too few requests to judge, whatever their share."""
    minutes = (
        series_minute(1, 1),
        series_minute(1, 1),
        series_minute(1),
        series_minute(1),
        Minute.empty(),
    )

    judged = judge_failing(Window(minutes), _failing(0.01, 500), NORMAL)

    assert (judged.window_requests, judged.window_failures) == (4, 2)
    assert judged.verdict == "too_little_traffic"


@pytest.mark.parametrize("per_minute", [5, 100])
def test_one_failure_on_a_service_that_never_fails_opens_nothing(per_minute: int) -> None:
    """AC4, FR10: a normal rate of zero is (0 + 0.5) / (requests + 250), never zero."""
    minutes = [series_minute(per_minute) for _ in range(NORMAL.window_minutes)]
    minutes[2] = series_minute(per_minute, 1)

    judged = judge_failing(Window(tuple(minutes)), _failing(0.0, 5 * per_minute), NORMAL)

    assert judged.verdict == "normal"


def test_an_endpoint_with_one_percent_of_the_traffic_failing_completely_is_named() -> None:
    """AC11, FR2, FR3: the service moves one point, under the minimum; the endpoint is
    unusual on its own, critical at its own share (D27), and is the evidence."""
    baseline = _failing(
        0.005, 10_000, endpoints={"POST /checkout": (9_900, 0.005), "POST /pay": (100, 0.005)}
    )
    busy = {"POST /checkout": (1_980, 10), "POST /pay": (20, 20)}
    failing_minute = series_minute(2_000, 30, endpoints=busy)
    window = Window((failing_minute,) * NORMAL.window_minutes)

    judged = judge_failing(window, baseline, NORMAL)

    assert judged.verdict != "unusual"
    assert not judged.critical
    pay = next(each for each in judged.endpoints if each.name == "POST /pay")
    assert pay.verdict == "unusual"
    assert pay.critical
    assert judged.evidence_endpoint == "POST /pay"
    checkout = next(each for each in judged.endpoints if each.name == "POST /checkout")
    assert checkout.verdict == "normal"


def test_an_endpoint_at_the_critical_share_of_its_own_requests_is_critical() -> None:
    """AC46, D27: a quarter of one endpoint's requests failing is critical, though the
    service as a whole is far from it."""
    baseline = _failing(
        0.005, 5_000, endpoints={"GET /": (4_500, 0.005), "POST /pay": (500, 0.005)}
    )
    busy = {"GET /": (900, 4), "POST /pay": (100, 25)}
    window = Window((series_minute(1_000, 29, endpoints=busy),) * NORMAL.window_minutes)

    judged = judge_failing(window, baseline, NORMAL)

    pay = next(each for each in judged.endpoints if each.name == "POST /pay")
    assert pay.verdict == "unusual" and pay.critical
    assert pay.rate >= CRITICAL_FAILING
    assert not judged.critical


def test_a_failure_beyond_critical_is_a_critical_finding_in_time() -> None:
    """AC12, *failing more*: 30% of requests failing, from a normal of 1%."""
    calm = [series_minute(100, 1)] * 20
    failing = [series_minute(100, 30)] * 15

    found = _first_unusual(
        [*calm, *failing], lambda w: judge_failing(w, _failing(0.01, 500), NORMAL), 5, critical=True
    )

    assert found is not None
    index, judged = found
    assert judged.critical
    assert timedelta(minutes=index - len(calm)) + SETTLING <= TIME_TO_A_FINDING


def test_a_burst_at_or_above_critical_is_never_tolerated() -> None:
    """D26: a bursty service tolerates a burst up to its usual height, but never one
    at the critical share, however high its bursts have run."""
    tolerant = BurstProfile(heights={9: 0.5}, minutes=60, excursions=8, days=28.0)
    baseline = _failing(0.01, 500, profile=tolerant)

    def judged(failures: int) -> str:
        window = Window((series_minute(100, failures),) * NORMAL.window_minutes)
        return judge_failing(window, baseline, NORMAL, run_before=3).verdict

    assert tolerant.bursty
    assert judged(10) != "unusual", "a 10% burst is inside this service's usual bursts"
    assert judged(25) == "unusual"


def test_traffic_falling_to_five_percent_is_a_critical_finding_in_time() -> None:
    """AC9 and AC12, *losing traffic*."""
    baseline = _traffic(100)
    rng = random.Random(3)
    calm = [series_minute(poisson(rng, 100)) for _ in range(20)]
    fallen = [series_minute(poisson(rng, 5)) for _ in range(15)]

    found = _first_unusual(
        [*calm, *fallen], lambda w: judge_traffic(w, baseline, NORMAL), 5, critical=True
    )

    assert found is not None
    index, judged = found
    assert judged.critical
    assert timedelta(minutes=index - len(calm)) + SETTLING <= TIME_TO_A_FINDING
    assert all(
        judge_traffic(Window(tuple(calm[end - 5 : end])), baseline, NORMAL).verdict == "normal"
        for end in range(5, len(calm) + 1)
    )


@pytest.mark.parametrize("stops_only", [True, False], ids=["day 3", "three weeks"])
def test_a_small_service_that_stops_is_a_critical_finding_in_time(stops_only: bool) -> None:
    """AC41, D30: 2 requests a minute is far under the floor for a drop, and before 3
    weeks nothing but a stop is judged; a stop is judged anyway."""
    baseline = _traffic(2, stops_only=stops_only)
    rng = random.Random(5)
    calm = [series_minute(poisson(rng, 2)) for _ in range(30)]
    stopped = [Minute.empty()] * 15

    assert baseline.expected < NORMAL.traffic_floor
    quiet = [judge_traffic(Window(tuple(calm[e - 5 : e])), baseline, NORMAL) for e in range(5, 31)]
    assert {each.verdict for each in quiet} == {"learning" if stops_only else "too_little_traffic"}
    found = _first_unusual(
        [*calm, *stopped], lambda w: judge_traffic(w, baseline, NORMAL), 5, critical=True
    )
    assert found is not None
    index, judged = found
    assert judged.stop and judged.critical
    assert timedelta(minutes=index - len(calm)) + SETTLING <= TIME_TO_A_FINDING


def test_before_three_weeks_a_clear_drop_is_kept_out_and_opens_nothing() -> None:
    """FR6: a drop to a quarter, not a stop, before the weekly pattern exists."""
    baseline = _traffic(100, stops_only=True)
    window = Window((series_minute(25),) * NORMAL.window_minutes)

    judged = judge_traffic(window, baseline, NORMAL)

    assert judged.verdict == "learning"
    assert judged.shadow


# The acceptance criteria below build their normal from history, as the hourly
# job does, rather than from a summary.


def _baselines(
    history: Mapping[datetime, Minute],
    minutes: Sequence[datetime],
    first_seen: datetime,
    zone: ZoneInfo = UTC_ZONE,
) -> dict[datetime, Baselines]:
    return service_baselines(
        history, minutes, zone=zone, first_seen=first_seen, width=NORMAL.window_minutes
    )


def _unusual(
    history: Mapping[datetime, Minute], baselines: Mapping[datetime, Baselines]
) -> list[tuple[datetime, str]]:
    """Every minute and signal judged unusual."""
    found = []
    for at, normal in baselines.items():
        window = Window(tuple(series_window(history, at, NORMAL.window_minutes)))
        for name, judged in (
            ("traffic", judge_traffic(window, normal.traffic, NORMAL)),
            ("failing", judge_failing(window, normal.failing, NORMAL)),
            ("slower", judge_slower(window, normal.slower, NORMAL)),
        ):
            if judged.verdict == "unusual":
                found.append((at, name))
    return found


def test_a_business_service_s_quiet_weekends_open_nothing_and_traffic_learns_to_day_21() -> None:
    """AC3, FR5, FR6: weekend traffic at 15% of a weekday's. A Wednesday start makes
    day 10 and day 17 Saturdays."""
    first_seen = datetime(2026, 9, 2, tzinfo=UTC)
    history = series(first_seen, 18 * 1440, daily(UTC_ZONE, peak=200, weekend=0.15), seed=11)

    for day in (10, 17):
        saturday = first_seen + timedelta(days=day)
        assert saturday.weekday() == 5
        minutes = list(span(saturday + timedelta(hours=6), saturday + timedelta(hours=20)))[::10]
        baselines = _baselines(history, minutes, first_seen)

        assert _unusual(history, baselines) == []
        # Day 10 is the second weekend day, still learning; day 17 is judged for
        # everything but traffic, which says when its judging starts: day 21.
        traffic = {each.traffic for each in baselines.values()}
        mode = "learning" if day == 10 else "daily"
        assert {(t.mode, t.learning_until) for t in traffic} == {(mode, date(2026, 9, 23))}


def test_a_batch_spike_that_wanders_three_minutes_a_day_opens_nothing() -> None:
    """AC7, FR4: a one-minute batch, three times the traffic and twice as slow, at
    09:00 give or take 3 minutes each day; the normal pools ±15 minutes around it."""
    first_seen = datetime(2026, 9, 1, tzinfo=UTC)
    rng = random.Random(13)
    history = series(first_seen, 30 * 1440, lambda _: 60.0, seed=13)
    for day in range(30):
        at = first_seen + timedelta(days=day, hours=9, minutes=rng.randint(-3, 3))
        history[at] = series_minute(180, 2, slower_by=4)
    judged_day = first_seen + timedelta(days=29)
    history[judged_day + timedelta(hours=9, minutes=3)] = series_minute(180, 2, slower_by=4)
    minutes = list(
        span(
            judged_day + timedelta(hours=8, minutes=40), judged_day + timedelta(hours=9, minutes=30)
        )
    )

    assert _unusual(history, _baselines(history, minutes, first_seen)) == []


def test_monday_at_its_own_level_is_normal_and_at_sunday_s_is_losing_traffic() -> None:
    """AC10: three weeks of history, the weekly pattern compares Mondays with Mondays."""
    first_seen = datetime(2026, 9, 6, tzinfo=UTC)  # a Sunday
    rate = daily(UTC_ZONE, peak=200, weekend=0.1)
    monday = datetime(2026, 9, 28, tzinfo=UTC)
    history = series(
        first_seen, (monday - first_seen) // timedelta(minutes=1) + 1440, rate, seed=17
    )
    minutes = list(span(monday + timedelta(hours=9), monday + timedelta(hours=9, minutes=20)))
    baselines = _baselines(history, minutes, first_seen)
    assert {each.traffic.mode for each in baselines.values()} == {"weekly"}

    assert _unusual(history, baselines) == []
    sunday_level = dict(history)
    rng = random.Random(19)
    for at in span(monday + timedelta(hours=8, minutes=50), minutes[-1]):
        sunday_level[at] = series_minute(poisson(rng, rate(at) * 0.1))
    found = {name for _, name in _unusual(sunday_level, baselines)}
    assert found == {"traffic"}


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
