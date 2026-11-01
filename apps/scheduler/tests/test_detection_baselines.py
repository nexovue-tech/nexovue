"""The hourly baseline (design, "The normal"; plan T2c.3, T3.2).

**Parity**: from a quiet service's minute history alone, the pools the simulation
judged by (`fixtures/detection_parity.json`) are built again, at every window
length, for all three signals. **Modes**: which days a minute is compared with,
and learning counted in distinct days. **Kept out** (BR3): findings, an admin's
false alarm, snoozes, refused minutes and the burst profile. **Acceptance**: AC1,
AC13, AC14 and AC17.
"""

from __future__ import annotations

import json
import math
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from detection_series import daily, series, span
from detection_series import minute as series_minute
from detection_series import window as series_window

from nexovue_scheduler.detection.baselines import (
    Baselines,
    Exclusions,
    FindingSpan,
    Shift,
    endpoint_profiles,
    exclusions,
    failing_from_json,
    failing_json,
    failing_profile,
    history_spans,
    horizon,
    learning_until,
    select_pool,
    service_baselines,
    slower_from_json,
    slower_json,
    traffic_from_json,
    traffic_json,
)
from nexovue_scheduler.detection.method import (
    BUCKET_COUNT,
    LOG_EDGES,
    SENSITIVITIES,
    Minute,
    Window,
    judge_failing,
    judge_slower,
    judge_traffic,
)

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
    """[minute, requests, errors, sparse buckets, [endpoint, requests, errors, …]]."""
    history = {}
    for minute, requests, errors, sparse, endpoints in rows:
        buckets = [0] * BUCKET_COUNT
        for index in range(0, len(sparse), 2):
            buckets[sparse[index]] = sparse[index + 1]
        counts = {
            f"ep{endpoints[i]}": (endpoints[i + 1], endpoints[i + 2])
            for i in range(0, len(endpoints), 3)
        }
        history[_at(minute)] = Minute(requests, errors, tuple(buckets), counts)
    return history


def _built(
    history: dict[datetime, Minute],
    minutes: list[datetime],
    *,
    first_seen: datetime,
    width: int,
    zone: ZoneInfo = UTC_ZONE,
) -> dict[datetime, Baselines]:
    return service_baselines(history, minutes, zone=zone, first_seen=first_seen, width=width)


def _parity_cases() -> list[Any]:
    return [
        pytest.param(case, width, id=f"{case['minutes'][0]}-w{width}")
        for case in PARITY["baselines"]
        for width in (3, 5, 10)
    ]


@pytest.mark.parametrize(("case", "width"), _parity_cases())
def test_the_pools_are_the_simulation_s(case: dict[str, Any], width: int) -> None:
    minutes = [_at(minute) for minute in case["minutes"]]

    built = _built(_history(case["history"]), minutes, first_seen=EPOCH, width=width)

    for minute, expected in zip(minutes, case["expected"][str(width)], strict=True):
        baseline = built[minute].slower
        assert baseline.mode == expected["mode"]
        assert baseline.days == expected["days"]
        assert baseline.pool_requests == expected["pool_requests"]
        assert baseline.threshold_index == expected["threshold_index"]
        assert baseline.share_above == pytest.approx(expected["share_above"], rel=1e-12)
        assert baseline.wander == pytest.approx(expected["wander"], rel=1e-9, abs=1e-15)
        assert baseline.normal_p95_ms == pytest.approx(expected["normal_p95_ms"], rel=1e-12)
        failing, failing_expected = built[minute].failing, expected["failing"]
        assert failing.mode == expected["mode"]
        assert failing.pool_requests == failing_expected["pool_requests"]
        assert failing.pool_failures == failing_expected["pool_failures"]
        assert failing.wander == pytest.approx(failing_expected["wander"], rel=1e-9, abs=1e-15)
        assert set(failing.endpoints) == set(failing_expected["endpoints"])
        for name, (requests, failures, wander) in failing_expected["endpoints"].items():
            normal = failing.endpoints[name]
            assert (normal.pool_requests, normal.pool_failures) == (requests, failures)
            assert normal.wander == pytest.approx(wander, rel=1e-9, abs=1e-15)
        traffic = built[minute].traffic
        assert traffic.mode == expected["mode"]
        assert traffic.median == pytest.approx(expected["traffic"]["median"], rel=1e-12)
        assert traffic.spread == pytest.approx(expected["traffic"]["spread"], rel=1e-12)


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
    built = _built({}, [WEDNESDAY_NOON], first_seen=first_seen, width=5)[WEDNESDAY_NOON]
    # Wednesday, Thursday and Friday are behind the Monday after; traffic waits for
    # 3 weeks as well (FR6).
    for baseline in (built.slower, built.failing, built.traffic):
        assert baseline.mode == "learning"
    assert built.slower.learning_until == built.failing.learning_until == date(2026, 11, 9)
    assert built.traffic.learning_until == date(2026, 11, 25)


