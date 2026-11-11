"""F19's arithmetic and states, without a store (plan T1.1).

AC1's allowance, AC51's combinations, AC89's words by kind, AC110's order, AC111's
exact and missed cases, and the bucket boundaries' parity with the worker's Go
(AC3's parity half), read the way `tools/detection-seed/test_seed.py` reads them.
"""

from __future__ import annotations

import importlib.util
import math
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from nexovue_platform import slo
from nexovue_platform.slo import Burn, DayCounts, Facts, State

SERVICES_GO = (
    Path(__file__).resolve().parents[3] / "apps/workers/internal/pipeline/derive/services.go"
)
MIGRATION = next((Path(__file__).resolve().parents[1] / "migrations/versions").glob("0052_*.py"))
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
KOLKATA = ZoneInfo("Asia/Kolkata")
UTC_ZONE = ZoneInfo("UTC")


def _facts(**changes: object) -> Facts:
    """A fresh, enabled, healthy 28-day availability target: On track."""
    base = Facts(
        kind=slo.SERVICE_AVAILABILITY,
        window_days=28,
        enabled=True,
        now=NOW,
        evaluated_minute=NOW - timedelta(minutes=1),
        expected_since=NOW - timedelta(days=30),
        counting=False,
        source_gone=False,
        burn=None,
        coverage=0.99,
        last_event_minute=NOW - timedelta(minutes=1),
        days_held=28,
        allowed=1_000,
        failures=400,
        alerting=None,
        snoozed=False,
        longest_quiet_7d_minutes=0,
        traffic_finding_open=False,
        check_interval_seconds=None,
    )
    return base.replace(**changes)


FAST = Burn(severity="critical", rate=14.0, since=NOW - timedelta(hours=1), hours_left=48.0)
SLOW = Burn(severity="warning", rate=3.0, since=NOW - timedelta(hours=5), hours_left=200.0)


# ------------------------------------------------------------------ arithmetic


def test_ac1_the_allowance_is_what_the_objective_allows_less_the_failures() -> None:
    allowed = slo.allowed_failures(Decimal("99.9"), 1_000_000)
    assert allowed == 1_000
    assert slo.allowance_left(Decimal("99.9"), 1_000_000, 400) == 600


def test_the_allowance_rounds_down_and_is_never_shown_below_zero() -> None:
    assert slo.allowed_failures(Decimal("99.9"), 1_999) == 1
    assert slo.allowance_left(Decimal("99.9"), 1_000, 5) == -4
    assert slo.shown_allowance(-4) == 0


def test_ac1_a_7_day_window_adds_only_the_days_it_holds() -> None:
    today = date(2026, 11, 7)
    first = datetime(2026, 10, 1, tzinfo=UTC)
    rows = {
        today - timedelta(days=offset): DayCounts(good=900, total=1_000, lost_minutes=0)
        for offset in range(7)
        if offset != 3
    }
    rows[today - timedelta(days=9)] = DayCounts(good=0, total=50_000, lost_minutes=0)
    figures = slo.window_figures(rows, window_days=7, first_event_at=first, now=NOW, zone=UTC_ZONE)
    assert (figures.good, figures.total, figures.failures) == (5_400, 6_000, 600)
    assert figures.days_held == 6


def test_fr9_a_window_never_reaches_before_the_first_event() -> None:
    today = date(2026, 11, 7)
    assert slo.window_days_of(today, 28, first_event_day=None)[0] == today - timedelta(days=27)
    assert slo.window_days_of(today, 7, first_event_day=today - timedelta(days=2)) == [
        today - timedelta(days=2),
        today - timedelta(days=1),
        today,
    ]


def test_fr11_lost_minutes_lower_coverage_from_the_first_event() -> None:
    first = NOW - timedelta(hours=10)
    rows = {NOW.date(): DayCounts(good=10, total=10, lost_minutes=60)}
    figures = slo.window_figures(rows, window_days=28, first_event_at=first, now=NOW, zone=UTC_ZONE)
    assert figures.coverage == pytest.approx(1 - 60 / 600)


@pytest.mark.parametrize(
    ("objective", "refused"),
    [
        (Decimal("99.999"), True),
        (Decimal("89.9"), True),
        (Decimal("100"), True),
        (Decimal("90"), False),
        (Decimal("99.99"), False),
        (Decimal("99.5"), False),
    ],
)
def test_fr2_an_objective_outside_the_range_or_too_fine_is_refused(
    objective: Decimal, refused: bool
) -> None:
    assert (slo.objective_refusal(objective) is not None) is refused


