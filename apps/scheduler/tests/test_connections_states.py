"""Each connection state and each move between them (F22 AC79; spec, *Link and
connection states*), as one pure function so every row is cheap to name.

A call that works makes the connection `working`. A failure a person must fix (a key
GitHub refuses, an App installed nowhere) needs a person, except during setup, where the
setup screen shows the step instead. A failure that may pass counts up, and a working
connection becomes `failing` only after 5 in a row, so one bad minute pages nobody.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from nexovue_platform.connections import CONNECTION_FAILURES, FAILURES_BEFORE_FAILING

from nexovue_scheduler.connections.states import (
    LASTING_FAILURES,
    PASSING_FAILURES,
    Standing,
    after_failure,
    after_success,
)

NOW: Final = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
EARLIER: Final = NOW - timedelta(minutes=20)


def _standing(state: str, failures: int = 0, since: datetime | None = None) -> Standing:
    return Standing(
        state=state,
        consecutive_failures=failures,
        failing_since=since,
        last_failure="server_error" if failures else None,
        last_failure_status=503 if failures else None,
    )


def test_every_failure_word_is_either_lasting_or_passing() -> None:
    assert set(LASTING_FAILURES) | set(PASSING_FAILURES) == set(CONNECTION_FAILURES)
    assert not set(LASTING_FAILURES) & set(PASSING_FAILURES)


@pytest.mark.parametrize("state", ["setting_up", "working", "failing", "needs_attention"])
def test_a_call_that_works_makes_any_live_connection_working(state: str) -> None:
    after = after_success(_standing(state, 3, EARLIER), now=NOW)

    assert after == Standing(
        state="working",
        consecutive_failures=0,
        failing_since=None,
        last_failure=None,
        last_failure_status=None,
        last_ok_at=NOW,
    )


def test_a_passing_failure_counts_up_and_a_working_connection_stays_working() -> None:
    after = after_failure(_standing("working"), "timed_out", None, now=NOW)

    assert (after.state, after.consecutive_failures, after.failing_since) == ("working", 1, NOW)
    assert (after.last_failure, after.last_ok_at) == ("timed_out", None)


def test_the_fifth_straight_failure_makes_it_failing_keeping_when_it_began() -> None:
    before = _standing("working", FAILURES_BEFORE_FAILING - 1, EARLIER)

    after = after_failure(before, "server_error", 502, now=NOW)

    assert (after.state, after.consecutive_failures) == ("failing", FAILURES_BEFORE_FAILING)
    assert (after.failing_since, after.last_failure_status) == (EARLIER, 502)


def test_failing_stays_failing_while_failures_last() -> None:
    after = after_failure(_standing("failing", 9, EARLIER), "unreachable", None, now=NOW)

    assert (after.state, after.consecutive_failures) == ("failing", 10)


@pytest.mark.parametrize("state", ["working", "failing"])
@pytest.mark.parametrize("failure", ["unauthorized", "app_not_installed", "key_unreadable"])
def test_a_failure_a_person_must_fix_needs_attention(state: str, failure: str) -> None:
    after = after_failure(_standing(state, 2, EARLIER), failure, 401, now=NOW)

    assert after.state == "needs_attention"
    assert after.last_failure == failure


def test_needs_attention_is_not_hidden_by_a_passing_failure() -> None:
    after = after_failure(_standing("needs_attention"), "timed_out", None, now=NOW)

    assert after.state == "needs_attention"


@pytest.mark.parametrize("failure", sorted(CONNECTION_FAILURES))
def test_a_setup_stays_setting_up_whatever_failed(failure: str) -> None:
    """The setup screen names the step (design, *GitHub setup screens*)."""
    after = after_failure(_standing("setting_up"), failure, None, now=NOW)

    assert (after.state, after.last_failure) == ("setting_up", failure)


def test_a_disconnected_connection_never_moves() -> None:
    with pytest.raises(ValueError, match="disconnected"):
        after_success(_standing("disconnected"), now=NOW)
    with pytest.raises(ValueError, match="disconnected"):
        after_failure(_standing("disconnected"), "timed_out", None, now=NOW)
