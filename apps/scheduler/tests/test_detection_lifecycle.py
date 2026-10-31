"""A finding's life, minute by minute, without a store (plan T3.3; design, "The
statistical method" and "Findings and their state").

One finding per service listing every signal and endpoint (BR2, AC18), closing on
recovery under the current sensitivity (AC30), after 6 hours unjudged (D20), or
as *service gone* after 24 hours of silence (FR32, AC41); severity that only rises
(FR14, AC46); the project finding (FR15, AC21); titles (FR17); and what the
project job records for the normal to leave out (BR3). The same rules against
Postgres and ClickHouse are in `test_detection_project_job_live.py`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from detection_series import minute as series_minute

from nexovue_scheduler.detection import project_job
from nexovue_scheduler.detection.lifecycle import (
    GONE_AFTER,
    UNJUDGED_CLOSE_MINUTES,
    Change,
    Context,
    MinuteJudged,
    Normal,
    OpenFinding,
    TrafficSnapshot,
    percent,
    project_rule,
    project_title,
    step,
    title,
    traffic_waits,
)
from nexovue_scheduler.detection.method import (
    RECOVERY_MINUTES,
    SENSITIVITIES,
    EndpointJudgement,
    FailingJudgement,
    SlowerBaseline,
    SlowerJudgement,
    TrafficJudgement,
    Window,
    bucket_p95,
    judge_slower,
    threshold_share,
)

UTC_ZONE: Final = ZoneInfo("UTC")
KOLKATA: Final = ZoneInfo("Asia/Kolkata")
ONSET: Final = datetime(2026, 11, 4, 14, 0, tzinfo=UTC)
MINUTE: Final = timedelta(minutes=1)
WIDTH: Final = 5
WEEKLY: Final = Normal("weekly", 4)


# ------------------------------------------------------------------ builders


def _traffic(
    verdict: str = "normal",
    *,
    requests: int = 500,
    expected: float | None = 500.0,
    critical: bool = False,
    clears: bool | None = None,
) -> TrafficJudgement:
    return TrafficJudgement(
        verdict,  # type: ignore[arg-type]
        critical,
        requests,
        expected,
        0.0,
        clears=verdict == "normal" if clears is None else clears,
        stop=requests == 0 and verdict == "unusual",
    )


def _endpoint(
    name: str, verdict: str = "normal", *, failures: int = 1, critical: bool = False
) -> EndpointJudgement:
    return EndpointJudgement(name, verdict, critical, 100, failures, 0.01, None)  # type: ignore[arg-type]


def _failing(
    verdict: str = "normal",
    *,
    failures: int = 5,
    critical: bool = False,
    endpoints: Sequence[EndpointJudgement] = (),
) -> FailingJudgement:
    return FailingJudgement(
        verdict,  # type: ignore[arg-type]
        critical,
        500,
        failures,
        0.01,
        0.0,
        None,
        verdict == "unusual",
        endpoints=tuple(endpoints),
    )


def _slower(
    verdict: str = "normal", *, p95: float = 100.0, critical: bool = False
) -> SlowerJudgement:
    return SlowerJudgement(verdict, critical, 500, p95, 10, p95 / 100.0, None)  # type: ignore[arg-type]


def _at(
    minute: datetime,
    *,
    traffic: TrafficJudgement | None = None,
    failing: FailingJudgement | None = None,
    slower: SlowerJudgement | None = None,
    requests: int = 100,
) -> MinuteJudged:
    return MinuteJudged(
        minute=minute,
        width=WIDTH,
        requests=requests,
        traffic=traffic or _traffic(),
        failing=failing or _failing(),
        slower=slower or _slower(),
        traffic_normal=WEEKLY,
        failing_normal=WEEKLY,
        slower_normal=WEEKLY,
        slower_normal_ms=100.0,
    )


@dataclass
class Run:
    """A service's finding over a run of minutes, with every change it made."""

    finding: OpenFinding | None = None
    opened: int = 0
    closed: list[str] | None = None
    joined: list[str] | None = None
    raised: int = 0
    last_request: datetime | None = None

    def feed(self, minutes: Sequence[MinuteJudged], context: Context | None = None) -> None:
        self.closed = self.closed or []
        self.joined = self.joined or []
        for judged in minutes:
            if judged.requests > 0:
                self.last_request = judged.minute
            given = context or Context()
            given = Context(given.traffic_absorbed, given.traffic_waits, self.last_request)
            finding, change = step(self.finding, judged, given, zone=UTC_ZONE)
            self._count(change)
            self.finding = None if change.closed else finding

    def _count(self, change: Change) -> None:
        assert self.closed is not None and self.joined is not None
        self.opened += change.opened
        self.raised += change.raised
        self.joined.extend(change.joined)
        if change.closed:
            self.closed.append(change.closed)


