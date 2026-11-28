"""The run's stored facts: one JSON Lines file, so `rules.decide` can be replayed offline (AC62).

Line one names the tune and report rounds, before any decision is written; every other line is one
`Decision`. A log with no rounds line, a second one, or a decision in an unnamed round is refused
when read, so a run cannot be scored with its rounds chosen afterwards.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from bed_facts import facts_from_json, facts_to_json
from score import Decision, Planted, RoundPlan

ROUNDS: str = "rounds"
DECISION: str = "decision"


def _append(path: Path, line: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(line, separators=(",", ":")) + "\n")


def start(path: Path, plan: RoundPlan) -> None:
    """Name the rounds. Refuses a file that already holds anything."""
    if path.exists() and path.stat().st_size > 0:
        raise ValueError(f"{path} already holds a run: rounds are named once, before it starts")
    _append(path, {"kind": ROUNDS, "tune": sorted(plan.tune), "report": sorted(plan.report)})


def append_decision(path: Path, decision: Decision) -> None:
    _append(
        path,
        {
            "kind": DECISION,
            "fault": decision.fault,
            "service": decision.service,
            "round": decision.round,
            "finding_opened": decision.finding_opened,
            "facts": None if decision.facts is None else facts_to_json(decision.facts),
            "planted": [
                {
                    "service": p.service,
                    "version": p.version,
                    "at": p.at.isoformat(),
                    "role": p.role,
                }
                for p in decision.planted
            ],
        },
    )


def _decision(line: dict[str, Any]) -> Decision:
    return Decision(
        fault=str(line["fault"]),
        service=str(line["service"]),
        round=int(line["round"]),
        finding_opened=bool(line["finding_opened"]),
        facts=None if line["facts"] is None else facts_from_json(line["facts"]),
        planted=tuple(
            Planted(p["service"], p["version"], datetime.fromisoformat(p["at"]), p["role"])
            for p in line["planted"]
        ),
    )


def lines(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as log:
        for number, text in enumerate(log, start=1):
            if not text.strip():
                continue
            try:
                line = json.loads(text)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{number} is not JSON: {error}") from error
            if not isinstance(line, dict) or "kind" not in line:
                raise ValueError(f"{path}:{number} is not a log line")
            yield line


def read(path: Path) -> tuple[RoundPlan, list[Decision]]:
    plan: RoundPlan | None = None
    decisions: list[Decision] = []
    for line in lines(path):
        if line["kind"] == ROUNDS:
            if plan is not None or decisions:
                raise ValueError("the rounds are named once, on the first line")
            plan = RoundPlan(frozenset(line["tune"]), frozenset(line["report"]))
        elif line["kind"] == DECISION:
            if plan is None:
                raise ValueError("a decision before the rounds were named")
            decision = _decision(line)
            plan.kind_of(decision.round)
            decisions.append(decision)
        else:
            raise ValueError(f"unknown log line {line['kind']!r}")
    if plan is None:
        raise ValueError("the log names no rounds")
    return plan, decisions