def test_a_figure_rounds_towards_failing() -> None:
    assert slo.figure_percent(99_899, 100_000) == Decimal("99.89")
    assert slo.figure_percent(999, 1_000) == Decimal("99.90")
    assert slo.figure_percent(0, 0) is None


# ----------------------------------------------------------------------- words


@pytest.mark.parametrize(
    ("kind", "words", "never"),
    [
        (slo.SERVICE_AVAILABILITY, "Failures still allowed", "slow"),
        (slo.ENDPOINT_AVAILABILITY, "Failures still allowed", "runs"),
        (slo.SERVICE_LATENCY, "Slow requests still allowed", "failures"),
        (slo.CHECK_AVAILABILITY, "Failed runs still allowed", "requests"),
    ],
)
def test_ac89_each_kind_has_its_own_allowance_words(kind: str, words: str, never: str) -> None:
    assert slo.allowance_words(kind) == words
    everything = " ".join(
        (slo.allowance_words(kind), slo.failure_noun(kind), slo.event_noun(kind))
    ).lower()
    assert never not in everything


def test_the_label_is_composed_and_two_targets_on_a_service_differ() -> None:
    availability = slo.subject_label(
        slo.SERVICE_AVAILABILITY,
        service_name="checkout",
        objective=Decimal("99.90"),
        window_days=28,
    )
    endpoint = slo.subject_label(
        slo.ENDPOINT_AVAILABILITY,
        service_name="checkout",
        endpoint_name="/pay",
        objective=Decimal("99.9"),
        window_days=28,
    )
    latency = slo.subject_label(
        slo.SERVICE_LATENCY,
        service_name="checkout",
        boundary_index=slo.boundary_at_or_under(500.0),
        objective=Decimal("95"),
        window_days=28,
    )
    check = slo.subject_label(
        slo.CHECK_AVAILABILITY, check_name="Payment check", objective=Decimal("99"), window_days=7
    )
    assert availability == "checkout availability, 99.9% over 28 days"
    assert endpoint == "checkout /pay availability, 99.9% over 28 days"
    assert latency == "checkout latency under 487.2 ms, 95% over 28 days"
    assert check == "Payment check availability, 99% over 7 days"


def test_the_definition_key_ignores_the_name_and_tells_definitions_apart() -> None:
    one = slo.definition_key(
        slo.SERVICE_AVAILABILITY, service_name="checkout", objective=Decimal("99.9"), window_days=28
    )
    same = slo.definition_key(
        slo.SERVICE_AVAILABILITY,
        service_name="checkout",
        objective=Decimal("99.90"),
        window_days=28,
    )
    other = slo.definition_key(
        slo.SERVICE_AVAILABILITY, service_name="checkout", objective=Decimal("99.9"), window_days=7
    )
    assert one == same != other


# --------------------------------------------------------------------- buckets


def _go_constant(source: str, name: str) -> float:
    found = re.search(rf"\b{name}\s*=\s*([\d_.]+)", source)
    assert found, f"{name} is no longer a literal in services.go"
    return float(found.group(1).replace("_", ""))


def test_ac3_the_boundaries_are_the_workers() -> None:
    source = SERVICES_GO.read_text()
    count = int(_go_constant(source, "LatencyBucketCount"))
    floor = _go_constant(source, "latencyFloorMS")
    ceiling = _go_constant(source, "latencyCeilingMS")
    assert "math.Pow(latencyCeilingMS/latencyFloorMS, float64(i+1)/LatencyBucketCount)" in source
    assert count == slo.BUCKET_COUNT
    assert count - 1 == slo.BOUNDARY_COUNT
    for index in range(slo.BOUNDARY_COUNT):
        want = floor * math.pow(ceiling / floor, (index + 1) / count)
        assert abs(slo.boundary_ms(index) - want) <= want * 1e-12


def test_fr3_a_typed_threshold_snaps_down_to_a_boundary() -> None:
    index = slo.boundary_at_or_under(500.0)
    assert index is not None
    assert round(slo.boundary_ms(index), 1) == 487.2
    assert slo.boundary_at_or_under(slo.boundary_ms(10)) == 10
    assert slo.boundary_at_or_under(1.0) is None
    assert slo.boundary_at_or_under(120_000.0) == slo.BOUNDARY_COUNT - 1


# ---------------------------------------------------------------------- states