def _minutes(start: datetime, count: int) -> Iterator[datetime]:
    return (start + index * MINUTE for index in range(count))


# --------------------------------------------------------- one record (BR2)


def test_two_signals_and_three_endpoints_over_two_hours_are_one_finding() -> None:
    """AC18, BR2: slower and failing more, with three endpoints failing, for two hours,
    is one finding whose worst values follow the worst window; it closes once every
    key has recovered."""
    run = Run()
    endpoints = [_endpoint(name, "unusual", failures=40) for name in ("a", "b", "c")]
    deviation = [
        _at(
            minute,
            failing=_failing(
                "unusual", failures=30 + index // 10, endpoints=endpoints[: 1 + index // 40]
            ),
            slower=_slower("unusual", p95=200.0 + index),
        )
        for index, minute in enumerate(_minutes(ONSET, 120))
    ]
    recovered = [_endpoint(name) for name in ("a", "b", "c")]
    recovery = [
        _at(minute, failing=_failing(endpoints=recovered))
        for minute in _minutes(ONSET + 120 * MINUTE, RECOVERY_MINUTES)
    ]

    run.feed(deviation)
    finding = run.finding
    assert finding is not None
    assert run.opened == 1 and run.closed == []
    assert set(finding.keys) == {"failing", "slower", "endpoint:a", "endpoint:b", "endpoint:c"}
    assert run.joined == ["endpoint:b", "endpoint:c"]
    assert finding.started_at == ONSET - (WIDTH - 1) * MINUTE
    assert finding.keys["slower"].worst == 319.0
    assert finding.keys["failing"].worst == pytest.approx(100 * 41 / 500)
    signals = finding.signals()
    assert [s["endpoint"] for s in signals if s["signal"] == "failing"].count(None) <= 1
    assert {s["endpoint"] for s in signals} >= {"a", "b", "c"}

    run.feed(recovery)
    assert run.opened == 1 and run.closed == ["recovered"]


def test_a_deviation_that_returns_before_recovery_continues_the_same_finding() -> None:
    """FR13: nine normal minutes, then unusual again, is still one finding."""
    run = Run()
    run.feed([_at(m, slower=_slower("unusual", p95=250)) for m in _minutes(ONSET, 10)])
    run.feed([_at(m) for m in _minutes(ONSET + 10 * MINUTE, RECOVERY_MINUTES - 1)])
    run.feed([_at(m, slower=_slower("unusual", p95=250)) for m in _minutes(ONSET + 19 * MINUTE, 5)])
    run.feed([_at(m) for m in _minutes(ONSET + 24 * MINUTE, RECOVERY_MINUTES)])

    assert run.opened == 1
    assert run.closed == ["recovered"]


def test_minutes_that_cannot_be_judged_pause_recovery_without_resetting_it() -> None:
    """FR11, FR13, AC5: too little traffic neither closes nor extends a finding."""
    run = Run()
    run.feed([_at(m, slower=_slower("unusual", p95=250)) for m in _minutes(ONSET, 5)])
    quiet = _slower("too_little_traffic")
    pattern = ["normal"] * 6 + ["quiet"] * 30 + ["normal"] * (RECOVERY_MINUTES - 6)
    for index, kind in enumerate(pattern):
        assert run.finding is not None, f"closed early at {index}"
        run.feed([_at(ONSET + (5 + index) * MINUTE, slower=quiet if kind == "quiet" else None)])

    assert run.closed == ["recovered"]


