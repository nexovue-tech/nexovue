"""Whether now is quiet on a channel, and until when (FR15, BR4)."""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest
from nexovue_platform.alerts.quiet_hours import QuietHours, quiet_until
from pydantic import ValidationError

NIGHTS = QuietHours(timezone="Asia/Kolkata", days=[0, 1, 2, 3, 4], start=time(22), end=time(7))


def test_a_night_that_began_yesterday_is_still_quiet_this_morning() -> None:
    # Tuesday 06:00 in Kolkata is Monday 00:30 UTC; the window began Monday 22:00.
    tuesday_morning = datetime(2026, 9, 22, 0, 30, tzinfo=UTC)
    assert quiet_until(NIGHTS, tuesday_morning) == datetime(2026, 9, 22, 1, 30, tzinfo=UTC)


def test_the_afternoon_is_not_quiet() -> None:
    assert quiet_until(NIGHTS, datetime(2026, 9, 22, 9, 0, tzinfo=UTC)) is None


def test_a_night_is_quiet_only_if_it_began_on_a_chosen_day() -> None:
    # Saturday 23:00 in Kolkata: Saturday is not chosen.
    saturday_night = datetime(2026, 9, 26, 17, 30, tzinfo=UTC)
    assert quiet_until(NIGHTS, saturday_night) is None


def test_no_quiet_hours_is_never_quiet() -> None:
    assert quiet_until(None, datetime(2026, 9, 22, 0, 30, tzinfo=UTC)) is None


def test_an_unknown_time_zone_or_an_empty_window_is_refused() -> None:
    with pytest.raises(ValidationError):
        QuietHours(timezone="Mars/Olympus", days=[0], start=time(22), end=time(7))
    with pytest.raises(ValidationError):
        QuietHours(timezone="UTC", days=[0], start=time(22), end=time(22))


def test_an_end_the_clock_skips_is_one_answer_for_held_and_new_warnings() -> None:
    """New York skips 02:00-03:00 on 2026-03-08. A quiet period ending at 02:30 ends
    at one instant, whether asked of a warning held overnight or one raised at 03:10."""
    hours = QuietHours(
        timezone="America/New_York", days=list(range(7)), start=time(22, 0), end=time(2, 30)
    )
    overnight = datetime(2026, 3, 8, 4, 0, tzinfo=UTC)  # 23:00 EST on the 7th
    after_the_jump = datetime(2026, 3, 8, 7, 10, tzinfo=UTC)  # 03:10 EDT

    ends = quiet_until(hours, overnight)
    assert ends is not None
    assert quiet_until(hours, after_the_jump) == (ends if after_the_jump < ends else None)
    assert ends > after_the_jump
