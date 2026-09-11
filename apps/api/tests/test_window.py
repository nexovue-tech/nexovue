"""The window and page size every telemetry read is bounded by."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexovue_api import window
from nexovue_api.errors.exceptions import InvalidRequestError

_START = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
_END = datetime(2026, 8, 27, 11, 0, tzinfo=UTC)


def test_the_default_window_is_the_last_hour() -> None:
    start, end = window.resolve_window(None, None)

    assert end - start == window.DEFAULT_WINDOW


def test_a_window_that_ends_before_it_starts_is_rejected() -> None:
    with pytest.raises(InvalidRequestError):
        window.resolve_window(_END, _START)


@pytest.mark.parametrize(
    ("requested", "expected"), [(None, 50), (10, 10), (1000, 200), (0, 1), (-5, 1)]
)
def test_the_limit_is_clamped_to_the_documented_range(requested: int | None, expected: int) -> None:
    assert window.clamp_limit(requested, default=50, maximum=200) == expected


def test_the_receive_window_is_wider_than_the_window_it_was_derived_from() -> None:
    """A bound on the customer's clock prunes no partition on ours."""
    received_from, received_to = window.receive_window(_START, _END)

    assert received_from == _START - window.RECEIVE_CLOCK_SLACK
    assert received_to == _END + window.RECEIVE_CLOCK_SLACK


def test_the_receive_slack_is_at_least_one_partition() -> None:
    """Partitions are daily: less than a day widens the range without buying one."""
    assert timedelta(days=1) <= window.RECEIVE_CLOCK_SLACK
