"""Write access stays off in code until the founder's recorded go (F5 plan T6.5; foundation
item 33; ADR-097).

The go and `status.md` move together: a go set in code with the status still reading
"not given", or the reverse, fails here, so neither can change alone.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from nexovue_platform.fixes import write_gate
from nexovue_platform.fixes.write_gate import WRITE_ACCESS_GO, WriteGo, write_access_on

STATUS = Path(__file__).resolve().parents[3] / "docs" / "features" / "ai-solution" / "status.md"
NOT_GIVEN = "**T6.5, the founder's go on write access: not given.**"


def _go(**overrides: Any) -> WriteGo:
    values: dict[str, Any] = {
        "given_on": date(2027, 1, 4),
        "safety_gates_passed_on": ("A named option",),
        "buyers_answer": "status.md, the buyer round",
        "terms": "docs/legal/no-warranty.md",
        "drafts_from_stack_alone": False,
        "enterprise_server_before_step_7": False,
    }
    return WriteGo(**(values | overrides))


def test_write_access_is_off_until_the_founders_go() -> None:
    assert WRITE_ACCESS_GO is None
    assert write_access_on() is False


def test_a_recorded_go_turns_it_on() -> None:
    assert write_access_on(_go()) is True


@pytest.mark.parametrize(
    "missing",
    [
        {"safety_gates_passed_on": ()},
        {"safety_gates_passed_on": ("  ",)},
        {"buyers_answer": " "},
        {"terms": ""},
    ],
    ids=["no_option", "blank_option", "no_buyers_answer", "no_terms"],
)
def test_a_go_cannot_be_written_without_what_t6_5_requires(missing: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="a go names"):
        _go(**missing)


def test_status_records_the_gate_as_the_code_holds_it() -> None:
    recorded_not_given = NOT_GIVEN in STATUS.read_text(encoding="utf-8")

    assert recorded_not_given == (write_gate.WRITE_ACCESS_GO is None), (
        "write_gate.WRITE_ACCESS_GO and status.md's T6.5 line must change together"
    )
