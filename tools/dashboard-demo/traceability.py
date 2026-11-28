"""Every FR, NFR and AC in F20's spec appears in the plan's Traceability tables (plan T11.7).

Ids named in the plan's ranges ("AC10 to AC13") are not expanded: the plan lists ids one by one for
acceptance criteria, and writes "FR5 ... T5.1a to T5.2" ranges only for tasks.

    uv run python tools/dashboard-demo/traceability.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Final

DASHBOARDS: Final = Path(__file__).resolve().parents[2] / "docs" / "features" / "dashboards"
ID: Final = re.compile(r"\b(?:AC|FR|NFR)\d+\b")


def ids_in(text: str) -> set[str]:
    return set(ID.findall(text))


def missing(spec: str, plan: str) -> list[str]:
    declared = set(re.findall(r"^- \*\*((?:AC|FR|NFR)\d+)[.\s*]", spec, flags=re.MULTILINE))
    traced = ids_in(plan.split("## Traceability", 1)[1].split("## New risks", 1)[0])
    return sorted(
        declared - traced,
        key=lambda name: (name.rstrip("0123456789"), int(re.sub(r"\D", "", name))),
    )


def main() -> int:
    spec = (DASHBOARDS / "spec.md").read_text(encoding="utf-8")
    plan = (DASHBOARDS / "plan.md").read_text(encoding="utf-8")
    declared = len(set(re.findall(r"^- \*\*((?:AC|FR|NFR)\d+)[.\s*]", spec, flags=re.MULTILINE)))
    gaps = missing(spec, plan)
    print(
        f"{declared} requirements declared in spec.md; {len(gaps)} not in the plan's traceability."
    )
    for name in gaps:
        print("  missing:", name)
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
