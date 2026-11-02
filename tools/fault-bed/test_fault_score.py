"""AC36's scoring over a recorded run built by hand: a caught fault, a missed one, a gap, and a
false alarm in the reference run. No demo and no API: scoring reads only the run log."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fault_model import (
    REFERENCE,
    FindingSeen,
    Injection,
    Observation,
    Plan,
    RecordedRun,
    ServiceWindow,
    append,
    events,
    findings_event,
    load,
    observation_event,
    opened_at,
    plan_event,
    reference_services_event,
)
from fault_score import CLEARLY_PAST, NOT_PAST, PAST, how_far_past, render, score, window_moves

REFERENCE_START = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
SLOT = timedelta(minutes=90)
ACTIVE = timedelta(minutes=30)
FAULTS = ("paymentFailure", "cartFailure", "failedReadinessProbe")
CALM = {
    "payment": ServiceWindow("payment", 100, 0, 50.0),
    "cart": ServiceWindow("cart", 300, 0, 20.0),
    "frontend": ServiceWindow("frontend", 1500, 3, 120.0),
}


def uuid7(at: datetime) -> str:
    """A UUIDv7 stamped `at`, as Postgres's `uuidv7()` stamps a finding's row."""
    millis = int(at.timestamp() * 1000)
    value = (millis << 80) | (0x7 << 76) | (0b10 << 62) | 0x1234
    return str(uuid.UUID(int=value))


def finding(at: datetime, service: str, signal: str = "failing") -> FindingSeen:
    return FindingSeen(uuid7(at), at, "service", (service,), (signal,), "warning", "recovered")


def plan() -> Plan:
    injections = []
    at = REFERENCE_START + REFERENCE
    for round_number in range(1, 6):
        for name in FAULTS:
            injections.append(Injection(name, round_number, at, at + ACTIVE))
            at += SLOT
    resting = dict.fromkeys(FAULTS, "off")
    return Plan("project", "3.0.0", REFERENCE_START, resting, tuple(injections))


def during(**changed: ServiceWindow) -> tuple[ServiceWindow, ...]:
    return tuple({**CALM, **changed}.values())


def observation(fault: str, round_number: int) -> Observation:
    if fault == "paymentFailure":
        moved = during(payment=ServiceWindow("payment", 100, 12, 55.0))
    elif fault == "cartFailure":
        moved = during(cart=ServiceWindow("cart", 300, 30, 21.0))
    else:  # failedReadinessProbe moves nothing: compose has no readiness probes.
        moved = during()
    return Observation(fault, round_number, moved, tuple(CALM.values()))


def recorded(late_rounds: frozenset[int] = frozenset({5})) -> RecordedRun:
    the_plan = plan()
    starts = {(i.fault, i.round): i.start for i in the_plan.injections}
    findings = [finding(REFERENCE_START + timedelta(days=2, hours=3), "frontend", "slower")]
    for round_number in range(1, 6):
        # payment is caught every round: in 8 minutes, or 25 (late) in the late rounds.
        taken = timedelta(minutes=25 if round_number in late_rounds else 8)
        findings.append(finding(starts[("paymentFailure", round_number)] + taken, "payment"))
    # A finding on a service the fault did not move catches nothing.
    findings.append(finding(starts[("cartFailure", 1)] + timedelta(minutes=5), "frontend"))
    return RecordedRun(
        plan=the_plan,
        observations=tuple(observation(f, r) for r in range(1, 6) for f in FAULTS),
        skipped=(),
        findings=tuple(sorted(findings, key=lambda f: f.opened_at)),
        reference_services=tuple(sorted(CALM)),
    )


def by_fault(run: RecordedRun) -> dict[str, object]:
    return {fault.fault: fault for fault in score(run).faults}


def test_a_fault_caught_in_time_four_times_in_five_passes() -> None:
    payment = score(recorded()).faults[0]
    assert payment.fault == "paymentFailure"
    assert payment.moves == (("payment", "failing"),)
    assert payment.counted == 5
    assert payment.in_time == 4
    assert [i.time_to_catch for i in payment.injections][:2] == [timedelta(minutes=8)] * 2
    assert payment.injections[4].caught_by is not None
    assert not payment.injections[4].in_time  # 25 minutes against a quiet service's 20.
    assert payment.verdict == "pass"


def test_a_quiet_service_gets_20_minutes_and_a_busy_one_12() -> None:
    quiet = score(recorded(late_rounds=frozenset())).faults[0].injections[0]
    assert quiet.in_time and quiet.time_to_catch == timedelta(minutes=8)
    run = recorded(late_rounds=frozenset())
    the_plan = run.plan
    payment = next(i for i in the_plan.injections if i.fault == "paymentFailure" and i.round == 1)
    at = payment.start + timedelta(minutes=15)
    fifteen = (finding(at, "payment"),)
    for rpm_per_window, in_time in ((100, True), (1000, False)):
        busy = ServiceWindow("payment", rpm_per_window, 0, 50.0)
        observations = tuple(
            Observation(
                o.fault,
                o.round,
                during(payment=ServiceWindow("payment", rpm_per_window, rpm_per_window // 8, 50.0)),
                tuple({**CALM, "payment": busy}.values()),
            )
            if o.fault == "paymentFailure"
            else o
            for o in run.observations
        )
        card = score(RecordedRun(the_plan, observations, (), fifteen, run.reference_services))
        first = card.faults[0].injections[0]
        assert first.time_to_catch == timedelta(minutes=15)
        assert first.in_time is in_time  # 100 per 5 minutes is quiet; 1,000 is 200 a minute.


def test_three_in_time_of_five_fails() -> None:
    payment = score(recorded(late_rounds=frozenset({4, 5}))).faults[0]
    assert payment.in_time == 3
    assert payment.verdict == "fail: 4 needed in time"


def test_a_fault_that_moved_a_signal_but_opened_nothing_is_missed() -> None:
    cart = score(recorded()).faults[1]
    assert cart.moves == (("cart", "failing"),)
    assert cart.counted == 5
    assert all(i.caught_by is None for i in cart.injections)
    assert cart.verdict == "fail: 4 needed in time"


def test_a_flag_that_moves_nothing_is_a_gap_with_its_expected_service_and_signal() -> None:
    probe = score(recorded()).faults[2]
    assert probe.gap
    assert probe.verdict == "gap (FR27)"
    assert probe.expected == ("cart", "traffic")
    table = render(score(recorded()))
    assert "| `failedReadinessProbe` = `on` | cart · traffic | 5 |" in table


def test_a_finding_in_the_reference_run_is_a_false_alarm() -> None:
    card = score(recorded())
    assert [f.services for f in card.false_alarms] == [("frontend",)]
    assert card.false_alarm_rate == pytest.approx(1 / 3)
    assert "1 findings over 1 week(s) and 3 services: 0.33 per service per week" in render(card)


def test_a_finding_on_a_service_the_fault_did_not_move_is_unattributed() -> None:
    card = score(recorded())
    assert [f.services for f in card.unattributed] == [("frontend",)]


def test_the_table_has_a_row_per_fault() -> None:
    table = render(score(recorded()))
    assert (
        "| `paymentFailure` | `paymentFailure` = `10%` | payment · failing | 5 of 5 | 4 of 5 "
        "| 8.0 min | payment · failing | pass |"
    ) in table
    assert (
        "| `cartFailure` | `cartFailure` = `10%` | cart · failing | 5 of 5 | 0 of 5 | — | — |"
        in (table)
    )


def test_a_move_seen_in_only_two_of_five_injections_is_noise() -> None:
    run = recorded()
    noisy = tuple(
        Observation(o.fault, o.round, during(), o.reference)
        if o.fault == "paymentFailure" and o.round > 2
        else o
        for o in run.observations
    )
    payment = score(RecordedRun(run.plan, noisy, (), run.findings, run.reference_services))
    assert payment.faults[0].gap


@pytest.mark.parametrize(
    ("signal", "now", "grade"),
    [
        # Slower: the minimum is 1.5x, clearly past it is 1.55x; 200 requests needed.
        ("slower", ServiceWindow("s", 400, 0, 74.0), NOT_PAST),
        ("slower", ServiceWindow("s", 400, 0, 76.0), PAST),
        ("slower", ServiceWindow("s", 400, 0, 77.5), CLEARLY_PAST),
        ("slower", ServiceWindow("s", 150, 0, 500.0), NOT_PAST),
        # Failing: +2 points with 5 failures; clearly past is +2.2 points.
        ("failing", ServiceWindow("s", 400, 8, 50.0), PAST),
        ("failing", ServiceWindow("s", 500, 11, 50.0), CLEARLY_PAST),
        ("failing", ServiceWindow("s", 100, 4, 50.0), NOT_PAST),
        # Traffic: at or below 50% of the reference; clearly past is 45% or less (D19).
        ("traffic", ServiceWindow("s", 210, 0, 50.0), NOT_PAST),
        ("traffic", ServiceWindow("s", 190, 0, 50.0), PAST),
        ("traffic", ServiceWindow("s", 160, 0, 50.0), CLEARLY_PAST),
        ("traffic", None, CLEARLY_PAST),
    ],
)
def test_how_far_past_the_minimum(signal: str, now: ServiceWindow | None, grade: int) -> None:
    before = ServiceWindow("s", 400, 0, 50.0)
    assert how_far_past(signal, now, before) == grade  # type: ignore[arg-type]


def test_traffic_at_40_percent_is_clearly_past_and_too_little_traffic_is_not_judged() -> None:
    reference = (ServiceWindow("frontend-proxy", 500, 0, 30.0), ServiceWindow("quiet", 40, 0, 9.0))
    lowered = (ServiceWindow("frontend-proxy", 200, 0, 30.0), ServiceWindow("quiet", 10, 0, 9.0))
    moves = window_moves(Observation("trafficAt40", 1, lowered, reference))
    assert moves == {("frontend-proxy", "traffic"): CLEARLY_PAST}


def test_a_finding_opened_at_is_its_uuid7_time() -> None:
    at = datetime(2026, 11, 9, 14, 2, 31, 250000, tzinfo=UTC)
    assert opened_at(uuid7(at)) == at
    with pytest.raises(ValueError, match="UUIDv7"):
        opened_at(str(uuid.uuid4()))


def test_a_run_log_round_trips_and_keeps_each_findings_latest_state(tmp_path: Path) -> None:
    run = recorded()
    log = tmp_path / "run.jsonl"
    append(log, plan_event(run.plan))
    for o in run.observations:
        append(log, observation_event(o))
    still_open = [
        FindingSeen(f.id, f.opened_at, f.scope, f.services, f.signals, f.severity, None)
        for f in run.findings
    ]
    append(log, findings_event(REFERENCE_START, still_open))
    append(log, findings_event(REFERENCE_START, run.findings))
    append(log, reference_services_event(CALM))
    assert load(events(log)) == run


def test_an_injection_recorded_twice_is_refused(tmp_path: Path) -> None:
    run = recorded()
    log = tmp_path / "run.jsonl"
    append(log, plan_event(run.plan))
    append(log, observation_event(run.observations[0]))
    append(log, observation_event(run.observations[0]))
    with pytest.raises(ValueError, match="recorded twice"):
        load(events(log))