def test_a_healthy_target_is_on_track_with_no_qualifier() -> None:
    status = slo.status_of(_facts())
    assert status.state is State.ON_TRACK
    assert status.qualifiers == ()
    assert not status.attention


@pytest.mark.parametrize(
    ("changes", "qualifier"),
    [
        ({"allowed": 8, "failures": 1}, slo.Qualifier.THIN_TRAFFIC),
        ({"failures": 1_000}, None),
        ({"failures": 1_200}, slo.Qualifier.MISSED),
        ({"coverage": 0.71}, slo.Qualifier.NOT_MEASURED),
        ({"last_event_minute": NOW - timedelta(hours=3)}, slo.Qualifier.NO_RECENT_REQUESTS),
        ({"alerting": False}, slo.Qualifier.NO_ALERTS),
    ],
)
def test_ac51_a_burn_on_fresh_status_reads_running_out_and_the_rest_are_qualifiers(
    changes: dict[str, object], qualifier: slo.Qualifier | None
) -> None:
    status = slo.status_of(_facts(burn=FAST, **changes))
    assert status.state is State.RUNNING_OUT
    assert status.attention
    if qualifier is not None:
        assert qualifier in status.qualifiers


def test_ac51_a_burn_with_nothing_left_says_so_in_its_sentence() -> None:
    status = slo.status_of(_facts(burn=FAST, failures=1_000))
    assert status.variant is slo.Variant.NOTHING_LEFT


def test_ac51_a_burn_on_stale_status_reads_not_up_to_date_and_still_counts() -> None:
    status = slo.status_of(_facts(burn=FAST, evaluated_minute=NOW - timedelta(minutes=16)))
    assert status.state is State.NOT_UP_TO_DATE
    assert status.variant is slo.Variant.WAS_BURNING
    assert status.attention


def test_ac51_a_disabled_target_with_a_tier_holding_reads_disabled_and_sorts_last() -> None:
    disabled = slo.status_of(_facts(enabled=False, burn=FAST))
    assert disabled.state is State.DISABLED
    assert slo.Qualifier.RUNNING_OUT in disabled.qualifiers
    on_track = slo.status_of(_facts())
    ordered = slo.ordered([("disabled", disabled), ("fine", on_track)])
    assert [name for name, _ in ordered] == ["fine", "disabled"]


def test_ac51_a_resolving_burn_reads_back_within_its_allowance() -> None:
    resolving = Burn(
        severity="critical", rate=0.4, since=FAST.since, hours_left=None, resolving=True
    )
    assert slo.status_of(_facts(burn=resolving)).variant is slo.Variant.RESOLVING


def test_ac51_a_burn_no_tier_can_judge_reads_not_judged_since() -> None:
    unjudged = Burn(severity="warning", rate=3.0, since=SLOW.since, hours_left=None, judged=False)
    assert slo.status_of(_facts(burn=unjudged)).variant is slo.Variant.NOT_JUDGED


@pytest.mark.parametrize(
    ("changes", "state"),
    [
        ({"enabled": False}, State.DISABLED),
        ({"evaluated_minute": NOW - timedelta(minutes=16)}, State.NOT_UP_TO_DATE),
        ({"evaluated_minute": None}, State.NOT_UP_TO_DATE),
        ({"source_gone": True}, State.SOURCE_GONE),
        ({"counting": True}, State.COUNTING),
        ({"coverage": 0.89}, State.NOT_MEASURED),
        ({"last_event_minute": NOW - timedelta(minutes=16)}, State.NO_RECENT_REQUESTS),
        ({"days_held": 6}, State.BUILDING_HISTORY),
        ({"failures": 1_000}, State.NOTHING_LEFT),
        ({"allowed": 9, "failures": 0}, State.TOO_LITTLE_TRAFFIC),
    ],
)
def test_fr10_each_state_applies_when_its_rule_does(
    changes: dict[str, object], state: State
) -> None:
    assert slo.status_of(_facts(**changes)).state is state


def test_a_target_never_worked_out_reads_counting_until_15_minutes_have_passed() -> None:
    new = _facts(evaluated_minute=None, expected_since=NOW - timedelta(minutes=2), counting=True)
    owed = _facts(evaluated_minute=None, expected_since=NOW - timedelta(minutes=16), counting=True)
    assert slo.status_of(new).state is State.COUNTING
    assert slo.status_of(owed).state is State.NOT_UP_TO_DATE