# ----------------------------------------------------------- severity (FR14)


def test_severity_rises_and_never_falls() -> None:
    run = Run()
    run.feed([_at(ONSET, slower=_slower("unusual", p95=200))])
    run.feed([_at(ONSET + MINUTE, slower=_slower("unusual", p95=500, critical=True))])
    run.feed([_at(m, slower=_slower("unusual", p95=200)) for m in _minutes(ONSET + 2 * MINUTE, 5)])

    assert run.finding is not None
    assert run.finding.severity == "critical"
    assert run.raised == 1


def test_an_endpoint_at_its_critical_share_makes_its_finding_critical() -> None:
    """AC46, D27, as the finding sees it."""
    run = Run()
    pay = _endpoint("POST /pay", "unusual", failures=25, critical=True)

    run.feed([_at(ONSET, failing=_failing("elevated", endpoints=[pay]))])

    assert run.finding is not None
    assert run.finding.severity == "critical"
    assert list(run.finding.keys) == ["endpoint:POST /pay"]


# --------------------------------------------------- closing under recovery


def _steady_slower(sensitivity_name: str) -> SlowerBaseline:
    sensitivity = SENSITIVITIES[sensitivity_name]
    pool = [count * 4 * 31 * sensitivity.window_minutes for count in series_minute(100).buckets]
    index, share = threshold_share(pool)
    return SlowerBaseline(
        "weekly", 4, sensitivity.window_minutes, sum(pool), index, share, 0.002, bucket_p95(pool)
    )


def test_a_finding_closes_under_a_lower_sensitivity_while_the_rise_continues() -> None:
    """AC30, FR18: about 1.7 times slower is past normal's minimum of 1.5 and short of
    low's 2. Opened at normal, it closes under low after *recovery*, the rise
    unchanged, because its window no longer passes low's minimum."""
    rise = series_minute(100, slower_by=3)
    normal, low = SENSITIVITIES["normal"], SENSITIVITIES["low"]
    at_normal = judge_slower(Window((rise,) * 5), _steady_slower("normal"), normal)
    at_low = judge_slower(Window((rise,) * 10), _steady_slower("low"), low)
    assert at_normal.verdict == "unusual"
    assert at_low.verdict == "normal"
    assert at_low.tail is not None and at_low.tail < 0.05, "still far from its normal"

    run = Run()
    run.feed([_at(ONSET, slower=at_normal)])
    run.feed([_at(m, slower=at_low) for m in _minutes(ONSET + MINUTE, RECOVERY_MINUTES)])

    assert run.opened == 1
    assert run.closed == ["recovered"]


# ------------------------------------------------------------- D20 and FR32


def test_a_finding_no_longer_judged_for_six_hours_closes_as_such() -> None:
    """D20, FR13: one minute short of 6 hours it is still open."""
    run = Run()
    run.feed([_at(ONSET, slower=_slower("unusual", p95=250))])
    quiet = _slower("too_little_traffic")
    run.feed([_at(m, slower=quiet) for m in _minutes(ONSET + MINUTE, UNJUDGED_CLOSE_MINUTES - 1)])
    assert run.finding is not None

    run.feed([_at(ONSET + UNJUDGED_CLOSE_MINUTES * MINUTE, slower=quiet)])

    assert run.closed == ["could_not_be_judged"]


