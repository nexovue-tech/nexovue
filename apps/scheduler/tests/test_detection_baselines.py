"""The hourly baseline (design, "The normal"; plan T2c.3).

**Parity**: from a quiet service's minute history alone, the pools the simulation
judged by (`fixtures/detection_parity.json`) are built again, at every window
length. **Modes**: which days a minute is compared with, and learning counted in
distinct days.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest

from nexovue_scheduler.detection.baselines import (
    baseline_from_json,
    baseline_json,
    history_spans,
    horizon,
    learning_until,
    select_pool,
    slower_baselines,
)
from nexovue_scheduler.detection.method import BUCKET_COUNT, Minute

PARITY: Final = json.loads(
    (Path(__file__).parent / "fixtures" / "detection_parity.json").read_text(encoding="utf-8")
)
EPOCH: Final = datetime.fromisoformat(PARITY["epoch"])
UTC_ZONE: Final = ZoneInfo("UTC")
KOLKATA: Final = ZoneInfo("Asia/Kolkata")
NEW_YORK: Final = ZoneInfo("America/New_York")
# A Wednesday.
WEDNESDAY_NOON: Final = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def _at(minute: int) -> datetime:
    return EPOCH + timedelta(minutes=minute)


def _history(rows: list[list[Any]]) -> dict[datetime, Minute]:
    history = {}
    for minute, requests, errors, sparse in rows:
        buckets = [0] * BUCKET_COUNT
        for index in range(0, len(sparse), 2):
            buckets[sparse[index]] = sparse[index + 1]
        history[_at(minute)] = Minute(requests, errors, tuple(buckets))
    return history


def _parity_cases() -> list[Any]:
    return [
        pytest.param(case, width, id=f"{case['minutes'][0]}-w{width}")
        for case in PARITY["baselines"]
        for width in (3, 5, 10)
    ]


@pytest.mark.parametrize(("case", "width"), _parity_cases())
def test_the_pools_are_the_simulation_s(case: dict[str, Any], width: int) -> None:
    minutes = [_at(minute) for minute in case["minutes"]]

    built = slower_baselines(
        _history(case["history"]), minutes, zone=UTC_ZONE, first_seen=EPOCH, width=width
    )

    for minute, expected in zip(minutes, case["expected"][str(width)], strict=True):
        baseline = built[minute]
        assert baseline.mode == expected["mode"]
        assert baseline.days == expected["days"]
        assert baseline.pool_requests == expected["pool_requests"]
        assert baseline.threshold_index == expected["threshold_index"]
        assert baseline.share_above == pytest.approx(expected["share_above"], rel=1e-12)
        assert baseline.wander == pytest.approx(expected["wander"], rel=1e-9, abs=1e-15)
        assert baseline.normal_p95_ms == pytest.approx(expected["normal_p95_ms"], rel=1e-12)


def test_the_parity_cases_cover_both_modes_and_cut_pools_at_midnight() -> None:
    modes = {expected["mode"] for case in PARITY["baselines"] for expected in case["expected"]["5"]}
    assert modes == {"daily", "weekly"}
    # 22:15 and later: the ±180-minute pool of the day before reaches past midnight.
    assert all(minute % 1440 >= 22 * 60 for c in PARITY["baselines"] for minute in c["minutes"])


# ------------------------------------------------------------------- modes


def test_three_weeks_of_history_compares_the_same_weekday() -> None:
    first_seen = WEDNESDAY_NOON - timedelta(days=21, hours=1)

    pool = select_pool(WEDNESDAY_NOON, UTC_ZONE, first_seen)

    assert pool.mode == "weekly"
    assert [day.offset for day in pool.days] == [7, 14, 21]
    assert all(day.centre.weekday() == WEDNESDAY_NOON.weekday() for day in pool.days)


def test_four_weeks_is_the_most_used() -> None:
    pool = select_pool(WEDNESDAY_NOON, UTC_ZONE, WEDNESDAY_NOON - timedelta(days=60))

    assert [day.offset for day in pool.days] == [7, 14, 21, 28]


def test_under_three_weeks_compares_weekdays_with_weekdays() -> None:
    """FR5: until the weekly pattern exists, a weekday's normal is earlier weekdays."""
    first_seen = WEDNESDAY_NOON - timedelta(days=10)

    pool = select_pool(WEDNESDAY_NOON, UTC_ZONE, first_seen)

    assert pool.mode == "daily"
    assert all(day.centre.weekday() < 5 for day in pool.days)
    # Tuesday 3 November back to Sunday 25 October: 7 weekdays, the weekends left out.
    assert len(pool.days) == 7


def test_a_weekend_minute_compares_weekends_only() -> None:
    saturday = WEDNESDAY_NOON + timedelta(days=3)

    pool = select_pool(saturday, UTC_ZONE, saturday - timedelta(days=14))

    assert pool.mode == "daily"
    assert {day.centre.weekday() for day in pool.days} == {5, 6}


