"""Which minute is settled (design, "When a minute is settled"; plan T2b.1).

The rule is proved with progress and offsets the tests choose;
`test_detection_settle_live.py` proves the real inputs feed it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from nexovue_platform.detection import SETTLING_LAG

from nexovue_scheduler.alerts import thresholds
from nexovue_scheduler.detection.settle import (
    EXPORT_ALLOWANCE,
    GUARD_ORGANIZATIONS,
    HOLD_LIMIT,
    Hold,
    Intake,
    Mark,
    Settled,
    Span,
    apply_holds,
    fallen,
    guards,
    refusal_holds,
    settled_minute,
    spans,
)

NOW: Final = datetime(2026, 11, 1, 12, 5, 30, tzinfo=UTC)
# 12:05:30 less the 2-minute allowance and the minute itself: 12:02 is the newest
# minute whose end, plus the allowance, has passed.
CURRENT: Final = datetime(2026, 11, 1, 12, 2, tzinfo=UTC)
LONG_AGO: Final = NOW - timedelta(hours=3)


def test_the_allowance_is_f21_s_settling_time() -> None:
    assert timedelta(minutes=2) == EXPORT_ALLOWANCE == thresholds.SETTLING


def test_the_newest_settled_minute_starts_the_shared_lag_before_the_clock() -> None:
    """The API reads delays by the same lag the scheduler settles by."""
    assert settled_minute(now=NOW, partition_count=0, marks={}, newest_offsets={}).current == (
        NOW - SETTLING_LAG
    ).replace(second=0)


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


# --------------------------------------------- holds (design 2.1, item 1; C5)

MINUTE: Final = timedelta(minutes=1)


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 11, 1, hour, minute, tzinfo=UTC)


def spans_of(first: datetime, last: datetime) -> list[datetime]:
    return [first + step * MINUTE for step in range((last - first) // MINUTE + 1)]


def _caught_up() -> Settled:
    return settled_minute(now=NOW, partition_count=0, marks={}, newest_offsets={})


def test_a_refused_minute_holds_the_settled_minute_before_it() -> None:
    """Any minute nexovue's intake refused is unsettled for every project."""
    held = apply_holds(_caught_up(), refusal_holds([_at(12, 1), _at(12, 3)]), now=NOW)

    assert held.minute == _at(12, 0)
    assert [hold.span for hold in held.holding] == [Span(_at(12, 1), _at(12, 3))]
    assert held.expired == ()


def test_a_hold_ends_once_the_pipeline_has_delivered_past_it() -> None:
    """Refusals stopped and their senders had the allowance to send again."""
    held = apply_holds(_caught_up(), refusal_holds([_at(11, 50), CURRENT]), now=NOW)

    assert held.minute == CURRENT
    assert held.holding == ()


def test_refused_minutes_close_together_are_one_hold() -> None:
    assert spans([_at(11, 55), _at(11, 57), _at(12, 1), _at(12, 5)]) == [
        Span(_at(11, 55), _at(11, 57)),
        Span(_at(12, 1), _at(12, 1)),
        Span(_at(12, 5), _at(12, 5)),
    ]


def test_a_hold_a_slow_pipeline_is_inside_holds_from_its_first_minute() -> None:
    """A slow worker must not let the start of a refused spell be judged early."""
    lagging = settled_minute(
        now=NOW,
        partition_count=1,
        marks={0: Mark(5, _at(11, 59) + EXPORT_ALLOWANCE + MINUTE)},
        newest_offsets={0: 9},
    )
    holds = refusal_holds([_at(11, 50), _at(11, 51), *spans_of(_at(11, 57), _at(12, 1))])

    held = apply_holds(lagging, holds, now=NOW)

    assert lagging.minute == _at(11, 59)
    assert held.minute == _at(11, 56), "the earlier, passed spell holds nothing"


def test_a_hold_past_its_limit_is_released_and_reported() -> None:
    """Each hold lasts at most 30 minutes; after that the minute is judged."""
    first = NOW.replace(second=0) - HOLD_LIMIT - MINUTE
    refused = [first + step * MINUTE for step in range(int(HOLD_LIMIT / MINUTE) + 1)]

    held = apply_holds(_caught_up(), refusal_holds(refused), now=NOW)

    assert held.minute == CURRENT
    assert held.holding == ()
    assert [hold.span.first for hold in held.expired] == [first]


def test_a_hold_within_its_limit_still_holds() -> None:
    first = NOW.replace(second=0) - HOLD_LIMIT + MINUTE
    held = apply_holds(_caught_up(), refusal_holds(spans_of(first, CURRENT + MINUTE)), now=NOW)

    assert held.minute == first - MINUTE
    assert held.expired == ()


def test_holds_never_settle_what_the_pipeline_has_not() -> None:
    missing = settled_minute(now=NOW, partition_count=1, marks={}, newest_offsets={0: 4})

    held = apply_holds(missing, refusal_holds([_at(11, 0)]), now=NOW)

    assert held.minute is None


# ------------------------------------------- the pipeline signal and the guard


def test_a_minute_the_install_accepted_half_its_reference_or_less_has_fallen() -> None:
    intake = Intake(
        reference=100.0,
        minutes={_at(11, 0): 100, _at(11, 1): 40, _at(11, 2): 50, _at(11, 3): 51},
    )

    assert fallen(intake) == [_at(11, 1), _at(11, 2)]


def test_an_install_with_no_reference_traffic_has_nothing_to_fall_from() -> None:
    assert fallen(Intake(reference=0.5, minutes={_at(11, 0): 0})) == []


def _votes(count: int, onset: datetime) -> dict[str, datetime]:
    return {f"org-{index}": onset + (index % 3) * MINUTE for index in range(count)}


def test_enough_organizations_losing_traffic_as_the_intake_fell_trip_the_guard() -> None:
    fell = [_at(12, 0) + step * MINUTE for step in range(8)]

    (guard,) = guards(_votes(GUARD_ORGANIZATIONS, _at(11, 59)), fell)

    assert guard == Hold(Span(_at(11, 59), _at(12, 7)), "guarded")


def test_one_organization_short_of_the_count_does_not_trip_the_guard() -> None:
    fell = [_at(12, 0) + step * MINUTE for step in range(8)]

    assert guards(_votes(GUARD_ORGANIZATIONS - 1, _at(12, 0)), fell) == []


def test_organizations_losing_traffic_while_the_intake_held_up_do_not_trip_it() -> None:
    """A hostile set of tenants stopping together cannot switch detection off for
    everyone unless nexovue's own intake fell with them."""
    assert guards(_votes(GUARD_ORGANIZATIONS * 4, _at(12, 0)), []) == []


def test_drops_long_before_the_fall_are_not_its_votes() -> None:
    fell = [_at(12, 30) + step * MINUTE for step in range(5)]

    assert guards(_votes(GUARD_ORGANIZATIONS, _at(12, 0)), fell) == []