def test_one_signal_recovered_and_one_unjudged_closes_as_no_longer_judged() -> None:
    """D20's mixed case: failing recovers, slower goes 6 hours without a judgeable
    minute; once both are done the finding closes, and says it could not be judged."""
    run = Run()
    run.feed(
        [_at(ONSET, failing=_failing("unusual", failures=40), slower=_slower("unusual", p95=250))]
    )
    quiet = _slower("too_little_traffic")
    run.feed([_at(m, slower=quiet) for m in _minutes(ONSET + MINUTE, UNJUDGED_CLOSE_MINUTES)])

    assert run.closed == ["could_not_be_judged"]
    # Both recovered is *recovered*.
    both = Run()
    both.feed(
        [_at(ONSET, failing=_failing("unusual", failures=40), slower=_slower("unusual", p95=250))]
    )
    both.feed([_at(m) for m in _minutes(ONSET + MINUTE, RECOVERY_MINUTES)])
    assert both.closed == ["recovered"]


def test_a_service_that_stops_is_critical_and_closes_as_gone_after_a_day() -> None:
    """AC41, FR32: a stop opens a critical finding. Silent nights that the normal
    expects little of hold it open rather than recover it or let D20 close it; after
    24 hours of silence it closes as *service gone*."""
    run = Run()
    run.feed([_at(m) for m in _minutes(ONSET - 10 * MINUTE, 10)])
    stop = _traffic("unusual", requests=0, expected=10.0, critical=True)
    run.feed([_at(ONSET, traffic=stop, requests=0)])
    assert run.finding is not None and run.finding.severity == "critical"
    night = _traffic("too_little_traffic", requests=0, expected=1.0, clears=False)
    silent = GONE_AFTER // MINUTE
    run.feed(
        [
            _at(m, traffic=night, requests=0, failing=_failing("too_little_traffic"))
            for m in _minutes(ONSET + MINUTE, silent - 2)
        ]
    )
    assert run.finding is not None, "D20 must not close a stopped service"

    run.feed([_at(ONSET + silent * MINUTE, traffic=night, requests=0)])

    assert run.closed == ["service_gone"]
    assert run.opened == 1


def test_traffic_coming_back_recovers_a_stop() -> None:
    run = Run()
    run.feed([_at(ONSET, traffic=_traffic("unusual", requests=0, critical=True), requests=0)])
    run.feed([_at(m) for m in _minutes(ONSET + MINUTE, RECOVERY_MINUTES)])

    assert run.closed == ["recovered"]


# ------------------------------------------------------------- FR15, AC21


def _snapshot(
    *, unusual: bool, since: datetime | None = ONSET, clear: int = 0, judged: bool = True
) -> TrafficSnapshot:
    return TrafficSnapshot(
        judged=judged,
        unusual=unusual,
        unusual_since=since if unusual else None,
        clear_minutes=clear,
        in_own_finding=False,
        last_request=ONSET - MINUTE,
        expected_rpm=100.0,
        current_rpm=0.0 if unusual else 100.0,
    )


def test_nine_of_eleven_services_stopping_together_is_one_project_finding() -> None:
    """AC21, FR15."""
    snapshots = {f"svc{i}": _snapshot(unusual=i < 9) for i in range(11)}

    decision = project_rule(snapshots, involved=None, minute=ONSET + 4 * MINUTE)

    assert decision.open
    assert decision.involved == frozenset(f"svc{i}" for i in range(9))
    assert decision.judged == 11
    assert project_title(9, 11, ONSET, KOLKATA) == (
        "Traffic stopped arriving from 9 of 11 services at 19:30 IST. "
        "Check your collector or network"
    )


@pytest.mark.parametrize(
    ("unusual", "total"),
    [(2, 2), (3, 5)],
    ids=["fewer than three", "under three quarters"],
)
def test_too_few_services_together_is_no_project_finding(unusual: int, total: int) -> None:
    snapshots = {f"svc{i}": _snapshot(unusual=i < unusual) for i in range(total)}

    assert not project_rule(snapshots, involved=None, minute=ONSET).open


def test_services_that_stopped_minutes_apart_are_not_one_problem() -> None:
    """Within 5 minutes of each other, the project rule; further apart, each its own."""
    snapshots = {
        "a": _snapshot(unusual=True, since=ONSET - 10 * MINUTE),
        "b": _snapshot(unusual=True),
        "c": _snapshot(unusual=True),
    }

    assert not project_rule(snapshots, involved=None, minute=ONSET).open