def test_under_three_distinct_days_is_learning_until_the_third() -> None:
    """AC14: learning is counted in days the service existed, and says until when."""
    first_seen = WEDNESDAY_NOON - timedelta(days=2)  # Monday noon

    assert select_pool(WEDNESDAY_NOON, UTC_ZONE, first_seen).mode == "learning"
    assert learning_until(WEDNESDAY_NOON, UTC_ZONE, first_seen) == date(2026, 11, 5)
    # The Thursday after: Monday, Tuesday and Wednesday are behind it.
    assert select_pool(WEDNESDAY_NOON + timedelta(days=1), UTC_ZONE, first_seen).mode == "daily"


def test_a_weekend_learns_later_than_a_weekday() -> None:
    first_seen = WEDNESDAY_NOON - timedelta(days=2)  # Monday noon
    saturday = WEDNESDAY_NOON + timedelta(days=3)

    assert select_pool(saturday, UTC_ZONE, first_seen).mode == "learning"
    # Its class's third day behind it: Saturday 7, Sunday 8, Saturday 14, so Sunday 15.
    assert learning_until(saturday, UTC_ZONE, first_seen) == date(2026, 11, 15)


def test_days_follow_the_project_s_clock_across_a_daylight_saving_change() -> None:
    """FR7: 09:00 in New York is 09:00 on each past day, whatever its UTC offset.
    The clocks went back on 1 November 2026."""
    nine = datetime(2026, 11, 4, 9, 0, tzinfo=NEW_YORK).astimezone(UTC)

    pool = select_pool(nine, NEW_YORK, nine - timedelta(days=60))

    local = [day.centre.astimezone(NEW_YORK) for day in pool.days]
    assert {(c.hour, c.minute) for c in local} == {(9, 0)}
    assert {c.utcoffset() for c in local} == {timedelta(hours=-4)}
    assert nine.astimezone(NEW_YORK).utcoffset() == timedelta(hours=-5)


def test_the_local_day_decides_the_class_and_the_pool_stops_at_its_midnight() -> None:
    """00:30 on Saturday in Kolkata is Friday 19:00 UTC, and a weekend minute. At
    23:00 on Sunday, the day before's ±180 minutes stop at Kolkata's midnight."""
    saturday = datetime(2026, 11, 7, 0, 30, tzinfo=KOLKATA).astimezone(UTC)
    sunday_late = datetime(2026, 11, 8, 23, 0, tzinfo=KOLKATA).astimezone(UTC)

    weekend = select_pool(saturday, KOLKATA, saturday - timedelta(days=14))
    late = select_pool(sunday_late, KOLKATA, sunday_late - timedelta(days=14))

    assert saturday.weekday() == 4
    assert {day.centre.astimezone(KOLKATA).weekday() for day in weekend.days} == {5, 6}
    day_before = late.days[0]
    assert day_before.offset == 1
    midnight = datetime(2026, 11, 8, tzinfo=KOLKATA).astimezone(UTC)
    assert day_before.dispersion[1] == midnight - timedelta(minutes=1)
    assert day_before.level[1] == day_before.centre + timedelta(minutes=15)


def test_a_learning_minute_reads_no_history() -> None:
    first_seen = WEDNESDAY_NOON - timedelta(hours=1)
    pools = [select_pool(WEDNESDAY_NOON, UTC_ZONE, first_seen)]

    assert history_spans(pools, 5) == {}
    built = slower_baselines({}, [WEDNESDAY_NOON], zone=UTC_ZONE, first_seen=first_seen, width=5)[
        WEDNESDAY_NOON
    ]
    assert built.mode == "learning"
    # Wednesday, Thursday and Friday are behind the Monday after.
    assert built.learning_until == date(2026, 11, 9)


def test_no_traffic_in_the_pool_is_a_normal_with_no_requests() -> None:
    """A service that existed but was quiet at this hour: its days count, and it has
    nothing to judge by, which the method reads as too little traffic."""
    built = slower_baselines(
        {},
        [WEDNESDAY_NOON],
        zone=UTC_ZONE,
        first_seen=WEDNESDAY_NOON - timedelta(days=30),
        width=5,
    )[WEDNESDAY_NOON]

    assert built.mode == "weekly"
    assert built.pool_requests == 0
    assert built.normal_p95_ms is None


def test_a_stored_baseline_reads_back_as_written() -> None:
    first_seen = WEDNESDAY_NOON - timedelta(days=1)
    for baseline in slower_baselines(
        {}, [WEDNESDAY_NOON], zone=UTC_ZONE, first_seen=first_seen, width=3
    ).values():
        assert baseline_from_json(json.loads(json.dumps(baseline_json(baseline)))) == baseline


def test_a_run_covers_the_minutes_being_judged_and_two_hours_ahead() -> None:
    minutes = horizon(datetime(2026, 11, 4, 12, 0, 40, tzinfo=UTC))

    assert minutes[0] == datetime(2026, 11, 4, 11, 45, tzinfo=UTC)
    assert minutes[-1] == datetime(2026, 11, 4, 13, 59, tzinfo=UTC)
    assert len(set(minutes)) == len(minutes)
