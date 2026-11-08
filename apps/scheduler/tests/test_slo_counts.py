"""A target's day from its source's minutes, without a store (plan T1.4).

AC6 (a replay gives the same row), AC9 (a lost minute lowers coverage and adds no
good events), AC10 (lost and quiet minutes kept apart), AC12 (a window with no
failure carries the instrumentation line), AC73 (local midnight in Kolkata), a quiet
stretch across midnight, and no good events from missing minutes.

Step 2 (plan T2.1 to T2.3): a latency minute's slow requests (AC3), left-out endpoints
subtracted minute by minute and never below zero (AC80, FR34), and a check's day of
runs with its expected, missed and maintenance runs (AC2, AC67's coverage half).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from nexovue_platform import slo

from nexovue_scheduler.slos.counts import (
    DayRow,
    check_day_row,
    day_row,
    day_rows_by_local_day,
    first_run,
    last_event,
    last_run,
    latency_minutes,
    longest_quiet,
    without_left_out,
)
from nexovue_scheduler.slos.store import CheckMinute, LatencyMinute, MinuteCounts

MINUTE = timedelta(minutes=1)
KOLKATA = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 11, 3)
UTC_ZONE = ZoneInfo("UTC")
START, END = slo.day_bounds(DAY, UTC_ZONE)


def _busy(
    first: datetime, count: int, requests: int = 10, failures: int = 0
) -> dict[datetime, MinuteCounts]:
    return {first + MINUTE * i: MinuteCounts(requests, failures) for i in range(count)}


def _row(
    source: Mapping[datetime, MinuteCounts],
    *,
    through: datetime = END,
    first_event_at: datetime = START,
    lost: frozenset[datetime] = frozenset(),
) -> DayRow:
    return day_row(
        source,
        day_start=START,
        day_end=END,
        through=through,
        first_event_at=first_event_at,
        lost=lost,
    )


def test_a_full_busy_day_counts_every_request_and_no_quiet() -> None:
    row = _row(_busy(START, 1_440, requests=10, failures=1))
    assert (row.good, row.total, row.failures) == (12_960, 14_400, 1_440)
    assert (row.quiet_minutes, row.lost_minutes) == (0, 0)
    assert (row.quiet_leading_minutes, row.quiet_trailing_minutes, row.longest_quiet_minutes) == (
        0,
        0,
        0,
    )


def test_ac6_replaying_the_same_minutes_gives_the_same_row() -> None:
    source = _busy(START, 600) | _busy(START + timedelta(hours=12), 300, failures=2)
    assert _row(source) == _row(dict(reversed(list(source.items()))))


def test_missing_minutes_are_quiet_and_add_no_good_events() -> None:
    row = _row(_busy(START + timedelta(hours=1), 60))
    assert (row.good, row.total) == (600, 600)
    assert row.quiet_minutes == 1_440 - 60
    assert row.quiet_leading_minutes == 60
    assert row.quiet_trailing_minutes == 1_440 - 120
    assert row.longest_quiet_minutes == 1_440 - 120


def test_ac9_ac10_a_lost_minute_is_not_quiet_lowers_coverage_and_adds_nothing() -> None:
    lost = frozenset(START + MINUTE * i for i in range(30))
    source = _busy(START + timedelta(minutes=30), 1_410)
    row = _row(source, lost=lost)
    assert row.lost_minutes == 30
    assert row.quiet_minutes == 0
    assert (row.good, row.total) == (14_100, 14_100)
    figures = slo.window_figures(
        {DAY: slo.DayCounts(row.good, row.total, row.lost_minutes)},
        window_days=28,
        first_event_at=START,
        now=END,
        zone=UTC_ZONE,
    )
    assert figures.coverage is not None and figures.coverage < 1


def test_a_day_counts_only_from_the_first_event_and_up_to_the_settled_minute() -> None:
    first = START + timedelta(hours=10)
    through = START + timedelta(hours=12)
    row = _row(_busy(first, 30), first_event_at=first, through=through)
    assert row.total == 300
    # 10:30 to 12:00 is quiet; nothing before the first event, nothing after `through`.
    assert row.quiet_minutes == 90
    assert row.quiet_leading_minutes == 0
    assert row.quiet_trailing_minutes == 90


def test_a_quiet_stretch_across_midnight_is_one_stretch() -> None:
    evening = _row(_busy(START, 22 * 60))
    next_start, next_end = slo.day_bounds(DAY + timedelta(days=1), UTC_ZONE)
    morning = day_row(
        _busy(next_start + timedelta(hours=2), 22 * 60),
        day_start=next_start,
        day_end=next_end,
        through=next_end,
        first_event_at=START,
        lost=frozenset(),
    )
    assert evening.longest_quiet_minutes == 120 and morning.longest_quiet_minutes == 120
    assert longest_quiet([evening, morning]) == 240


def test_ac73_a_minute_either_side_of_kolkata_midnight_lands_in_its_own_day() -> None:
    midnight = datetime(2026, 11, 3, 18, 30, tzinfo=UTC)  # 00:00 on 4 Nov in Kolkata
    source = {midnight - MINUTE: MinuteCounts(1, 0), midnight: MinuteCounts(2, 0)}
    days = day_rows_by_local_day(
        source,
        days=[date(2026, 11, 3), date(2026, 11, 4)],
        zone=KOLKATA,
        through=midnight + timedelta(days=1),
        first_event_at=midnight - timedelta(days=1),
        lost=frozenset(),
    )
    assert days[date(2026, 11, 3)].total == 1
    assert days[date(2026, 11, 4)].total == 2
    third_minutes = days[date(2026, 11, 3)].quiet_minutes + 1
    assert third_minutes == 1_440


def test_ac12_a_window_with_no_failure_carries_the_instrumentation_qualifier() -> None:
    row = _row(_busy(START, 1_440))
    allowed = slo.allowed_failures(Decimal("99.9"), row.total)
    facts = slo.Facts(
        kind=slo.SERVICE_AVAILABILITY,
        window_days=28,
        enabled=True,
        now=END,
        evaluated_minute=END,
        expected_since=START,
        counting=False,
        source_gone=False,
        burn=None,
        coverage=1.0,
        last_event_minute=END - MINUTE,
        days_held=28,
        allowed=allowed,
        failures=row.failures,
        alerting=None,
        snoozed=False,
        longest_quiet_7d_minutes=0,
        traffic_finding_open=False,
        check_interval_seconds=None,
    )
    assert slo.Qualifier.NEVER_FAILED in slo.status_of(facts).qualifiers


# ------------------------------------------------------- Step 2: other sources


def test_ac80_left_out_requests_are_subtracted_minute_by_minute() -> None:
    """A service of 100 requests a minute, 10 of them `/health` probes: 90 count. A
    minute with only probes counts nothing, so it is quiet and a dead service reads
    **No recent requests** (FR34)."""
    service = _busy(START, 3, requests=100, failures=4)
    service[START + MINUTE * 2] = MinuteCounts(10, 0)
    probes = _busy(START, 3, requests=10, failures=1)
    probes[START + MINUTE * 2] = MinuteCounts(10, 0)

    counted = without_left_out(service, [probes])

    assert counted[START] == MinuteCounts(90, 3)
    assert counted[START + MINUTE * 2] == MinuteCounts(0, 0)
    row = _row(counted, through=START + MINUTE * 3)
    assert (row.total, row.failures, row.quiet_minutes) == (180, 6, 1)
    assert last_event(counted) == START + MINUTE


def test_fr34_a_capped_minute_with_no_service_row_never_goes_negative() -> None:
    """The service cap drops service rows, not endpoint rows: a minute with probes and
    no service row subtracts nothing, and more probes than requests leaves zero."""
    service = {START: MinuteCounts(5, 5)}
    probes = {START: MinuteCounts(8, 6), START + MINUTE: MinuteCounts(10, 0)}

    counted = without_left_out(service, [probes])

    assert counted == {START: MinuteCounts(0, 0)}


def test_two_left_out_endpoints_are_both_subtracted() -> None:
    service = {START: MinuteCounts(100, 10)}
    counted = without_left_out(
        service, [{START: MinuteCounts(10, 1)}, {START: MinuteCounts(20, 2)}]
    )
    assert counted == {START: MinuteCounts(70, 7)}


def test_ac3_a_latency_minute_is_good_at_or_under_its_boundary() -> None:
    index = slo.boundary_at_or_under(500.0)
    assert index is not None
    buckets = [0] * slo.BUCKET_COUNT
    buckets[index - 1], buckets[index], buckets[index + 1] = 3, 5, 7
    minutes = {START: LatencyMinute(requests=15, buckets=tuple(buckets))}

    counted = latency_minutes(minutes, index)

    # A latency target's failure is a slow request (FR12).
    assert counted == {START: MinuteCounts(15, 7)}


def _runs(
    first: datetime, count: int, every: timedelta, *, up: int = 1
) -> dict[datetime, CheckMinute]:
    return {
        first + every * i: CheckMinute(seen=1, counted=1, up=up, maintenance=0)
        for i in range(count)
    }


def test_ac2_a_check_day_counts_confirmed_runs_and_states_maintenance() -> None:
    every = timedelta(minutes=5)
    runs = _runs(START, 288, every)
    runs[START] = CheckMinute(seen=1, counted=0, up=0, maintenance=1)
    runs[START + every] = CheckMinute(seen=1, counted=1, up=0, maintenance=0)

    row = check_day_row(
        runs,
        day_start=START,
        day_end=END,
        through=END,
        first_event_at=START,
        interval_seconds=300,
        locations=1,
    )

    assert (row.good, row.total, row.failures) == (286, 287, 1)
    assert (row.expected_runs, row.missed_runs, row.maintenance_runs) == (288, 0, 1)
    # A check has no quiet minutes (FR11).
    assert row.quiet_minutes == 0


def test_ac67_a_stopped_prober_misses_runs_at_each_interval() -> None:
    """Half a day of runs, then none: half the day's runs are missed, whatever the
    interval, and from two locations twice as many are expected."""
    for interval in (60, 300, 3_600):
        every = timedelta(seconds=interval)
        expected = 86_400 // interval
        runs = _runs(START, expected // 2, every)
        row = check_day_row(
            runs,
            day_start=START,
            day_end=END,
            through=END,
            first_event_at=START,
            interval_seconds=interval,
            locations=1,
        )
        assert (row.expected_runs, row.missed_runs) == (expected, expected - expected // 2)
        two = check_day_row(
            runs,
            day_start=START,
            day_end=END,
            through=END,
            first_event_at=START,
            interval_seconds=interval,
            locations=2,
        )
        assert two.expected_runs == 2 * expected
        assert last_run(runs) == START + every * (expected // 2 - 1)


def test_a_check_day_expects_runs_only_from_its_first_run_up_to_the_settled_minute() -> None:
    every = timedelta(minutes=1)
    first = START + timedelta(hours=6)
    runs = _runs(first, 60, every)
    row = check_day_row(
        runs,
        day_start=START,
        day_end=END,
        through=first + timedelta(hours=2),
        first_event_at=first,
        interval_seconds=60,
        locations=1,
    )
    assert (row.expected_runs, row.missed_runs) == (120, 60)
    assert first_run(runs) == first
