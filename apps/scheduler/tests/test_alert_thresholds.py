"""The threshold state machine and the meter check — pure, so every case is cheap."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from nexovue_platform.alerts.model import ErrorRateCondition, LatencyCondition

from nexovue_scheduler.alerts.silence import MeterProgress, meter_is_behind
from nexovue_scheduler.alerts.thresholds import Phase, Reading, judge, step

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
FIVE = timedelta(minutes=5)


def _run(breaches: Sequence[bool | None]) -> list[str | None]:
    """Feed one judgement a minute and collect what each minute did."""
    phase: Phase = "ok"
    since = NOW
    actions: list[str | None] = []
    for minute, breached in enumerate(breaches):
        moved = step(
            phase,
            since,
            breached,
            now=NOW + timedelta(minutes=minute),
            fire_after=FIVE,
            resolve_after=FIVE,
        )
        phase, since = moved.phase, moved.since
        actions.append(moved.action)
    return actions


def test_a_three_minute_breach_does_not_fire() -> None:
    """AC9."""
    assert "fire" not in _run([True, True, True, False, False])


def test_a_six_minute_breach_fires_once_at_five() -> None:
    """AC9."""
    assert _run([True] * 7) == [None, None, None, None, None, "fire", None]


def test_it_resolves_after_five_clear_minutes_and_not_before() -> None:
    actions = _run([True] * 6 + [False] * 6)
    assert actions.count("fire") == 1
    assert actions.index("clear") == 11


def test_a_breach_between_clear_minutes_stays_one_incident() -> None:
    actions = _run([True] * 6 + [False, False, True, False] + [False] * 5)
    assert actions.count("fire") == 1
    assert actions.count("clear") == 1


def test_too_little_traffic_neither_fires_nor_resolves() -> None:
    """FR6."""
    assert _run([None] * 10) == [None] * 10
    firing = _run([True] * 6 + [None] * 10)
    assert "clear" not in firing


def test_an_error_rate_is_judged_on_the_screens_own_arithmetic() -> None:
    rule = ErrorRateCondition(threshold_percent=5, min_requests=20)
    assert judge(rule, Reading("checkout", requests=200, errors=12, p95_ms=100)) == (True, 6.0)
    assert judge(rule, Reading("checkout", requests=200, errors=8, p95_ms=100)) == (False, 4.0)
    # Five failures of eight is not a 62% outage.
    assert judge(rule, Reading("checkout", requests=8, errors=5, p95_ms=100)) == (None, None)
    assert judge(rule, None) == (None, None)


def test_a_p95_is_judged_in_milliseconds() -> None:
    rule = LatencyCondition(threshold_ms=500, min_requests=1)
    assert judge(rule, Reading("checkout", requests=50, errors=0, p95_ms=620.5)) == (True, 620.5)


class _Watermarks:
    def __init__(self, newest: int) -> None:
        self._newest = newest

    def newest_offset(self, topic: str, partition: int) -> int:
        return self._newest


def test_a_meter_with_nothing_unread_is_not_behind_however_quiet() -> None:
    """The case both of Q2's first options got wrong: a single service stops, and
    so does all traffic. Nothing is unread, so silence is real."""
    idle = [MeterProgress("spans-raw", 0, 100, NOW - timedelta(hours=3))]
    assert not meter_is_behind(idle, _Watermarks(100), now=NOW)


def test_a_meter_with_data_waiting_and_no_progress_is_behind() -> None:
    stuck = [MeterProgress("spans-raw", 0, 100, NOW - timedelta(minutes=6))]
    assert meter_is_behind(stuck, _Watermarks(250), now=NOW)


def test_a_meter_that_is_merely_busy_is_not_behind() -> None:
    busy = [MeterProgress("spans-raw", 0, 100, NOW - timedelta(seconds=20))]
    assert not meter_is_behind(busy, _Watermarks(250), now=NOW)