def test_a_project_finding_closes_once_fewer_than_half_are_still_unusual() -> None:
    involved = frozenset({"a", "b", "c", "d"})
    still = {name: _snapshot(unusual=False, clear=3) for name in involved}
    recovered = {name: _snapshot(unusual=False, clear=RECOVERY_MINUTES) for name in involved}
    recovered["a"] = _snapshot(unusual=True)

    assert project_rule(still, involved=involved, minute=ONSET).close is None
    assert project_rule(recovered, involved=involved, minute=ONSET).close == "recovered"


def test_a_project_whose_services_stay_silent_for_a_day_closes_as_gone() -> None:
    involved = frozenset({"a", "b", "c"})
    silent = {name: _snapshot(unusual=True) for name in involved}

    decision = project_rule(silent, involved=involved, minute=ONSET + GONE_AFTER)

    assert decision.close == "service_gone"


def test_a_service_s_traffic_waits_for_the_project_rule_and_then_opens() -> None:
    """FR15: while 2 or more services are pending, one waits up to 3 minutes; a
    service alone does not wait. A service the project finding holds never opens
    its own for traffic."""
    assert traffic_waits(ONSET, ONSET + 2 * MINUTE, pending=2)
    assert not traffic_waits(ONSET, ONSET + 3 * MINUTE, pending=2)
    assert not traffic_waits(ONSET, ONSET, pending=1)
    stop = _traffic("unusual", requests=0, critical=True)
    held = Run()
    held.feed([_at(m, traffic=stop, requests=0) for m in _minutes(ONSET, 30)], Context(True))
    waiting = Run()
    waiting.feed([_at(ONSET, traffic=stop, requests=0)], Context(traffic_waits=True))

    assert held.opened == waiting.opened == 0


# ------------------------------------------------------------- titles (FR17)


def test_the_worst_signal_leads_the_title_and_the_others_follow() -> None:
    """FR17's example: two rates, never a multiple, in the project's zone."""
    finding = OpenFinding(severity="warning", started_at=ONSET + 10 * MINUTE, keys={})
    run = Run()
    run.feed(
        [
            _at(
                ONSET + 10 * MINUTE,
                failing=FailingJudgement("unusual", False, 1000, 32, 0.004, 0.0, None, True),
                slower=_slower("unusual", p95=130),
            )
        ]
    )
    assert run.finding is not None
    finding = run.finding
    finding.started_at = datetime(2026, 11, 4, 8, 40, tzinfo=UTC)

    assert title("Checkout", finding, KOLKATA) == (
        "Checkout is failing 3.2% of requests, against 0.4% normally, since 14:10 IST, "
        "and is slower"
    )


@pytest.mark.parametrize(
    ("value", "written"),
    [(3.2, "3.2%"), (0.4, "0.4%"), (0.04, "0.04%"), (25.0, "25%"), (1.0, "1%"), (0.0, "0%")],
)
def test_a_rate_is_written_as_a_rate(value: float, written: str) -> None:
    assert percent(value) == written


def test_a_stop_and_an_endpoint_read_plainly() -> None:
    stopped = Run()
    stopped.feed([_at(ONSET, traffic=_traffic("unusual", requests=0, critical=True), requests=0)])
    endpoint = Run()
    pay = _endpoint("POST /pay", "unusual", failures=100, critical=True)
    endpoint.feed([_at(ONSET, failing=_failing("elevated", endpoints=[pay]))])
    assert stopped.finding is not None and endpoint.finding is not None

    assert title("frontend", stopped.finding, UTC_ZONE).startswith(
        "frontend stopped receiving requests at 13:56 UTC"
    )
    assert title("checkout", endpoint.finding, UTC_ZONE) == (
        "checkout is failing 100% of POST /pay requests, against 1% normally, since 13:56 UTC"
    )


