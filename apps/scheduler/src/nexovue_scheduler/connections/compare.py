"""The comparison (F22 plan T4.4; design, *The comparison*): what one synced link's
fresh read means, as a pure table, so a new developer reads the sync in this file.

T is what the tracker says now, S the link's `seen_state`, W its `wanted_state`, the
status a person's act wants the tracker to reach (null when nothing is pending). With
only two statuses, T different from S and W different from T together mean W = S, which
is never stored, so no true conflict on status can arise (design, *Where this differs
from the research*). Status is not the case: a close with the case signal open holds the
case instead of ending it (D73), and a reference link never changes a case (FR41).

`compare` decides; the job applies, under the finding's and the link's locks, and reads
the table again there with what it then sees. Row 7 (a reopen another active case
blocks) is decided where the reopen is applied, because the unique index decides it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from nexovue_platform.connections import SYNCED

from nexovue_scheduler.connections.trackers import (
    IssueGone,
    IssueMissing,
    IssueMoved,
    IssueRead,
)

OPEN = "open"
CLOSED = "closed"
INVESTIGATING = "investigating"
RESOLVED = "resolved"


class Action(Enum):
    NOTHING = "1: nothing changed"
    WRITE_WANTED = "2: write W to the tracker"
    ACCEPT_LANDED = "3: our earlier write landed"
    REOPEN_CASE = "4: reopened there, the case reopens"
    END_HOLD = "4b: reopened there during a hold"
    HOLD = "5a: closed there with the signal open, the case holds"
    RESOLVE_AFTER_HOLD = "5b: the hold is over, the case resolves"
    RESOLVE_CLOSED = "5c: closed there, the case resolves"
    RECORD_ONLY = "6: the case already matches, record what the tracker says"
    GONE = "8: deleted"
    GONE_RECHECK = "9: missing from a readable repository"
    PLACE_UNREACHABLE = "10: the repository no longer answers"
    MOVED = "11: moved"


@dataclass(frozen=True, slots=True)
class LinkView:
    role: str
    seen: str | None
    wanted: str | None
    # `tracker_closed_at` is set: closed there while the case signal was open.
    held: bool


@dataclass(frozen=True, slots=True)
class CaseView:
    # `investigating`, `resolved`, or None when the finding holds no case.
    state: str | None
    # Open if the root or any joined finding is open.
    signal_open: bool
    # False once a resolved case is past following (FR39, AC85).
    followed: bool


def compare(link: LinkView, tracker: str, case: CaseView) -> Action:
    """One row of the table, for a link whose issue was read as `tracker`."""
    if link.role != SYNCED:
        return Action.NOTHING if link.seen == tracker else Action.RECORD_ONLY
    if link.wanted is not None:
        return Action.ACCEPT_LANDED if tracker == link.wanted else Action.WRITE_WANTED
    active = case.state == INVESTIGATING
    if link.held:
        if tracker == OPEN:
            return Action.END_HOLD
        return Action.RESOLVE_AFTER_HOLD if active and not case.signal_open else Action.NOTHING
    if tracker == link.seen:
        return Action.NOTHING
    if tracker == OPEN and link.seen == CLOSED:
        reopens = case.state == RESOLVED and case.followed
        return Action.REOPEN_CASE if reopens else Action.RECORD_ONLY
    if tracker == CLOSED and link.seen == OPEN and active:
        return Action.HOLD if case.signal_open else Action.RESOLVE_CLOSED
    return Action.RECORD_ONLY


def compare_read(read: IssueRead) -> Action | None:
    """Rows 8 to 11, which the read itself decides; None for an issue read as a status."""
    if isinstance(read, IssueGone):
        return Action.GONE
    if isinstance(read, IssueMissing):
        return Action.GONE_RECHECK if read.repository_readable else Action.PLACE_UNREACHABLE
    if isinstance(read, IssueMoved):
        return Action.MOVED
    return None


def after(
    action: Action, link: LinkView, tracker: str, case: CaseView
) -> tuple[LinkView, CaseView, str]:
    """What an action leaves behind, for the table's own idempotence test (AC45)."""
    if action is Action.WRITE_WANTED:
        assert link.wanted is not None
        return replace(link, seen=link.wanted, wanted=None), case, link.wanted
    if action is Action.ACCEPT_LANDED:
        return replace(link, seen=tracker, wanted=None), case, tracker
    if action is Action.REOPEN_CASE:
        return replace(link, seen=tracker), replace(case, state=INVESTIGATING), tracker
    if action in (Action.END_HOLD, Action.RECORD_ONLY):
        return replace(link, seen=tracker, held=False), case, tracker
    if action is Action.HOLD:
        return replace(link, seen=tracker, held=True), case, tracker
    if action in (Action.RESOLVE_AFTER_HOLD, Action.RESOLVE_CLOSED):
        return replace(link, seen=tracker, held=False), replace(case, state=RESOLVED), tracker
    return link, case, tracker
