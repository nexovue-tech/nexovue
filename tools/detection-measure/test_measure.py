"""The burst and window arithmetic behind `measure.py`, without a store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from measure import Minute, burst_lengths, hourly_rates, poisson_tail, report, windows_past_minimum

START = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


def _minutes(errors: list[int], requests: int = 200) -> list[Minute]:
    return [
        Minute(at=START + timedelta(minutes=i), requests=requests, errors=e, p95_ms=80.0)
        for i, e in enumerate(errors)
    ]


def test_the_poisson_tail_matches_a_hand_count() -> None:
    assert poisson_tail(0, 2.0) == 1.0
    assert abs(poisson_tail(5, 1.0) - 0.0036598) < 1e-6


def test_a_four_minute_burst_is_one_burst_of_four() -> None:
    series = _minutes([1] * 60 + [30, 28, 31, 29] + [1] * 60)
    assert burst_lengths(series, {10: 0.005, 11: 0.005}) == [4]


def test_one_quiet_minute_inside_a_burst_does_not_split_it() -> None:
    series = _minutes([1] * 30 + [30, 30, 1, 30, 30] + [1] * 30)
    assert burst_lengths(series, {10: 0.005}) == [5]


def test_ordinary_failures_are_not_bursts() -> None:
    series = _minutes([1, 2, 1, 0, 1, 2, 1] * 10)
    rates = hourly_rates(series)
    assert burst_lengths(series, rates) == []
    assert windows_past_minimum(series, rates) == 0


def test_a_burst_clears_the_minimum_in_every_window_that_holds_enough_of_it() -> None:
    series = _minutes([1] * 20 + [30, 30, 30, 30] + [1] * 20)
    # Every five-minute window that holds at least one burst minute clears +2 points and 5 failures.
    assert windows_past_minimum(series, {10: 0.005}) == 8


def test_a_report_counts_short_bursts_per_day() -> None:
    day = [1] * 1440
    for start in (100, 500, 900):
        day[start : start + 4] = [30] * 4
    result = report("p", "checkout", _minutes(day))
    assert result.bursts_per_day == 3
    assert result.short_bursts_per_day == 3
    assert result.burst_minutes_median == 4
