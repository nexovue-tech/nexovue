"""The comparison table, one test per row (F22 plan T4.4; design, *The comparison*).

T is what the tracker says now, S the link's `seen_state`, W its `wanted_state`. The
table is pure, so each row is one named test here; T4.5's live suite runs every row
through the real job against Postgres and the simulated GitHub. Running a row's outcome
through the table again lands on row 1 (AC45's "running twice changes nothing").
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from nexovue_scheduler.connections.compare import (
    Action,
    CaseView,
    LinkView,
    after,
    compare,
    compare_read,
)
from nexovue_scheduler.connections.trackers import IssueGone, IssueMissing, IssueMoved, IssueSeen

ACTIVE = CaseView(state="investigating", signal_open=False, followed=True)
ACTIVE_SIGNAL_OPEN = replace(ACTIVE, signal_open=True)
RESOLVED = CaseView(state="resolved", signal_open=False, followed=True)


def _link(seen: str | None, wanted: str | None = None, *, held: bool = False) -> LinkView:
    return LinkView(role="synced", seen=seen, wanted=wanted, held=held)


def test_row_1_nothing_changed() -> None:
    assert compare(_link("open"), "open", ACTIVE) is Action.NOTHING
    assert compare(_link("closed"), "closed", RESOLVED) is Action.NOTHING


def test_row_2_a_person_changed_the_case() -> None:
    assert compare(_link("open", "closed"), "open", RESOLVED) is Action.WRITE_WANTED
    assert compare(_link("closed", "open"), "closed", ACTIVE) is Action.WRITE_WANTED


def test_row_3_our_earlier_write_landed() -> None:
    assert compare(_link("open", "closed"), "closed", RESOLVED) is Action.ACCEPT_LANDED
    assert compare(_link("closed", "open"), "open", ACTIVE) is Action.ACCEPT_LANDED


def test_row_4_reopened_there_reopens_a_recently_resolved_case() -> None:
    assert compare(_link("closed"), "open", RESOLVED) is Action.REOPEN_CASE


def test_row_4_does_not_apply_once_the_link_is_not_followed() -> None:
    unfollowed = replace(RESOLVED, followed=False)
    assert compare(_link("closed"), "open", unfollowed) is Action.RECORD_ONLY


def test_row_4b_reopened_there_during_a_hold_ends_the_hold() -> None:
    held = _link("closed", held=True)
    assert compare(held, "open", ACTIVE_SIGNAL_OPEN) is Action.END_HOLD
    assert compare(held, "open", ACTIVE) is Action.END_HOLD


def test_row_5a_closed_there_with_the_signal_open_holds_the_case() -> None:
    assert compare(_link("open"), "closed", ACTIVE_SIGNAL_OPEN) is Action.HOLD


def test_row_5b_the_hold_ends_when_the_signal_closes() -> None:
    held = _link("closed", held=True)
    assert compare(held, "closed", ACTIVE) is Action.RESOLVE_AFTER_HOLD
    # While the signal stays open, the held case waits.
    assert compare(held, "closed", ACTIVE_SIGNAL_OPEN) is Action.NOTHING


def test_row_5c_closed_there_with_the_signal_closed_resolves() -> None:
    assert compare(_link("open"), "closed", ACTIVE) is Action.RESOLVE_CLOSED


def test_row_6_a_case_that_already_matches_only_records_the_tracker() -> None:
    # Resolved in nexovue with nothing wanted (no_activity, leave-open), then closed there.
    assert compare(_link("open"), "closed", RESOLVED) is Action.RECORD_ONLY
    # Active, and the issue reopened there without a hold: nothing to undo.
    assert compare(_link("closed"), "open", ACTIVE) is Action.RECORD_ONLY


def test_a_reference_link_never_moves_a_case() -> None:
    """AC53: its status is read and shown, and changes nothing else."""
    reference = LinkView(role="reference", seen="open", wanted=None, held=False)
    assert compare(reference, "closed", ACTIVE) is Action.RECORD_ONLY
    assert compare(reference, "open", ACTIVE) is Action.NOTHING
    assert compare(replace(reference, seen=None), "open", RESOLVED) is Action.RECORD_ONLY


# Row 7 is decided where the reopen is applied: the unique index refuses it (BR7),
# so `REOPEN_CASE` becomes `needs_attention`. The live suite proves it through the job.


def test_row_8_deleted_is_gone() -> None:
    assert compare_read(IssueGone()) is Action.GONE


def test_row_9_missing_from_a_readable_repository_is_gone_and_read_again() -> None:
    assert compare_read(IssueMissing(repository_readable=True)) is Action.GONE_RECHECK


def test_row_10_missing_from_an_unreadable_repository_needs_attention() -> None:
    assert compare_read(IssueMissing(repository_readable=False)) is Action.PLACE_UNREACHABLE


def test_row_11_a_move_is_a_signal_only() -> None:
    assert compare_read(IssueMoved(repository="acme/web", number=7, issue_id="99")) is Action.MOVED
    assert compare_read(IssueMoved(repository=None, number=None, issue_id=None)) is Action.MOVED


def test_a_read_issue_goes_through_the_table() -> None:
    assert compare_read(IssueSeen(state="open", close_reason=None)) is None


@pytest.mark.parametrize(
    ("link", "tracker", "case"),
    [
        (_link("open", "closed"), "open", RESOLVED),
        (_link("open", "closed"), "closed", RESOLVED),
        (_link("closed"), "open", RESOLVED),
        (_link("closed", held=True), "open", ACTIVE),
        (_link("open"), "closed", ACTIVE_SIGNAL_OPEN),
        (_link("closed", held=True), "closed", ACTIVE),
        (_link("open"), "closed", ACTIVE),
        (_link("open"), "closed", RESOLVED),
    ],
)
def test_running_twice_changes_nothing_the_second_time(
    link: LinkView, tracker: str, case: CaseView
) -> None:
    """AC45: whatever a row does, the table then reads row 1."""
    action = compare(link, tracker, case)
    link_then, case_then, tracker_then = after(action, link, tracker, case)
    assert compare(link_then, tracker_then, case_then) is Action.NOTHING