def test_ac9_coverage_at_91_percent_judges_and_at_89_does_not() -> None:
    assert slo.status_of(_facts(coverage=0.91)).state is State.ON_TRACK
    assert slo.status_of(_facts(coverage=0.89)).state is State.NOT_MEASURED


def test_building_history_is_under_a_quarter_of_the_window() -> None:
    assert slo.status_of(_facts(days_held=7)).state is State.ON_TRACK
    assert slo.status_of(_facts(window_days=7, days_held=1)).state is State.BUILDING_HISTORY
    assert slo.status_of(_facts(window_days=7, days_held=2)).state is State.ON_TRACK


def test_a_check_reads_no_recent_runs_only_after_three_intervals() -> None:
    check = {"kind": slo.CHECK_AVAILABILITY, "check_interval_seconds": 3_600}
    two_hours = _facts(last_event_minute=NOW - timedelta(hours=2), **check)
    four_hours = _facts(last_event_minute=NOW - timedelta(hours=4), **check)
    assert slo.status_of(two_hours).state is State.ON_TRACK
    assert slo.status_of(four_hours).state is State.NO_RECENT_REQUESTS


def test_ac111_exactly_at_the_allowance_reads_exactly_on_target_and_one_more_misses() -> None:
    exactly = slo.status_of(_facts(failures=1_000))
    beyond = slo.status_of(_facts(failures=1_001))
    assert (exactly.state, exactly.variant) == (State.NOTHING_LEFT, slo.Variant.EXACTLY_ON_TARGET)
    assert (beyond.state, beyond.variant) == (State.NOTHING_LEFT, slo.Variant.MISSED)


def test_ac111_two_allowed_and_five_failures_is_nothing_left_and_counts() -> None:
    status = slo.status_of(_facts(allowed=2, failures=5))
    assert status.state is State.NOTHING_LEFT
    assert status.attention
    assert slo.Qualifier.THIN_TRAFFIC in status.qualifiers


def test_ac12_a_window_with_no_failure_carries_the_instrumentation_qualifier() -> None:
    status = slo.status_of(_facts(failures=0))
    assert slo.Qualifier.NEVER_FAILED in status.qualifiers


@pytest.mark.parametrize(
    ("longest", "silent", "counted"),
    [(40, 79, False), (40, 81, True), (8 * 60, 15 * 60, False), (8 * 60, 17 * 60, True)],
)
def test_ac98_the_attention_limit_follows_the_usual_quiet(
    longest: int, silent: int, counted: bool
) -> None:
    facts = _facts(
        last_event_minute=NOW - timedelta(minutes=silent), longest_quiet_7d_minutes=longest
    )
    status = slo.status_of(facts)
    assert status.state is State.NO_RECENT_REQUESTS
    assert status.attention is counted


def test_ac98_a_silence_of_25_hours_or_an_open_traffic_finding_always_counts() -> None:
    long_quiet = {"longest_quiet_7d_minutes": 20 * 60}
    day = _facts(last_event_minute=NOW - timedelta(hours=25), **long_quiet)
    finding = _facts(
        last_event_minute=NOW - timedelta(minutes=20), traffic_finding_open=True, **long_quiet
    )
    assert slo.status_of(day).attention
    assert slo.status_of(finding).attention


def test_qualifiers_come_in_the_designs_order() -> None:
    status = slo.status_of(
        _facts(burn=FAST, alerting=False, allowed=8, failures=9, coverage=0.5, days_held=1)
    )
    assert status.qualifiers == (
        slo.Qualifier.NO_ALERTS,
        slo.Qualifier.MISSED,
        slo.Qualifier.THIN_TRAFFIC,
        slo.Qualifier.NOT_MEASURED,
        slo.Qualifier.BUILDING_HISTORY,
    )


def test_ac110_counted_rows_come_first_critical_then_warning_then_shortest_time_left() -> None:
    rows = [
        ("building", slo.status_of(_facts(days_held=1))),
        ("disabled", slo.status_of(_facts(enabled=False))),
        ("warning", slo.status_of(_facts(burn=SLOW))),
        ("gone", slo.status_of(_facts(source_gone=True))),
        ("critical-later", slo.status_of(_facts(burn=FAST))),
        (
            "critical-sooner",
            slo.status_of(_facts(burn=Burn("critical", 30.0, FAST.since, hours_left=5.0))),
        ),
        ("fine", slo.status_of(_facts())),
    ]
    names = [name for name, _ in slo.ordered(rows)]
    assert names == [
        "critical-sooner",
        "critical-later",
        "warning",
        "gone",
        "building",
        "fine",
        "disabled",
    ]
    counted = sum(status.attention for _, status in rows)
    assert names[:counted] == ["critical-sooner", "critical-later", "warning", "gone"]


