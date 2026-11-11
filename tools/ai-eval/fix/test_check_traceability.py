"""The traceability check (F5 plan T2.11): the real documents pass, and fixture
documents fail on a missing row, an unknown task, an unplanned criterion and a lone
half."""

from __future__ import annotations

from fix.check_traceability import PLAN, SPEC, main, problems

SPEC_FIXTURE = """# Spec

- **FR1.** A thing.
- **BR1. Honest.** Words.
- **AC1.** Proved here.
- **AC2.** Proved here too.
- **AC3.** In the closing list.

### What proves what

| Requirement | Proved by | Task |
|---|---|---|
| FR1 | AC1, AC2 | T1.1 to T1.2 |
| BR1 | AC1 | T2b.1 |

Other criteria not named above, and their tasks: AC3 (T1.2).

## Dependencies
"""

PLAN_FIXTURE = """# Plan

- [x] **T1.1** — code · proved by a test (AC1)
- [ ] **T1.2** — code · proved by a test (AC2, AC3's read half)
- [ ] **T2b.1** — code · proved by a test (AC3's write half)
- **T1.3** moved to Step 2. The number is not reused.
"""


def test_the_real_documents_pass() -> None:
    assert problems(SPEC.read_text(encoding="utf-8"), PLAN.read_text(encoding="utf-8")) == []
    assert main([]) == 0


def test_the_fixture_documents_pass_as_written() -> None:
    assert problems(SPEC_FIXTURE, PLAN_FIXTURE) == []


def test_a_requirement_with_no_row_fails() -> None:
    spec = SPEC_FIXTURE.replace("| BR1 | AC1 | T2b.1 |\n", "")

    assert problems(spec, PLAN_FIXTURE) == [
        "BR1 has no row in *What proves what* or its closing list"
    ]


def test_a_criterion_missing_from_the_table_and_the_list_fails() -> None:
    spec = SPEC_FIXTURE.replace("AC3 (T1.2)", "AC9 (T1.2)")

    assert "AC3 has no row in *What proves what* or its closing list" in problems(
        spec, PLAN_FIXTURE
    )


def test_an_unknown_task_fails() -> None:
    spec = SPEC_FIXTURE.replace("T2b.1 |", "T2b.7 |")

    assert problems(spec, PLAN_FIXTURE) == [
        "T2b.7 is named in *What proves what* but is not in plan.md"
    ]


def test_a_task_range_is_read_whole() -> None:
    """`T1.1 to T1.3` names T1.2 too, though only its ends are written."""
    spec = SPEC_FIXTURE.replace("T1.1 to T1.2", "T1.1 to T1.3").replace("(T1.2)", "(T1.1)")
    plan = PLAN_FIXTURE.replace("**T1.2**", "**T1.8**")

    assert problems(spec, plan) == ["T1.2 is named in *What proves what* but is not in plan.md"]


def test_a_criterion_no_task_names_fails() -> None:
    plan = PLAN_FIXTURE.replace("(AC2, AC3's read half)", "(AC3's read half)")

    assert problems(SPEC_FIXTURE, plan) == ["AC2 is named by no task in plan.md"]


def test_a_lone_half_fails() -> None:
    plan = PLAN_FIXTURE.replace("(AC3's write half)", "(nothing)")

    assert problems(SPEC_FIXTURE, plan) == ["AC3 is named only as a half, by T1.2 alone"]


def test_the_half_of_form_is_read_too() -> None:
    plan = PLAN_FIXTURE.replace("(AC3's write half)", "(nothing)").replace(
        "AC3's read half", "the read half of AC3"
    )

    assert problems(SPEC_FIXTURE, plan) == ["AC3 is named only as a half, by T1.2 alone"]


def test_a_half_with_the_whole_named_elsewhere_passes() -> None:
    plan = PLAN_FIXTURE.replace("(AC3's write half)", "(AC3)")

    assert problems(SPEC_FIXTURE, plan) == []