def test_no_traffic_in_the_pool_is_a_normal_with_no_requests() -> None:
    """A service that existed but was quiet at this hour: its days count, and it has
    nothing to judge by, which the method reads as too little traffic."""
    built = _built({}, [WEDNESDAY_NOON], first_seen=WEDNESDAY_NOON - timedelta(days=30), width=5)[
        WEDNESDAY_NOON
    ].slower

    assert built.mode == "weekly"
    assert built.pool_requests == 0
    assert built.normal_p95_ms is None


def test_a_stored_baseline_reads_back_as_written() -> None:
    (case, *_) = PARITY["baselines"]
    minutes = [_at(minute) for minute in case["minutes"]]
    for first_seen in (minutes[0] - timedelta(days=1), EPOCH):
        for built in _built(
            _history(case["history"]), minutes, first_seen=first_seen, width=3
        ).values():
            assert slower_from_json(_round_trip(slower_json(built.slower))) == built.slower
            assert failing_from_json(_round_trip(failing_json(built.failing))) == built.failing
            assert traffic_from_json(_round_trip(traffic_json(built.traffic))) == built.traffic


def _round_trip(stored: dict[str, Any]) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(json.dumps(stored))
    return loaded


def test_a_run_covers_the_minutes_being_judged_and_two_hours_ahead() -> None:
    minutes = horizon(datetime(2026, 11, 4, 12, 0, 40, tzinfo=UTC))

    assert minutes[0] == datetime(2026, 11, 4, 11, 45, tzinfo=UTC)
    assert minutes[-1] == datetime(2026, 11, 4, 13, 59, tzinfo=UTC)
    assert len(set(minutes)) == len(minutes)


# ---------------------------------------------------------------- kept out

NORMAL: Final = SENSITIVITIES["normal"]
TUESDAY: Final = datetime(2026, 10, 6, tzinfo=UTC)


def _range(first: datetime, hours: float) -> tuple[datetime, datetime]:
    return first, first + timedelta(hours=hours) - timedelta(minutes=1)


def _finding(
    first: datetime,
    hours: float,
    *,
    service: str | None = "checkout",
    false_alarm: bool = False,
    by_admin: bool = False,
) -> FindingSpan:
    start, last = _range(first, hours)
    return FindingSpan(service, start, last, false_alarm, by_admin)


def test_a_finding_s_minutes_are_kept_out_unless_an_admin_said_they_were_normal() -> None:
    """BR3, D28: a member's false alarm leaves the minutes out; an admin's returns them."""
    noon = TUESDAY + timedelta(hours=12)
    member = exclusions("checkout", [_finding(noon, 1, false_alarm=True)], recorded={}, delayed=[])
    admin = exclusions(
        "checkout", [_finding(noon, 1, false_alarm=True, by_admin=True)], recorded={}, delayed=[]
    )

    assert member.normal.kept_out(noon + timedelta(minutes=30))
    assert not admin.normal.kept_out(noon + timedelta(minutes=30))
    # A false alarm's minutes never shape the burst profile, whoever marked it (D26).
    assert member.profile.kept_out(noon) and admin.profile.kept_out(noon)