# ---------------------------------------------------------------------- days


def test_ac73_a_day_in_kolkata_is_its_local_midnight_to_midnight() -> None:
    start, end = slo.day_bounds(date(2026, 11, 7), KOLKATA)
    assert start == datetime(2026, 11, 6, 18, 30, tzinfo=UTC)
    assert end - start == timedelta(days=1)


@pytest.mark.parametrize(
    ("day", "minutes"),
    [(date(2026, 3, 8), 1_380), (date(2026, 11, 1), 1_500), (date(2026, 11, 7), 1_440)],
)
def test_ac73_a_clock_change_day_has_its_own_length(day: date, minutes: int) -> None:
    start, end = slo.day_bounds(day, ZoneInfo("America/New_York"))
    assert (end - start) == timedelta(minutes=minutes)


def test_the_migration_froze_the_same_kinds_and_answers() -> None:
    spec = importlib.util.spec_from_file_location("revision_0052", MIGRATION)
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    assert ", ".join(f"'{kind}'" for kind in slo.KINDS) == revision._KINDS
    assert ", ".join(f"'{answer}'" for answer in slo.SAMPLING_ANSWERS) == revision._SAMPLING


# --------------------------------------------------------- Step 2: four kinds


def test_ac4_every_kind_is_creatable_and_endpoint_latency_is_no_kind() -> None:
    assert slo.CREATABLE_KINDS == slo.KINDS
    assert "endpoint_latency" not in slo.KINDS


def test_ac101_a_latency_threshold_outside_the_boundaries_is_refused() -> None:
    """The highest offered boundary is about 50.5 s, and anything above it is refused
    rather than quietly snapped to it (AC101); below the finest there is no boundary."""
    assert round(slo.HIGHEST_BOUNDARY_MS) == 50_523
    assert slo.latency_refusal(500.0) is None
    assert slo.latency_refusal(slo.HIGHEST_BOUNDARY_MS) is None
    above = slo.latency_refusal(slo.HIGHEST_BOUNDARY_MS + 1)
    assert above is not None and "50.5 s" in above
    below = slo.latency_refusal(1.0)
    assert below is not None and "1.2 ms" in below
    assert slo.latency_refusal(0) is not None
    assert slo.latency_refusal(math.nan) is not None


def test_ac3_the_good_count_is_every_bucket_at_or_under_the_boundary() -> None:
    buckets = [0] * slo.BUCKET_COUNT
    index = slo.boundary_at_or_under(500.0)
    assert index is not None
    buckets[index - 1], buckets[index], buckets[index + 1] = 3, 5, 7
    assert slo.at_or_under(buckets, index) == 8
    assert slo.at_or_under(buckets, index - 1) == 3


@pytest.mark.parametrize(
    ("endpoint", "looks"),
    [
        ("GET /health", True),
        ("/healthz", True),
        ("GET /api/readyz", True),
        ("GET /livez", True),
        ("grpc.health.v1.Health/Check", True),
        ("GET /ping", True),
        ("GET /checkout", False),
        ("POST /orders/healthy-snacks", False),
        ("GET /shipping/delivery", False),
    ],
)
def test_fr34_names_that_look_like_health_checks_are_pre_ticked(endpoint: str, looks: bool) -> None:
    assert slo.looks_like_health_check(endpoint) is looks


def test_fr34_at_most_20_endpoints_are_left_out() -> None:
    assert slo.LEFT_OUT_MOST == 20


def test_fr11_a_check_s_coverage_is_runs_seen_against_runs_expected() -> None:
    """Missed runs lower a check's coverage, and runs in maintenance are left out of it,
    as excluded time is (FR11, AC67's coverage half)."""
    day = date(2026, 11, 2)
    now = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)
    held = {
        day: DayCounts(
            good=1_000,
            total=1_200,
            lost_minutes=0,
            expected_runs=1_440,
            missed_runs=120,
            maintenance_runs=120,
        )
    }
    figures = slo.window_figures(
        held,
        window_days=7,
        first_event_at=datetime(2026, 11, 1, tzinfo=UTC),
        now=now,
        zone=ZoneInfo("UTC"),
    )
    assert figures.coverage == pytest.approx(1_200 / 1_320)
    assert (figures.good, figures.total, figures.failures) == (1_000, 1_200, 200)


