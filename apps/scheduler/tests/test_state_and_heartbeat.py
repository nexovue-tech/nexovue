"""When a run changes a check's state, and when a heartbeat is late (AC14, AC19)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nexovue_scheduler.probes.heartbeat import outcome_of
from nexovue_scheduler.probes.state import CurrentState, decide

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def test_the_first_confirmed_run_sets_the_state() -> None:
    assert decide(None, "down", NOW).change_to == "down"


def test_the_same_outcome_again_records_the_run_and_changes_nothing() -> None:
    decision = decide(CurrentState("up", NOW - timedelta(minutes=1)), "up", NOW)
    assert (decision.record, decision.change_to) == (True, None)


def test_a_run_delivered_after_a_newer_one_changes_nothing() -> None:
    decision = decide(CurrentState("up", NOW), "down", NOW - timedelta(seconds=30))
    assert decision.record is False


def test_a_prober_error_is_never_a_state() -> None:
    """BR7: our failure is not the customer's."""
    assert (
        decide(CurrentState("up", NOW - timedelta(minutes=1)), "prober_error", NOW).record is False
    )


def test_a_heartbeat_called_within_its_allowance_is_up() -> None:
    assert (
        outcome_of(
            period_seconds=300,
            grace_seconds=60,
            last_ping_at=NOW - timedelta(seconds=359),
            created_at=NOW - timedelta(days=1),
            now=NOW,
        )
        == "up"
    )


def test_a_heartbeat_past_period_plus_grace_is_down() -> None:
    assert (
        outcome_of(
            period_seconds=300,
            grace_seconds=60,
            last_ping_at=NOW - timedelta(seconds=361),
            created_at=NOW - timedelta(days=1),
            now=NOW,
        )
        == "down"
    )


def test_a_new_heartbeat_is_not_down_before_it_could_have_been_called() -> None:
    fresh = outcome_of(
        period_seconds=300,
        grace_seconds=60,
        last_ping_at=None,
        created_at=NOW - timedelta(seconds=100),
        now=NOW,
    )
    never = outcome_of(
        period_seconds=300,
        grace_seconds=60,
        last_ping_at=None,
        created_at=NOW - timedelta(seconds=400),
        now=NOW,
    )
    assert (fresh, never) == (None, "down")