# ------------------------------------------------------- what the job keeps


@dataclass(frozen=True)
class _Row:
    id: uuid.UUID
    severity: str
    muted: bool
    signals: list[dict[str, Any]]
    started_at: datetime


def test_an_open_finding_is_restored_with_its_counts() -> None:
    """The job keeps each key's counts in the service's state, in `signals`' order;
    a finding whose keys the state lost is read by signal."""
    run = Run()
    pay = _endpoint("POST /pay", "unusual", failures=50)
    run.feed([_at(ONSET, failing=_failing("unusual", failures=40, endpoints=[pay]))])
    calm = _failing(endpoints=[_endpoint("POST /pay")])
    run.feed([_at(m, failing=calm) for m in _minutes(ONSET + MINUTE, 4)])
    assert run.finding is not None
    finding_id = uuid.uuid4()
    row = _Row(finding_id, "warning", False, json.loads(json.dumps(run.finding.signals())), ONSET)
    tracked = {
        "id": str(finding_id),
        "keys": [
            [k, run.finding.keys[k].clear, run.finding.keys[k].unjudged]
            for k in run.finding.ordered()
        ],
    }

    restored = project_job._restore(row, tracked)
    unknown = project_job._restore(row, {})

    assert {k: (r.clear, r.unjudged) for k, r in restored.keys.items()} == {
        "failing": (4, 0),
        "endpoint:POST /pay": (4, 0),
    }
    assert set(unknown.keys) == set(restored.keys)


def test_snoozed_and_shadowed_minutes_are_recorded_merged_and_forgotten() -> None:
    """BR3, D25, FR6: the minutes the baseline job must leave out, kept as ranges in the
    service's state, and dropped once no pool reaches them."""
    old = ONSET - timedelta(days=40)
    stored: dict[str, Any] = {
        "excluded": {"snoozed": [[old.isoformat(), (old + MINUTE).isoformat()]]}
    }
    runs = [
        project_job.Judged(
            minute=_at(
                m, traffic=TrafficJudgement("learning", False, 100, 500.0, -5.0, shadow=True)
            ),
            normals=project_job.Normals(),
            endpoint_counts={},
            shadow=True,
        )
        for m in _minutes(ONSET, 2)
    ]

    kept = project_job._record_exclusions(stored, runs, snoozed=True, now=ONSET)

    assert kept["excluded"]["snoozed"] == [[ONSET.isoformat(), (ONSET + MINUTE).isoformat()]]
    assert kept["excluded"]["shadow"] == [
        [(ONSET - (WIDTH - 1) * MINUTE).isoformat(), (ONSET + MINUTE).isoformat()]
    ]
    assert "excluded" not in project_job._record_exclusions({}, runs[:0], snoozed=False, now=ONSET)


def test_each_endpoint_s_excursions_are_recorded_as_judged_and_forgotten_after_28_days() -> None:
    """D18 per endpoint: a reading keeps no endpoint's normal, so each run above the
    base test is recorded when it ends, as its first and last minutes."""
    above = FailingJudgement(
        "elevated",
        False,
        500,
        9,
        0.01,
        0.0,
        None,
        False,
        endpoints=(
            EndpointJudgement("POST /pay", "elevated", False, 100, 9, 0.01, None, above=True),
        ),
    )
    records: dict[str, dict[str, Any]] = {}
    for minute in _minutes(ONSET, 3):
        records = project_job.advance_endpoint_runs(records, above, minute)
    assert records == {"POST /pay": {"run": 3, "runs": []}}

    records = project_job.advance_endpoint_runs(records, _failing(), ONSET + 3 * MINUTE)

    assert records == {
        "POST /pay": {"run": 0, "runs": [[ONSET.isoformat(), (ONSET + 2 * MINUTE).isoformat()]]}
    }
    assert project_job._forget_old_runs(records, ONSET + timedelta(days=28)) == records
    assert project_job._forget_old_runs(records, ONSET + timedelta(days=30)) == {}
