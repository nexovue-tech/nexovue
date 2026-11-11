"""The dated record of real calls, one per wire format (spec AC77; plan T4.10).

A format is offered on `/settings/ai` only once it has completed one real call, reporting
token counts, recorded here with its date. **None has yet** (2026-11-11): each needs a real
account, and the founder's free-tier key is foundation item 4. `tools/ai-eval/real_call.py`
makes the call and prints the line to add here, by hand, in the change that records it, so a
record is reviewed like any other code.

Until a line is added, the screen offers no format, and says why.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final


@dataclass(frozen=True, slots=True)
class RealCall:
    wire_format: str
    on: date
    # Which provider and model answered, as typed, so the record says what it proves.
    provider: str
    model: str
    input_tokens: int
    output_tokens: int


REAL_CALLS: Final[tuple[RealCall, ...]] = ()


def formats_with_a_real_call(records: tuple[RealCall, ...] = REAL_CALLS) -> tuple[str, ...]:
    """The formats the screen may offer, in the order first recorded."""
    seen: list[str] = []
    for record in records:
        if record.input_tokens > 0 and record.wire_format not in seen:
            seen.append(record.wire_format)
    return tuple(seen)
