"""Every F5 requirement has a proof and a task, and every task named exists (plan T2.11;
spec *What proves what*).

Reads `spec.md` and `plan.md` as text and fails when:

1. an FR, UR, BR, NFR or AC defined in the spec has no row in *What proves what*, nor a
   place in its closing list of "Other criteria";
2. a task named in that table or list is not in the plan;
3. an acceptance criterion is named by no plan task at all;
4. a criterion is named by plan tasks only as a "half" (`AC10a's read half`, `the
   import half of AC33`), and by one task alone, so its other half has no task.

**It reads names, and cannot show that a task is adequate.** A criterion split into
halves across two tasks passes even if the halves do not add up to the whole.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Final

FEATURE: Final = Path(__file__).resolve().parents[3] / "docs" / "features" / "ai-solution"
SPEC: Final = FEATURE / "spec.md"
PLAN: Final = FEATURE / "plan.md"

_ID: Final = r"(?:FR|UR|BR|NFR|AC)[0-9]+[a-z]?"
_DEFINED: Final = re.compile(rf"^- \*\*({_ID})\.", re.MULTILINE)
_ANY_ID: Final = re.compile(rf"\b{_ID}\b")
_CRITERION: Final = re.compile(r"\bAC[0-9]+[a-z]?\b")
_TASK: Final = re.compile(r"\bT([0-9]+b?)\.([0-9]+)\b")
_TASK_RANGE: Final = re.compile(r"\bT([0-9]+b?)\.([0-9]+) to T([0-9]+b?)\.([0-9]+)\b")
_PLAN_TASK: Final = re.compile(r"^- (?:\[[ x]\] )?\*\*(T[0-9]+b?\.[0-9]+)\*\*(.*)$", re.MULTILINE)
_HALF: Final = re.compile(r"\b(AC[0-9]+[a-z]?)'s (?:[\w-]+ )?half\b|\bhalf of (AC[0-9]+[a-z]?)\b")
_TABLE_HEADING: Final = "### What proves what"
_CLOSING_LIST: Final = "Other criteria not named above"


@dataclass(frozen=True, slots=True)
class Proofs:
    requirements: frozenset[str]
    criteria: frozenset[str]
    tasks: frozenset[str]


def defined_ids(spec: str) -> list[str]:
    return list(dict.fromkeys(_DEFINED.findall(spec)))


def _tasks_in(text: str) -> set[str]:
    """Task ids in `text`, with a range such as `T3.8 to T3.12` read as every task in it."""
    found = {f"T{step}.{number}" for step, number in _TASK.findall(text)}
    for first_step, first, last_step, last in _TASK_RANGE.findall(text):
        if first_step == last_step:
            found |= {f"T{first_step}.{n}" for n in range(int(first), int(last) + 1)}
    return found


def proofs(spec: str) -> Proofs:
    """The ids and tasks *What proves what* names, in its table and its closing list."""
    start = spec.index(_TABLE_HEADING)
    end = spec.index("\n## ", start)
    section = spec[start:end]
    requirements: set[str] = set()
    criteria: set[str] = set()
    tasks: set[str] = set()
    for line in section.splitlines():
        if line.startswith("|") and not line.startswith(("| Requirement", "|---")):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) == 3:
                requirements |= set(_ANY_ID.findall(cells[0]))
                criteria |= set(_CRITERION.findall(cells[1]))
                tasks |= _tasks_in(cells[2])
        elif line.startswith(_CLOSING_LIST):
            criteria |= set(_CRITERION.findall(line))
            tasks |= _tasks_in(line)
    return Proofs(frozenset(requirements), frozenset(criteria), frozenset(tasks))


def plan_tasks(plan: str) -> dict[str, str]:
    """Each task id in the plan, with its line's text."""
    return dict(_PLAN_TASK.findall(plan))


def problems(spec: str, plan: str) -> list[str]:
    found: list[str] = []
    proved = proofs(spec)
    tasks = plan_tasks(plan)
    for identifier in defined_ids(spec):
        place = proved.criteria if identifier.startswith("AC") else proved.requirements
        if identifier not in place:
            found.append(f"{identifier} has no row in *What proves what* or its closing list")
    for task in sorted(proved.tasks - set(tasks)):
        found.append(f"{task} is named in *What proves what* but is not in plan.md")
    whole: dict[str, set[str]] = defaultdict(set)
    halves: dict[str, set[str]] = defaultdict(set)
    for task, text in tasks.items():
        halved = {first or second for first, second in _HALF.findall(text)}
        for criterion in halved:
            halves[criterion].add(task)
        for criterion in set(_CRITERION.findall(_HALF.sub(" ", text))):
            whole[criterion].add(task)
    for criterion in [each for each in defined_ids(spec) if each.startswith("AC")]:
        if not whole[criterion] and not halves[criterion]:
            found.append(f"{criterion} is named by no task in plan.md")
        elif not whole[criterion] and len(halves[criterion]) == 1:
            [task] = halves[criterion]
            found.append(f"{criterion} is named only as a half, by {task} alone")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F5's traceability check (plan T2.11).")
    parser.add_argument("--spec", type=Path, default=SPEC)
    parser.add_argument("--plan", type=Path, default=PLAN)
    arguments = parser.parse_args(argv)
    found = problems(
        arguments.spec.read_text(encoding="utf-8"), arguments.plan.read_text(encoding="utf-8")
    )
    for problem in found:
        print(problem)
    print(
        f"{len(defined_ids(arguments.spec.read_text(encoding='utf-8')))} ids read; "
        f"{len(found)} problems"
    )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
