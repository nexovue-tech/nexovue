"""Whether a suggestion may ever be written to a customer's repository (spec, founder
decision 4; plan T6.5; foundation item 33; ADR-097).

**Off.** Steps 7 and 8 (the draft pull request and Run CI) are built against the
simulated GitHub with write access switched off here, in code. Every write path reads
`write_access_on()` and does nothing while it is False. No setting, environment
variable or database row can turn it on: only a reviewed commit that sets
`WRITE_ACCESS_GO` to the founder's recorded go, and records the same go in
`docs/features/ai-solution/status.md` (`test_fix_write_gate.py` holds the two equal).

The go names what T6.5 requires, so it cannot be written without them: the AI options
whose AC21 safety gates passed, the buyers' answer (spec D57), the no-warranty terms
(spec, founder decision 1), and his answers on a draft from an error stack alone (D58)
and on GitHub Enterprise Server before Step 7.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final


@dataclass(frozen=True, slots=True)
class WriteGo:
    """The founder's written go on write access, as T6.5 records it."""

    given_on: date
    # The named AI options that passed AC21's safety gates in the evaluation report.
    safety_gates_passed_on: tuple[str, ...]
    # Where the buyers' answer on write access is recorded (spec D57).
    buyers_answer: str
    # Where the no-warranty terms are (spec, founder decision 1).
    terms: str
    # His answer on opening a draft from an error stack alone (D58); False keeps it narrow.
    drafts_from_stack_alone: bool
    # His answer on moving GitHub Enterprise Server before Step 7 (D57).
    enterprise_server_before_step_7: bool

    def __post_init__(self) -> None:
        if not self.safety_gates_passed_on or not all(
            option.strip() for option in self.safety_gates_passed_on
        ):
            raise ValueError("a go names at least one AI option that passed AC21's safety gates")
        if not self.buyers_answer.strip():
            raise ValueError("a go names where the buyers' answer is recorded")
        if not self.terms.strip():
            raise ValueError("a go names the no-warranty terms")


# None until the founder's go is recorded at T6.5. Changed only by hand, in review.
WRITE_ACCESS_GO: Final[WriteGo | None] = None


def write_access_on(go: WriteGo | None = WRITE_ACCESS_GO) -> bool:
    """True only once the founder's go is recorded. Steps 7 and 8 read this first."""
    return go is not None
