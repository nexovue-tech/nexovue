"""Which burn tier holds, without a store (plan T4.3).

AC14 (every tier at its boundary, long and short, at 28 and 7 days), AC15 (a 90% target
can still page), AC16 (the floor, and half failing skips it), AC17's logic (a clean short
window holds nothing), AC92 (a night failing every request pages within the hour; 2% does
not pass the floor), AC93's logic (healthy traffic after a silence is an answer, not a
silence), AC100 (a check's floors), AC103 (the floor while counting), and how long what
is left lasts (FR18).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from nexovue_platform import slo

from nexovue_scheduler.slos import burn
from nexovue_scheduler.slos.burn import TierReading, WindowCounts
from nexovue_scheduler.slos.store import CheckMinute, MinuteCounts

FAST, MEDIUM, SLOW = slo.TIERS
MINUTE = timedelta(minutes=1)
END = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def _counts(events: int, failures: int, coverage: float = 1.0) -> WindowCounts:
    return WindowCounts(events=events, failures=failures, coverage=coverage)


def _only(
    tier: slo.Tier, long: WindowCounts, short: WindowCounts
) -> dict[slo.TierName, TierReading]:
    """One tier read; the others hold too few events to judge."""
    quiet = TierReading(long=_counts(0, 0), short=_counts(0, 0))
    readings = {each.name: quiet for each in slo.TIERS}
    readings[tier.name] = TierReading(long=long, short=short)
    return readings


def _judge(
    readings: dict[slo.TierName, TierReading],
    *,
    objective: str = "99.9",
    window_days: int = 28,
    budget: int | None = 1_000_000,
    left: int | None = None,
    is_check: bool = False,
) -> burn.BurnJudgment:
    return burn.judge(
        readings,
        objective=Decimal(objective),
        window_days=window_days,
        budget=budget,
        left=left,
        is_check=is_check,
    )


def _failures_at(rate: float, events: int, objective: str) -> int:
    return round(rate * slo.allowed_share(Decimal(objective)) * events)


# ----------------------------------------------------------------- AC14, AC17


@pytest.mark.parametrize("window_days", [28, 7])
@pytest.mark.parametrize("tier", slo.TIERS, ids=lambda tier: tier.name)
def test_ac14_each_tier_holds_just_over_its_rate_and_not_just_under(
    tier: slo.Tier, window_days: int
) -> None:
    events = 1_000_000
    limit = slo.tier_limit(tier, Decimal("99.9"), window_days)
    over = _counts(events, _failures_at(limit * 1.01, events, "99.9"))
    under = _counts(events, _failures_at(limit * 0.99, events, "99.9"))
    budget = 10  # a small budget, so the floor is FR16's 2 and only the rates decide

    assert _judge(_only(tier, over, over), window_days=window_days, budget=budget).breached
    for long, short in ((under, over), (over, under)):
        judged = _judge(_only(tier, long, short), window_days=window_days, budget=budget)
        assert judged.breached is False
        assert judged.holding is None


def test_ac17_a_long_window_over_the_rate_with_a_clean_short_window_holds_nothing() -> None:
    long = _counts(10_000, 500)
    clean = _counts(500, 0)
    judged = _judge(_only(FAST, long, clean), budget=10)
    assert judged.breached is False


def test_the_severity_is_critical_for_fast_and_medium_and_a_warning_for_slow() -> None:
    burning = _counts(10_000, 1_000)
    assert _judge(_only(FAST, burning, burning), budget=10).severity == "critical"
    assert _judge(_only(MEDIUM, burning, burning), budget=10).severity == "critical"
    assert _judge(_only(SLOW, burning, burning), budget=10).severity == "warning"


def test_the_rate_shown_is_the_fastest_holding_tier_s_long_window() -> None:
    fast = TierReading(long=_counts(1_000, 100), short=_counts(100, 50))
    slow = TierReading(long=_counts(10_000, 300), short=_counts(1_000, 30))
    medium = TierReading(long=_counts(0, 0), short=_counts(0, 0))
    judged = _judge(
        {FAST.name: fast, MEDIUM.name: medium, SLOW.name: slow},
        budget=10,
    )
    assert judged.holding is FAST
    assert judged.rate == pytest.approx(100.0)


# ------------------------------------------------------------------------ AC15


def test_ac15_a_90_percent_target_can_still_page_the_fast_tier() -> None:
    every_request = _counts(100, 100)
    judged = _judge(_only(FAST, every_request, every_request), objective="90", budget=10)
    assert slo.tier_limit(FAST, Decimal("90"), 28) == pytest.approx(9.0)
    assert judged.holding is FAST


# ------------------------------------------------------------------------ AC16


def test_ac16_one_failure_in_ten_requests_judges_nothing() -> None:
    thin = _counts(10, 1)
    assert _judge(_only(FAST, thin, _counts(2, 1))).breached is None


def test_ac16_a_small_budget_fires_on_two_failures_in_twenty() -> None:
    judged = _judge(_only(FAST, _counts(20, 2), _counts(2, 1)), budget=8)
    assert judged.holding is FAST


def test_ac16_a_large_budget_holds_two_in_twenty_under_its_floor() -> None:
    judged = _judge(_only(FAST, _counts(20, 2), _counts(2, 1)), budget=1_000)
    assert judged.breached is False


def test_ac16_half_failing_skips_the_floor() -> None:
    judged = _judge(_only(FAST, _counts(20, 10), _counts(2, 1)), budget=1_000)
    assert judged.holding is FAST


def test_fewer_than_two_failures_is_an_answer_not_a_silence() -> None:
    """AC93's logic: healthy traffic judges, so an open burn can resolve."""
    healthy = _counts(1_000, 1)
    assert _judge(_only(FAST, healthy, _counts(100, 1)), budget=10).breached is False


