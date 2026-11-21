"""How a connection's state moves after a call (F22 AC79; spec, *Link and connection
states*). Pure, so each move is one named test.

A call that works makes a live connection `working`. A failure a person must fix needs a
person (`needs_attention`). A failure that may pass counts up, and a working connection
becomes `failing` only after `FAILURES_BEFORE_FAILING` in a row. During setup the state
stays `setting_up` whatever failed: the setup screen names the step to take.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from nexovue_platform.connections import (
    CONNECTION_FAILURES,
    DISCONNECTED,
    FAILING,
    FAILURES_BEFORE_FAILING,
    NEEDS_ATTENTION,
    SETTING_UP,
    WORKING,
)

# A person must act: a key GitHub refuses, an App installed nowhere or twice, a key this
# install cannot open, a setup GitHub refused or let expire.
LASTING_FAILURES: Final = (
    "unauthorized",
    "app_not_installed",
    "installation_ambiguous",
    "no_repository",
    "key_unreadable",
    "manifest_expired",
    "manifest_refused",
)
PASSING_FAILURES: Final = tuple(
    failure for failure in CONNECTION_FAILURES if failure not in LASTING_FAILURES
)


@dataclass(frozen=True, slots=True)
class Standing:
    """The columns a call's outcome writes on `connections`."""

    state: str
    consecutive_failures: int
    failing_since: datetime | None
    last_failure: str | None
    last_failure_status: int | None
    # Set only by a success; None leaves the stored value as it is.
    last_ok_at: datetime | None = None


def after_success(before: Standing, *, now: datetime) -> Standing:
    _refuse_disconnected(before)
    return Standing(
        state=WORKING,
        consecutive_failures=0,
        failing_since=None,
        last_failure=None,
        last_failure_status=None,
        last_ok_at=now,
    )


def after_failure(before: Standing, failure: str, status: int | None, *, now: datetime) -> Standing:
    _refuse_disconnected(before)
    failures = before.consecutive_failures + 1
    counted = replace(
        before,
        consecutive_failures=failures,
        failing_since=before.failing_since or now,
        last_failure=failure,
        last_failure_status=status,
        last_ok_at=None,
    )
    if before.state == SETTING_UP:
        return counted
    if failure in LASTING_FAILURES:
        return replace(counted, state=NEEDS_ATTENTION)
    if before.state == WORKING and failures >= FAILURES_BEFORE_FAILING:
        return replace(counted, state=FAILING)
    return counted


def _refuse_disconnected(before: Standing) -> None:
    if before.state == DISCONNECTED:
        raise ValueError("a disconnected connection has no calls to judge")