def test_snoozed_refused_and_another_service_s_minutes() -> None:
    """D25: snoozed minutes stay out. A refused minute is *data delayed* for every
    service. A project finding (FR15) keeps every service's minutes out; another
    service's own finding keeps none of this one's."""
    noon = TUESDAY + timedelta(hours=12)
    kept = exclusions(
        "checkout",
        [
            _finding(noon, 1, service="payments"),
            _finding(noon + timedelta(hours=3), 1, service=None),
        ],
        recorded={"snoozed": [_range(noon + timedelta(hours=6), 2)]},
        delayed=[(noon + timedelta(hours=9), noon + timedelta(hours=9))],
    )

    assert not kept.normal.kept_out(noon + timedelta(minutes=10))
    assert kept.normal.kept_out(noon + timedelta(hours=3, minutes=10))
    assert kept.normal.kept_out(noon + timedelta(hours=7))
    assert kept.normal.kept_out(noon + timedelta(hours=9))
    assert not kept.normal.kept_out(noon + timedelta(hours=9, minutes=1))


def test_minutes_the_install_wide_guard_held_stay_out_of_every_normal() -> None:
    """Design 2.1, item 1: the guard's minutes are nexovue's intake failing, *data
    delayed* like a refused minute, so neither the normal nor the profile learns
    the drop."""
    noon = TUESDAY + timedelta(hours=12)
    guarded = (noon, noon + timedelta(minutes=9))

    kept = exclusions("checkout", [], recorded={}, delayed=[guarded])

    for minute in (noon, noon + timedelta(minutes=9)):
        assert kept.normal.kept_out(minute) and kept.profile.kept_out(minute)
    assert not kept.normal.kept_out(noon + timedelta(minutes=10))


def test_a_short_finding_stays_in_the_burst_profile_and_a_long_one_does_not() -> None:
    """The simulation's profile keeps a finding of 30 minutes or less, one of the
    service's own bursts, so a bursty service learns its bursts (D17)."""
    noon = TUESDAY + timedelta(hours=12)
    kept = exclusions(
        "checkout",
        [_finding(noon, 0.25), _finding(noon + timedelta(hours=2), 2)],
        recorded={},
        delayed=[],
    )

    assert kept.normal.kept_out(noon) and not kept.profile.kept_out(noon)
    assert kept.profile.kept_out(noon + timedelta(hours=3))


def test_a_burst_profile_leaves_out_what_it_must_not_learn_from() -> None:
    """D26: the same bursty history, once with its bursts inside a false alarm or a
    snooze: those excursions never widen the tolerance."""
    width = NORMAL.window_minutes
    day_start = TUESDAY + timedelta(days=28)
    first = day_start - timedelta(days=28)
    counts: dict[datetime, tuple[int, int]] = {}
    normals: dict[datetime, tuple[float, float]] = {}
    bursts = [first + timedelta(days=day, hours=10) for day in range(0, 28, 3)]
    for at in span(first - timedelta(minutes=width), day_start - timedelta(minutes=1)):
        counts[at] = (100, 1)
        normals[at] = (0.01, 0.0)
    for burst in bursts:
        for at in span(burst, burst + timedelta(minutes=7)):
            counts[at] = (100, 12)

    learned = failing_profile(
        counts, normals, day_start=day_start, width=width, kept_out=Exclusions()
    )
    kept_out = Exclusions([(burst, burst + timedelta(minutes=12)) for burst in bursts])
    without = failing_profile(counts, normals, day_start=day_start, width=width, kept_out=kept_out)

    assert learned.bursty and learned.excursions == len(bursts)
    assert learned.minutes >= 8
    assert without.excursions == 0 and not without.bursty
    assert max(without.heights.values()) < max(learned.heights.values())


def test_learning_counts_the_days_that_remain_after_exclusions() -> None:
    """AC14, FR4: three weekdays of history, Friday, Monday and Tuesday, two of them
    inside findings at this hour, leave one: learning again."""
    noon = WEDNESDAY_NOON
    first_seen = noon - timedelta(days=5)
    kept_out = Exclusions([_range(noon - timedelta(days=day, hours=2), 4) for day in (1, 2)])

    assert select_pool(noon, UTC_ZONE, first_seen).mode == "daily"
    assert select_pool(noon, UTC_ZONE, first_seen, kept_out).mode == "learning"


# --------------------------------------------------------------- acceptance

UTC_RATE: Final = daily(UTC_ZONE, peak=300)