def test_ac67_a_stopped_prober_reads_not_measured_through_missed_runs() -> None:
    day = date(2026, 11, 2)
    now = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)
    held = {
        day: DayCounts(good=100, total=100, lost_minutes=0, expected_runs=1_440, missed_runs=1_340)
    }
    figures = slo.window_figures(
        held,
        window_days=7,
        first_event_at=datetime(2026, 11, 1, tzinfo=UTC),
        now=now,
        zone=ZoneInfo("UTC"),
    )
    assert figures.coverage is not None and figures.coverage < slo.COVERAGE_TO_JUDGE


@pytest.mark.parametrize(
    ("interval", "window", "objective", "allowed"),
    [
        (300, 28, "99.9", 8),
        (300, 28, "99.5", 40),
        (60, 28, "99.9", 40),
        (3_600, 28, "99.9", 0),
        (60, 7, "99.9", 10),
    ],
)
def test_fr8_what_a_check_s_interval_allows(
    interval: int, window: int, objective: str, allowed: int
) -> None:
    expected = slo.expected_runs(interval, window_days=window)
    assert slo.allowed_failures(Decimal(objective), expected) == allowed


def test_fr16_a_check_slower_than_2_4_hours_a_run_cannot_be_judged() -> None:
    assert slo.alerts_can_judge_interval(8_640)
    assert not slo.alerts_can_judge_interval(8_641)
    assert not slo.alerts_can_judge_interval(21_600)


# ------------------------------------------------------ Step 3: the monthly answer

OCTOBER = date(2026, 10, 1)
# A month read on 7 November, its days all older than 7 days.
AFTER_OCTOBER = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
MINUTES_IN_A_DAY = 24 * 60
DECIMAL_OBJECTIVE = Decimal("99.9")


def _october_days(**changes: object) -> dict[date, DayCounts]:
    """31 full days of 10,000 requests and 5 failures each, all final."""
    base = DayCounts(
        good=9_995,
        total=10_000,
        lost_minutes=0,
        final=True,
        definition_version=1,
    )
    return {OCTOBER + timedelta(days=offset): base.replace(**changes) for offset in range(31)}


def _month(days: dict[date, DayCounts], **options: object) -> slo.MonthFigures:
    arguments: dict[str, object] = {
        "month": OCTOBER,
        "kind": slo.SERVICE_AVAILABILITY,
        "objective": DECIMAL_OBJECTIVE,
        "definition_version": 1,
        "first_event_at": LONG_AGO,
        "now": AFTER_OCTOBER,
        "zone": UTC_ZONE,
    }
    arguments.update(options)
    month = slo.month_figures(days, **arguments)  # type: ignore[arg-type]
    assert month is not None
    return month


def test_fr27_a_whole_month_gives_its_figure_and_result() -> None:
    month = _month(_october_days())
    assert (month.good, month.total, month.failures) == (309_845, 310_000, 155)
    assert month.figure_percent == Decimal("99.95")
    assert month.result is slo.MonthResult.MET
    assert (month.days_held, month.days_in_month) == (31, 31)
    assert month.may_still_change is False and month.loss_known is True


def test_a_month_past_its_allowance_is_missed() -> None:
    month = _month(_october_days(good=9_980))
    assert month.result is slo.MonthResult.MISSED
    assert month.figure_percent is not None and month.figure_percent < DECIMAL_OBJECTIVE


@pytest.mark.parametrize(("measured", "judged"), [(0.89, False), (0.91, True)])
def test_ac77_a_month_at_89_percent_coverage_is_not_judged_and_at_91_is(
    measured: float, judged: bool
) -> None:
    lost = round(MINUTES_IN_A_DAY * (1 - measured))
    month = _month(_october_days(lost_minutes=lost))
    assert (month.result is not slo.MonthResult.NOT_JUDGED) is judged
    assert month.coverage == pytest.approx(measured, abs=0.001)


def test_ac77_may_still_change_while_any_day_is_under_7_days_old() -> None:
    days = _october_days()
    assert _month(days, now=datetime(2026, 11, 5, tzinfo=UTC)).may_still_change is True
    assert _month(days, now=datetime(2026, 11, 8, 1, tzinfo=UTC)).may_still_change is False


