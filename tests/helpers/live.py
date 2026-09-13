"""Guard for the suites that need the dev stack.

A suite that skips itself when the stack is unreachable reports exactly the same
green as a suite that ran and held. For tenant isolation that is the worst
possible ambiguity: it is a Phase 1 invariant and P1-25's exit criterion, so
"nobody checked" must not look like "customers cannot see each other's data".

These suites therefore fail when the stack is absent. Skipping stays available,
but only when a person asks for it by name.
"""

from __future__ import annotations

import os
from typing import Final

import pytest

# Set this to run the offline gates alone — lint, types and every test that
# needs nothing running. Deliberate, and visible in the command that set it.
SKIP_VARIABLE: Final = "NEXOVUE_SKIP_LIVE_TESTS"

# The opt-in for a suite that spends something real. See `require_opt_in`.
OPT_IN_VARIABLE: Final = "NEXOVUE_BILLABLE_LIVE_TESTS"


def require_stack(*, reachable: bool, suite: str, remedy: str) -> None:
    """Refuse to report green on a suite that never ran.

    Call at module level, before any test is collected.
    """
    if reachable:
        return
    if os.environ.get(SKIP_VARIABLE) == "1":
        pytest.skip(
            f"{SKIP_VARIABLE}=1: {suite} was skipped on purpose",
            allow_module_level=True,
        )
    raise RuntimeError(
        f"{suite} cannot run: {remedy}. It is not skipped, because a skip here "
        f"would be indistinguishable from a pass. Run it, or set "
        f"{SKIP_VARIABLE}=1 to leave it out on purpose."
    )


def require_opt_in(*, suite: str, cost: str) -> None:
    """Refuse to run a suite that spends a metered third-party quota, unless asked.

    `require_stack` answers "can this suite run?". This answers a different
    question it does not: **should it run now?**

    Most live suites here cost nothing to repeat — ClickHouse and Postgres are
    ours, and running them a hundred times costs a hundred times nothing. A
    suite that calls a metered provider is not like that: every run is spent,
    and a gate people run on every change spends it on every change. The mail
    suite sent two messages per `just check`; forty runs in one working session
    consumed most of a month's allowance, and nothing in the design of the gate
    was counting.

    So the default is off, and the skip is loud rather than silent — it names
    the suite, what it costs, and how to ask for it. That keeps the rule the
    module docstring exists for: a suite you *asked* for never skips quietly.
    Ask for it and it still fails when it cannot run.

    Call at module level, before `require_stack`.
    """
    if os.environ.get(OPT_IN_VARIABLE) == "1":
        return
    pytest.skip(
        f"{suite} is not run by default: {cost}. Set {OPT_IN_VARIABLE}=1 to run "
        f"it — `just test-billable` does. It belongs in a release check rather "
        f"than in a gate that runs on every change.",
        allow_module_level=True,
    )