# ------------------------------------------------------------------------ AC92


@pytest.mark.parametrize("objective", ["99", "95"])
def test_ac92_a_night_failing_every_request_pages_within_the_hour(objective: str) -> None:
    """Day traffic is 1,000 an hour, a night 5% of it, at least 20 an hour. A 28-day
    budget at that traffic is far above the floor's reach in an hour."""
    budget = slo.allowed_failures(Decimal(objective), 1_000 * 24 * 28)
    night = _counts(50, 50)
    judged = _judge(_only(FAST, night, _counts(4, 4)), objective=objective, budget=budget)
    assert judged.holding is FAST
    assert judged.severity == "critical"


@pytest.mark.parametrize("objective", ["99", "95"])
def test_ac92_a_night_failing_two_percent_does_not_pass_the_floor(objective: str) -> None:
    budget = slo.allowed_failures(Decimal(objective), 1_000 * 24 * 28)
    for tier in slo.TIERS:
        hours = tier.long / timedelta(hours=1)
        events = int(50 * hours)
        night = _counts(events, events // 50)
        judged = _judge(_only(tier, night, night), objective=objective, budget=budget)
        assert judged.breached is False, tier.name


# ----------------------------------------------------------------------- AC103


def test_ac103_with_no_counts_the_floor_is_two() -> None:
    assert burn.floor_of(FAST, objective=Decimal("99.9"), window_days=28, budget=None) == 2
    assert burn.floor_of(FAST, objective=Decimal("99.9"), window_days=28, budget=0) == 2


def test_ac103_while_counting_the_floor_uses_the_counts_so_far() -> None:
    nominal = 14.4 * 60 / (28 * 24 * 60)
    assert burn.floor_of(
        FAST, objective=Decimal("99.9"), window_days=28, budget=10_000
    ) == pytest.approx(nominal * 10_000 / 2)


def test_ac103_the_floor_uses_the_capped_rate_at_a_low_objective() -> None:
    capped = 9.0 * 60 / (28 * 24 * 60)
    assert burn.floor_of(
        FAST, objective=Decimal("90"), window_days=28, budget=100_000
    ) == pytest.approx(capped * 100_000 / 2)


# ------------------------------------------------------------- coverage, FR17


def test_a_tier_below_90_percent_coverage_judges_nothing() -> None:
    burning = _counts(1_000, 500, coverage=0.89)
    assert _judge(_only(FAST, burning, burning), budget=10).breached is None


def test_a_short_window_with_no_event_judges_nothing() -> None:
    assert _judge(_only(FAST, _counts(1_000, 500), _counts(0, 0)), budget=10).breached is None


# ----------------------------------------------------------------------- AC100


def test_ac100_a_check_needs_ten_runs_and_a_short_window_of_two_intervals() -> None:
    nine = _counts(9, 9)
    ten = _counts(10, 10)
    assert _judge(_only(FAST, nine, nine), is_check=True, budget=10).breached is None
    assert _judge(_only(FAST, ten, ten), is_check=True, budget=10).holding is FAST
    assert burn.short_window(FAST, interval_seconds=300) == timedelta(minutes=10)
    assert burn.short_window(FAST, interval_seconds=60) == timedelta(minutes=5)


# --------------------------------------------------------------------- FR18


def test_what_is_left_lasts_at_the_failures_per_hour_seen() -> None:
    fast = _counts(10_000, 600)  # 600 failures in the hour
    judged = _judge(_only(FAST, fast, _counts(1_000, 60)), budget=10, left=1_200)
    assert judged.hours_left == pytest.approx(2.0)
    nothing_left = _judge(_only(FAST, fast, _counts(1_000, 60)), budget=10, left=-5)
    assert nothing_left.hours_left == 0.0


# --------------------------------------------------------- windows from minutes


def test_a_window_s_counts_leave_out_excluded_minutes_and_count_lost_ones_against_it() -> None:
    minutes = {END - MINUTE * (offset + 1): MinuteCounts(10, 1) for offset in range(60)}
    lost = frozenset({END - MINUTE * 3, END - MINUTE * 4})
    excluded = frozenset({END - MINUTE * 10})
    counted = burn.window_counts(
        minutes, end=END, length=timedelta(hours=1), lost=lost, excluded=excluded, since=None
    )
    assert (counted.events, counted.failures) == (590, 59)
    assert counted.coverage == pytest.approx(57 / 59)


def test_a_window_is_counted_only_from_the_first_event() -> None:
    since = END - MINUTE * 10
    counted = burn.window_counts(
        {}, end=END, length=timedelta(hours=1), lost=frozenset(), excluded=frozenset(), since=since
    )
    assert counted.coverage == 1.0
    assert counted.events == 0


def test_a_check_window_counts_runs_and_its_missed_runs_lower_coverage() -> None:
    runs = {
        END - MINUTE * (offset + 1): CheckMinute(
            seen=1, counted=1, up=0 if offset < 2 else 1, maintenance=0
        )
        for offset in range(0, 60, 2)
    }
    counted = burn.check_window_counts(
        runs,
        end=END,
        length=timedelta(hours=1),
        interval_seconds=60,
        locations=1,
        excluded=frozenset(),
        since=None,
    )
    assert (counted.events, counted.failures) == (30, 1)
    assert counted.coverage == pytest.approx(0.5)