def test_ac77_a_month_changed_after_it_ended_says_when_and_why() -> None:
    days = _october_days()
    changed = datetime(2026, 11, 3, 9, tzinfo=UTC)
    days[date(2026, 10, 14)] = days[date(2026, 10, 14)].replace(
        changed_at=changed, changed_why=slo.CHANGED_BY_DEFINITION
    )
    month = _month(days)
    assert (month.changed_at, month.changed_why) == (changed, "definition")
    # A change while the month was still under way is not a change after it ended.
    days[date(2026, 10, 14)] = days[date(2026, 10, 14)].replace(
        changed_at=datetime(2026, 10, 20, tzinfo=UTC)
    )
    assert _month(days).changed_at is None


def test_ac45_a_month_holding_two_definitions_says_where_the_old_one_ends() -> None:
    days = _october_days(definition_version=2)
    for offset in range(10):
        day = OCTOBER + timedelta(days=offset)
        days[day] = days[day].replace(definition_version=1)
    month = _month(days, definition_version=2)
    assert month.earlier_definition_through == date(2026, 10, 10)
    assert (
        _month(_october_days(definition_version=2), definition_version=2).earlier_definition_through
        is None
    )


def test_ac69_a_day_whose_losses_are_not_known_carries_onto_its_month() -> None:
    days = _october_days()
    days[date(2026, 10, 2)] = days[date(2026, 10, 2)].replace(loss_known=False)
    assert _month(days).loss_known is False


def test_fr27_a_month_holds_only_its_counted_days_and_says_how_many() -> None:
    days = {day: counts for day, counts in _october_days().items() if day.day >= 20}
    month = _month(days, first_event_at=datetime(2026, 10, 20, tzinfo=UTC))
    assert (month.days_held, month.days_in_month) == (12, 31)
    assert _month(_october_days(), month=date(2026, 10, 1)).days_held == 31
    assert (
        slo.month_figures(
            {},
            month=OCTOBER,
            kind=slo.SERVICE_AVAILABILITY,
            objective=DECIMAL_OBJECTIVE,
            definition_version=1,
            first_event_at=LONG_AGO,
            now=AFTER_OCTOBER,
            zone=UTC_ZONE,
        )
        is None
    )


def test_ac77_a_nightly_lull_crossing_midnight_is_one_stretch() -> None:
    days = _october_days(quiet_minutes=0)
    evening, morning = date(2026, 10, 14), date(2026, 10, 15)
    days[evening] = days[evening].replace(
        quiet_minutes=120, quiet_trailing_minutes=120, longest_quiet_minutes=120
    )
    days[morning] = days[morning].replace(
        quiet_minutes=125, quiet_leading_minutes=125, longest_quiet_minutes=125
    )
    month = _month(days)
    assert month.quiet_minutes == 245
    assert month.longest_quiet_minutes == 245


def test_fr27_a_check_month_states_its_missed_and_maintenance_runs() -> None:
    days = _october_days(expected_runs=288, missed_runs=2, maintenance_runs=12)
    month = _month(days, kind=slo.CHECK_AVAILABILITY)
    assert (month.missed_runs, month.maintenance_runs) == (62, 372)
    assert month.sharp_falls == ()


def test_ac113_a_day_60_percent_below_its_weekday_is_a_sharp_fall_and_40_percent_is_not() -> None:
    days = _october_days()
    days[date(2026, 10, 14)] = days[date(2026, 10, 14)].replace(good=4_000, total=4_000)
    days[date(2026, 10, 21)] = days[date(2026, 10, 21)].replace(good=6_000, total=6_000)
    falls = _month(days).sharp_falls
    assert falls == (slo.SharpFall(day=date(2026, 10, 14), fall_percent=60),)


def test_fr27c_a_partial_first_day_is_never_a_sharp_fall() -> None:
    days = _october_days()
    days[date(2026, 10, 15)] = days[date(2026, 10, 15)].replace(good=100, total=100)
    first = datetime(2026, 10, 15, 23, 0, tzinfo=UTC)
    month = _month(days, first_event_at=first)
    assert month.sharp_falls == ()


