"""Which minute is settled (design, "When a minute is settled"; plan T2b.1).

The rule is proved with progress and offsets the tests choose;
`test_detection_settle_live.py` proves the real inputs feed it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from nexovue_scheduler.detection.settle import EXPORT_ALLOWANCE, Mark, settled_minute

NOW: Final = datetime(2026, 11, 1, 12, 5, 30, tzinfo=UTC)
# 12:05:30 less the 2-minute allowance and the minute itself: 12:02 is the newest
# minute whose end, plus the allowance, has passed.
CURRENT: Final = datetime(2026, 11, 1, 12, 2, tzinfo=UTC)
LONG_AGO: Final = NOW - timedelta(hours=3)


def test_the_allowance_is_f21_s_settling_time() -> None:
    assert timedelta(minutes=2) == EXPORT_ALLOWANCE


def test_an_idle_partition_is_caught_up_however_old_its_progress() -> None:
    settled = settled_minute(
        now=NOW,
        partition_count=2,
        marks={0: Mark(100, LONG_AGO), 1: Mark(7, NOW - timedelta(seconds=5))},
        newest_offsets={0: 100, 1: 7},
    )

    assert settled.minute == CURRENT
    assert not settled.behind


def test_unread_data_with_old_progress_is_behind() -> None:
    settled = settled_minute(
        now=NOW,
        partition_count=2,
        marks={0: Mark(100, LONG_AGO), 1: Mark(7, NOW)},
        newest_offsets={0: 101, 1: 7},
    )

    assert settled.behind
    assert settled.lagging == (0,)
    assert settled.minute == datetime(2026, 11, 1, 9, 2, tzinfo=UTC), (
        "held to the minute its last counted record settles"
    )


def test_a_partition_with_data_and_no_progress_is_behind() -> None:
    settled = settled_minute(
        now=NOW,
        partition_count=3,
        marks={0: Mark(100, NOW), 2: Mark(5, NOW)},
        newest_offsets={0: 100, 1: 40, 2: 5},
    )

    assert settled.behind
    assert settled.minute is None
    assert settled.missing == (1,)


def test_a_partition_that_never_held_a_record_does_not_hold_settling_back() -> None:
    settled = settled_minute(
        now=NOW,
        partition_count=2,
        marks={0: Mark(100, NOW)},
        newest_offsets={0: 100, 1: -1},
    )

    assert settled.minute == CURRENT


def test_the_slowest_partition_decides() -> None:
    settled = settled_minute(
        now=NOW,
        partition_count=2,
        marks={0: Mark(9, NOW - timedelta(minutes=4)), 1: Mark(9, NOW - timedelta(minutes=10))},
        newest_offsets={0: 10, 1: 10},
    )

    assert settled.lagging == (0, 1)
    # 11:55:30 less the allowance and the minute.
    assert settled.minute == datetime(2026, 11, 1, 11, 52, tzinfo=UTC)


def test_a_minute_settles_exactly_when_its_end_plus_the_allowance_arrives() -> None:
    boundary = datetime(2026, 11, 1, 12, 5, tzinfo=UTC)

    settled = settled_minute(now=boundary, partition_count=0, marks={}, newest_offsets={})

    assert settled.minute == datetime(2026, 11, 1, 12, 2, tzinfo=UTC)