def _judged(
    history: dict[datetime, Minute], baselines: dict[datetime, Baselines]
) -> dict[datetime, dict[str, str]]:
    found = {}
    for at, normal in baselines.items():
        window = Window(tuple(series_window(history, at, NORMAL.window_minutes)))
        verdicts: dict[str, str] = {
            "traffic": judge_traffic(window, normal.traffic, NORMAL).verdict,
            "failing": judge_failing(window, normal.failing, NORMAL).verdict,
            "slower": judge_slower(window, normal.slower, NORMAL).verdict,
        }
        found[at] = verdicts
    return found


def _unusual(judged: dict[datetime, dict[str, str]]) -> list[tuple[datetime, str]]:
    return [
        (at, s) for at, verdicts in judged.items() for s, v in verdicts.items() if v == "unusual"
    ]


def test_a_steady_service_opens_nothing_over_a_week_at_its_busiest_and_quietest() -> None:
    """AC1: a four-week daily and weekly pattern with ordinary noise, judged every
    minute of its busiest and its quietest hour for a week."""
    first_seen = TUESDAY - timedelta(days=28)
    history = series(first_seen, 35 * 1440, daily(UTC_ZONE, peak=300, weekend=0.5), seed=23)
    verdicts: set[str] = set()
    for day in range(7):
        start = TUESDAY + timedelta(days=day)
        minutes = [
            *span(start + timedelta(hours=13, minutes=30), start + timedelta(hours=14, minutes=29)),
            *span(start + timedelta(hours=1), start + timedelta(hours=1, minutes=59)),
        ]
        judged = _judged(
            history,
            service_baselines(
                history, minutes, zone=UTC_ZONE, first_seen=first_seen, width=NORMAL.window_minutes
            ),
        )
        assert _unusual(judged) == [], f"day {day}"
        verdicts |= {v for each in judged.values() for v in each.values()}
    # Judged, not merely quiet: every signal read normal somewhere.
    assert "normal" in verdicts


@pytest.mark.parametrize("shift_minutes", [0, 60])
def test_a_repeated_outage_falls_back_to_the_daily_pattern_and_is_still_caught(
    shift_minutes: int,
) -> None:
    """AC13, FR4, BR3: the same 6-hour outage on two of three Tuesdays, both inside
    findings. On the fourth Tuesday the weekly days at those hours are gone, so the
    normal falls back to the daily pattern; it equals the normal built without the
    outage minutes; and the outage, an hour later or not, is caught."""
    first_seen = TUESDAY - timedelta(days=22)
    clean = series(first_seen, 29 * 1440, daily(UTC_ZONE, peak=300), seed=29)
    history = dict(clean)
    outages = [TUESDAY - timedelta(days=days) + timedelta(hours=10) for days in (7, 14)]
    for start in outages:
        for at in span(start, start + timedelta(hours=6) - timedelta(minutes=1)):
            history[at] = series_minute(3)
    kept_out = Exclusions(
        [
            (start - timedelta(minutes=4), start + timedelta(hours=6, minutes=10))
            for start in outages
        ]
    )
    today = TUESDAY + timedelta(hours=10, minutes=shift_minutes)
    for at in span(today, today + timedelta(hours=6)):
        history[at] = series_minute(3)
    minutes = [
        TUESDAY + timedelta(hours=9, minutes=30),
        *span(today + timedelta(minutes=4), today + timedelta(minutes=12)),
        TUESDAY + timedelta(hours=13),
    ]

    def built(source: dict[datetime, Minute]) -> dict[datetime, Baselines]:
        return service_baselines(
            source,
            minutes,
            zone=UTC_ZONE,
            first_seen=first_seen,
            width=NORMAL.window_minutes,
            kept_out=kept_out,
        )

    with_outages, without = built(history), built(clean)
    assert with_outages == without, "the outage minutes changed the normal"
    assert with_outages[TUESDAY + timedelta(hours=13)].traffic.mode == "daily"
    assert with_outages[TUESDAY + timedelta(hours=9, minutes=30)].traffic.mode == "weekly"
    caught = [at for at, s in _unusual(_judged(history, with_outages)) if s == "traffic"]
    assert caught and caught[0] - today <= timedelta(minutes=9)