def test_fr27_months_build_up_from_the_first_day_held_and_stop_at_13() -> None:
    today = date(2027, 12, 15)
    days = {
        date(2026, 1, 1) + timedelta(days=offset): DayCounts(good=10, total=10, lost_minutes=0)
        for offset in range((today - date(2026, 1, 1)).days + 1)
    }
    months = slo.months_of(
        days,
        kind=slo.SERVICE_AVAILABILITY,
        objective=DECIMAL_OBJECTIVE,
        definition_version=0,
        first_event_at=LONG_AGO,
        now=datetime(2027, 12, 15, 12, tzinfo=UTC),
        zone=UTC_ZONE,
    )
    assert [month.month for month in months][:2] == [date(2027, 12, 1), date(2027, 11, 1)]
    assert len(months) == slo.MONTHS_SHOWN == 13
    assert months[-1].month == date(2026, 12, 1)
    young = {day: counts for day, counts in days.items() if day >= date(2027, 11, 20)}
    assert (
        len(
            slo.months_of(
                young,
                kind=slo.SERVICE_AVAILABILITY,
                objective=DECIMAL_OBJECTIVE,
                definition_version=0,
                first_event_at=LONG_AGO,
                now=datetime(2027, 12, 15, 12, tzinfo=UTC),
                zone=UTC_ZONE,
            )
        )
        == 2
    )


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        (slo.SERVICE_AVAILABILITY, "Measured from requests that reached the service"),
        (slo.SERVICE_LATENCY, "Measured from requests that reached the service"),
        (slo.ENDPOINT_AVAILABILITY, "Measured from requests that reached the service"),
        (slo.CHECK_AVAILABILITY, "Measured from nexovue's own check runs, one view from outside"),
    ],
)
def test_fr27b_each_kind_carries_its_fixed_label(kind: str, label: str) -> None:
    assert slo.report_label(kind).startswith(label)
    assert slo.report_label(kind).endswith("Not a contract figure.")


def test_fr28_the_recount_limit_is_the_later_of_retention_and_29_days() -> None:
    today = date(2026, 11, 8)
    assert slo.recount_limit(today, retention_days=395) == date(2026, 10, 10)
    assert slo.recount_limit(today, retention_days=30) == date(2026, 10, 10)
    assert slo.recount_limit(today, retention_days=7) == date(2026, 11, 2)


def test_the_longest_quiet_stretch_joins_days_across_midnight() -> None:
    evening = DayCounts(
        good=5,
        total=5,
        lost_minutes=0,
        quiet_minutes=120,
        quiet_trailing_minutes=120,
        longest_quiet_minutes=120,
    )
    night = DayCounts(
        good=0,
        total=0,
        lost_minutes=0,
        quiet_minutes=1440,
        quiet_leading_minutes=1440,
        quiet_trailing_minutes=1440,
        longest_quiet_minutes=1440,
    )
    morning = DayCounts(
        good=5,
        total=5,
        lost_minutes=0,
        quiet_minutes=60,
        quiet_leading_minutes=60,
        longest_quiet_minutes=60,
    )
    assert slo.longest_quiet([evening, night, morning]) == 120 + 1440 + 60


def test_the_months_migration_froze_the_same_kinds_results_and_answers() -> None:
    path = next((Path(__file__).resolve().parents[1] / "migrations/versions").glob("0053_*.py"))
    spec = importlib.util.spec_from_file_location("revision_0053", path)
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    assert ", ".join(f"'{kind}'" for kind in slo.KINDS) == revision._KINDS
    assert ", ".join(f"'{result}'" for result in slo.MonthResult) == revision._RESULTS
    assert ", ".join(f"'{answer}'" for answer in slo.SAMPLING_ANSWERS) == revision._SAMPLING


# ------------------------------------------------------- Step 4: a burn from status


def test_a_burn_reads_from_the_status_the_job_stored() -> None:
    """T4.5: Running out comes from `slo_status` alone, so it needs no rule (FR10)."""
    since = datetime(2026, 11, 8, 2, 41, tzinfo=UTC)
    held = {
        "chart_tier": "fast",
        "rate": 14.7,
        "hours_left": 50.0,
        "by_tier": {"fast": {"can_judge": True, "holds": True}},
        "burn": {"severity": "critical", "rate": 14.7, "hours_left": 50.0},
    }
    burning = slo.burn_from_status(held, burning_since=since)
    assert burning == slo.Burn(
        severity="critical", rate=14.7, since=since, hours_left=50.0, resolving=False, judged=True
    )
    resolving = slo.burn_from_status(
        held | {"rate": None, "by_tier": {"fast": {"can_judge": True, "holds": False}}},
        burning_since=since,
    )
    assert resolving is not None and resolving.resolving and resolving.judged
    unjudged = slo.burn_from_status(
        held | {"by_tier": {"fast": {"can_judge": False, "holds": False}}}, burning_since=since
    )
    assert unjudged is not None and not unjudged.judged
    assert slo.burn_from_status(held, burning_since=None) is None
