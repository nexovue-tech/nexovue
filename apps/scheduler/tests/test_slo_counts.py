"""A target's day from its source's minutes, without a store (plan T1.4).

AC6 (a replay gives the same row), AC9 (a lost minute lowers coverage and adds no
good events), AC10 (lost and quiet minutes kept apart), AC12 (a window with no
failure carries the instrumentation line), AC73 (local midnight in Kolkata), a quiet
stretch across midnight, and no good events from missing minutes.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from nexovue_platform import slo

from nexovue_scheduler.slos.counts import DayRow, day_row, day_rows_by_local_day, longest_quiet
from nexovue_scheduler.slos.store import MinuteCounts

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
