"""What a confirmed run does to a check's state.

Pure, so the rules can be read and tested without a database: which outcomes are
states at all, when one replaces another, and when a run changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

# The states a run can confirm. A prober error is ours and says nothing about
# the customer's service, so it is not one of them (BR7).
STATE_OUTCOMES: Final = frozenset({"up", "degraded", "down", "misconfigured"})


@dataclass(frozen=True, slots=True)
class CurrentState:
    state: str
    last_run_at: datetime


@dataclass(frozen=True, slots=True)
class Decision:
    """What recording one run does."""

    record: bool
    # The state to change to, or None when it stays as it is.
    change_to: str | None


def decide(current: CurrentState | None, outcome: str, observed_at: datetime) -> Decision:
    """A run that arrives after a newer one has been recorded changes nothing: a
    retry delivered late must not overwrite what a later run already confirmed."""
    if outcome not in STATE_OUTCOMES:
        return Decision(record=False, change_to=None)
    if current is None:
        return Decision(record=True, change_to=outcome)
    if observed_at < current.last_run_at:
        return Decision(record=False, change_to=None)
    return Decision(record=True, change_to=None if current.state == outcome else outcome)