def test_the_normal_follows_local_time_through_a_clock_change() -> None:
    """AC17, FR7: a New York service whose traffic follows the local clock, judged for
    three weeks after the clocks went back on 1 November 2026, opens nothing."""
    zone = NEW_YORK
    first_seen = datetime(2026, 10, 1, tzinfo=zone).astimezone(UTC)
    change = datetime(2026, 11, 1, tzinfo=zone).astimezone(UTC)
    rate = daily(zone, peak=300, weekend=0.5)
    history = series(first_seen, (change - first_seen) // timedelta(minutes=1) + 22 * 1440, rate)
    for day in range(1, 22, 2):
        local = datetime.combine(
            (change + timedelta(days=day)).astimezone(zone).date(), datetime.min.time()
        )
        minutes = [
            (local + timedelta(hours=hour, minutes=m)).replace(tzinfo=zone).astimezone(UTC)
            for hour in (9, 14)
            for m in range(0, 30, 3)
        ]
        judged = _judged(
            history,
            service_baselines(
                history, minutes, zone=zone, first_seen=first_seen, width=NORMAL.window_minutes
            ),
        )
        assert _unusual(judged) == [], f"{day} days after the change"


def test_the_fall_back_hour_pools_both_of_its_utc_minutes() -> None:
    """FR7: 01:30 on 1 November 2026 happened twice in New York; a week later, 01:30
    is compared with both."""
    week_later = datetime(2026, 11, 8, 1, 30, tzinfo=NEW_YORK).astimezone(UTC)

    pool = select_pool(week_later, NEW_YORK, week_later - timedelta(days=60))

    (fall_back,) = [day for day in pool.days if day.offset == 7]
    assert [c.astimezone(NEW_YORK).strftime("%H:%M %Z") for c in fall_back.centres] == [
        "01:30 EDT",
        "01:30 EST",
    ]
    assert len(fall_back.level_ranges) == 2
    assert all(len(day.centres) == 1 for day in pool.days if day.offset != 7)


def test_an_endpoint_s_profile_comes_from_its_recorded_excursions_and_its_windows() -> None:
    """D18 per endpoint, D26: heights from its window counts, excursions from what the
    project job recorded, each cut where minutes are kept out, and one inside a false
    alarm dropped."""
    width = NORMAL.window_minutes
    day_start = TUESDAY + timedelta(days=28)
    first = day_start - timedelta(days=28)
    windows = {
        at: {"POST /pay": (100, 1 if at.minute else 30)}
        for at in span(first, day_start - timedelta(minutes=1))
    }
    bursts = [first + timedelta(days=day, hours=10) for day in range(0, 28, 3)]
    runs = {"POST /pay": [(b, b + timedelta(minutes=7)) for b in bursts]}
    false_alarm = Exclusions([(bursts[0], bursts[0] + timedelta(minutes=20))])
    split = Exclusions([(bursts[1] + timedelta(minutes=3), bursts[1] + timedelta(minutes=3))])

    learned = endpoint_profiles(
        windows, runs, day_start=day_start, width=width, kept_out=Exclusions(), days=28.0
    )["POST /pay"]
    without = endpoint_profiles(
        windows, runs, day_start=day_start, width=width, kept_out=false_alarm, days=28.0
    )["POST /pay"]
    cut = endpoint_profiles(
        windows, runs, day_start=day_start, width=width, kept_out=split, days=28.0
    )["POST /pay"]

    assert learned.bursty and learned.excursions == len(bursts)
    assert learned.minutes == 8 + width - 1
    assert learned.height(100) == pytest.approx(0.3)
    assert without.excursions == len(bursts) - 1
    assert cut.excursions == len(bursts) + 1


# ------------------------------------------------------------- level shifts (FR21)

# A level change of a whole number of buckets is exact on the bucket grid.
_SLOWER_BUCKETS: Final = 6
_SLOWER_RATIO: Final = math.exp(_SLOWER_BUCKETS * LOG_EDGES[1])
# The moment the service's level changed, and the hour judged after it.
_CHANGED: Final = TUESDAY + timedelta(hours=9)
_JUDGED: Final = list(
    span(TUESDAY + timedelta(hours=13), TUESDAY + timedelta(hours=13, minutes=30))
)


def _changed_history(
    *, slower_by: int = 0, traffic: float = 1.0, failing: float = 0.01, since: datetime = _CHANGED
) -> tuple[datetime, dict[datetime, Minute]]:
    """Four weeks of a steady daily pattern, then a new level from `since` onwards."""
    first_seen = TUESDAY - timedelta(days=28)
    rate = daily(UTC_ZONE, peak=300)
    history = series(first_seen, 29 * 1440, rate, seed=31)
    rng = random.Random(37)
    for at in span(since, TUESDAY + timedelta(days=1)):
        requests = max(1, round(rate(at) * traffic + rng.gauss(0, 1)))
        history[at] = series_minute(requests, round(requests * failing), slower_by=slower_by)
    return first_seen, history


def _with_shifts(
    history: dict[datetime, Minute], first_seen: datetime, shifts: list[Shift]
) -> dict[datetime, Baselines]:
    return service_baselines(
        history,
        _JUDGED,
        zone=UTC_ZONE,
        first_seen=first_seen,
        width=NORMAL.window_minutes,
        shifts=shifts,
    )


@pytest.mark.parametrize(
    ("signal", "level", "ratio"),
    [
        ("slower", {"slower_by": _SLOWER_BUCKETS}, _SLOWER_RATIO),
        ("traffic", {"traffic": 0.4}, 0.4),
        ("failing", {"failing": 0.06}, 6.0),
    ],
)
def test_a_new_normal_judges_the_new_level_as_normal_at_once(
    signal: str, level: dict[str, Any], ratio: float
) -> None:
    """AC15, FR21: without the shift the new level is unusual; with it, the same
    minutes read normal straight away, with no learning gap."""
    first_seen, history = _changed_history(**level)

    before = _judged(history, _with_shifts(history, first_seen, []))
    after = _judged(history, _with_shifts(history, first_seen, [Shift(signal, ratio, _CHANGED)]))

    assert any(verdicts[signal] == "unusual" for verdicts in before.values())
    assert [at for at, s in _unusual(after) if s == signal] == []
    assert {verdicts[signal] for verdicts in after.values()} & {"normal", "elevated"}


def test_a_new_normal_keeps_the_daily_shape_at_the_new_level() -> None:
    """AC15: every minute's normal moves by the same ratio, so the quiet hours stay
    quieter than the busy ones."""
    first_seen, history = _changed_history(traffic=0.4)
    quiet, busy = TUESDAY + timedelta(hours=3), TUESDAY + timedelta(hours=14)

    def normals(shifts: list[Shift]) -> list[float]:
        built = service_baselines(
            history,
            [quiet, busy],
            zone=UTC_ZONE,
            first_seen=first_seen,
            width=NORMAL.window_minutes,
            shifts=shifts,
        )
        return [built[at].traffic.expected for at in (quiet, busy)]

    unshifted = normals([])
    shifted = normals([Shift("traffic", 0.4, quiet - timedelta(hours=1))])
    assert shifted[0] == pytest.approx(0.4 * unshifted[0], rel=0.05)
    assert shifted[1] == pytest.approx(0.4 * unshifted[1], rel=0.05)


def test_a_day_already_at_the_new_level_is_not_moved_again() -> None:
    """Ten days on, the pool's latest week is at the new level by itself: only the days
    before the change are moved, so the normal is the new level and not the ratio
    applied twice."""
    since = TUESDAY - timedelta(days=10)
    first_seen, history = _changed_history(slower_by=_SLOWER_BUCKETS, since=since)
    # The new level, as history alone would have it, had it always been so.
    _, always = _changed_history(slower_by=_SLOWER_BUCKETS, since=first_seen)

    shifted = _with_shifts(history, first_seen, [Shift("slower", _SLOWER_RATIO, since)])
    plain = _with_shifts(always, first_seen, [])

    for at in _JUDGED:
        assert shifted[at].slower.normal_p95_ms == pytest.approx(
            plain[at].slower.normal_p95_ms, rel=0.15
        )
        # Moved twice, L would sit six buckets higher; noise moves it at most one.
        assert abs(shifted[at].slower.threshold_index - plain[at].slower.threshold_index) <= 1


def test_a_shift_after_the_minute_judged_moves_nothing() -> None:
    first_seen, history = _changed_history()

    assert _with_shifts(
        history, first_seen, [Shift("slower", 3.0, _JUDGED[-1] + timedelta(hours=1))]
    ) == (_with_shifts(history, first_seen, []))
