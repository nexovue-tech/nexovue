"""The in-process half of the limiter: windows, shares and in-flight slots.

Each API process enforces its share of a limit — the limit divided by
`API_INSTANCES` — so these hold the arithmetic that decides whether a caller is
refused, with the clock in the test's hands.
"""

from __future__ import annotations

from typing import Final

import pytest

from nexovue_api.ratelimit.limiter import MAX_TRACKED_CALLERS, LocalLimits
from nexovue_api.ratelimit.policy import Quota

PER_MINUTE: Final = Quota(name="reads", limit=6, window_seconds=60)


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


def _spend(limits: LocalLimits, count: int, identity: str = "caller") -> list[bool]:
    return [
        limits.consume(scope="session", identity=identity, quota=PER_MINUTE).allowed
        for _ in range(count)
    ]


def test_a_caller_is_refused_past_the_limit_and_told_when_to_return(clock: _Clock) -> None:
    limits = LocalLimits(instances=1, clock=clock)
    assert _spend(limits, PER_MINUTE.limit) == [True] * PER_MINUTE.limit

    clock.now += 10
    refused = limits.consume(scope="session", identity="caller", quota=PER_MINUTE)

    assert not refused.allowed
    assert refused.reset_seconds == 50


def test_the_window_slides_rather_than_resetting_on_the_minute(clock: _Clock) -> None:
    """Half the allowance early, half late: the early half frees up when it is a
    minute old, not when a clock boundary passes."""
    limits = LocalLimits(instances=1, clock=clock)
    _spend(limits, PER_MINUTE.limit // 2)
    clock.now += 40
    _spend(limits, PER_MINUTE.limit // 2)

    clock.now += 19
    assert _spend(limits, 1) == [False]
    clock.now += 2
    assert _spend(limits, PER_MINUTE.limit // 2) == [True] * (PER_MINUTE.limit // 2)
    assert _spend(limits, 1) == [False]


def test_each_process_enforces_its_share(clock: _Clock) -> None:
    """Three processes each allow a third, so three together allow the policy."""
    limits = LocalLimits(instances=3, clock=clock)

    assert _spend(limits, PER_MINUTE.limit // 3 + 1) == [True, True, False]


def test_a_share_is_never_zero(clock: _Clock) -> None:
    limits = LocalLimits(instances=PER_MINUTE.limit * 2, clock=clock)

    assert _spend(limits, 2) == [True, False]


def test_one_callers_spending_is_not_anothers(clock: _Clock) -> None:
    limits = LocalLimits(instances=1, clock=clock)
    _spend(limits, PER_MINUTE.limit, identity="busy")

    assert _spend(limits, 1, identity="quiet") == [True]


def test_in_flight_slots_are_capped_shared_and_given_back() -> None:
    limits = LocalLimits(instances=2)
    slot = {"scope": "organization", "identity": "acme"}

    assert [limits.acquire_slot(**slot, max_concurrent=8) for _ in range(5)] == [
        True,
        True,
        True,
        True,
        False,
    ]
    limits.release_slot(**slot)
    assert limits.acquire_slot(**slot, max_concurrent=8)


def test_a_flood_of_invented_callers_is_forgotten_rather_than_held(clock: _Clock) -> None:
    limits = LocalLimits(instances=1, clock=clock)
    for caller in range(MAX_TRACKED_CALLERS + 10):
        limits.consume(scope="ip", identity=str(caller), quota=PER_MINUTE)

    assert len(limits._windows) == MAX_TRACKED_CALLERS
