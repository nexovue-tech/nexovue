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
