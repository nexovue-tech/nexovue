"""Detection and the alert evaluation sweeps never reach the release sync (F28 plan T1.12;
AC42's structure half, D51).

A release never opens, closes, mutes or delays a finding or an alert (BR12). The surest
way to keep it so is that nothing deciding one can call into `nexovue_scheduler.releases`
or read its tables, directly or through anything it imports, at module level or inside a
function. The walk reads source with `nexovue_platform.import_walk` and runs nothing.

The roots are every module of `detection`, and the alert modules that evaluate and
compose: `sweeps`, `thresholds`, `new_errors`, `silence`, `check_state`, `incidents`,
`rules`, `outbox` and `messages`. `alerts/delivery.py` and `alerts/tasks.py` stay outside
them by name: FR44's line is read at delivery, after the claim commits (D51, Step 3).
"""

from __future__ import annotations

import re
from collections import deque
from typing import Final

from nexovue_platform.import_walk import imports_of, module_file, package_modules

FORBIDDEN_PACKAGE: Final = "nexovue_scheduler.releases"
ALERT_ROOTS: Final = tuple(
    f"nexovue_scheduler.alerts.{name}"
    for name in (
        "sweeps",
        "thresholds",
        "new_errors",
        "silence",
        "check_state",
        "incidents",
        "rules",
        "outbox",
        "messages",
    )
)
OUTSIDE_THE_ROOTS: Final = ("nexovue_scheduler.alerts.delivery", "nexovue_scheduler.alerts.tasks")
RELEASE_TABLES: Final = ("service_versions_1m", "releases", "release_periods", "release_sync_state")
# Upper-case keywords, as the repository writes SQL: prose saying "from releases" is no read.
_READS_A_RELEASE_TABLE: Final = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO)\s+(" + "|".join(RELEASE_TABLES) + r")\b"
)


def _roots() -> list[str]:
    return [*package_modules("nexovue_scheduler.detection"), *ALERT_ROOTS]


def _parents(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:end]) for end in range(1, len(parts) + 1)]


def reachable_from(start: list[str]) -> dict[str, str | None]:
    """Every repository module `start` can reach, with the module that first reached it."""
    came_from: dict[str, str | None] = dict.fromkeys(start)
    queue = deque(start)
    while queue:
        module = queue.popleft()
        path = module_file(module)
        if path is None:
            continue
        for imported in sorted(imports_of(module, path)):
            for step in _parents(imported):
                if step not in came_from and module_file(step) is not None:
                    came_from[step] = module
                    queue.append(step)
    return came_from


def _chain(module: str, came_from: dict[str, str | None]) -> str:
    chain = [module]
    while (parent := came_from[chain[-1]]) is not None:
        chain.append(parent)
    return " <- ".join(chain)


def test_the_roots_are_real_modules() -> None:
    """A walk from nothing passes everything."""
    roots = _roots()
    assert "nexovue_scheduler.detection.project_job" in roots
    for root in [*roots, *OUTSIDE_THE_ROOTS]:
        assert module_file(root) is not None, root
    assert module_file(FORBIDDEN_PACKAGE) is not None


def test_no_root_reaches_the_release_sync() -> None:
    came_from = reachable_from(_roots())
    reached = sorted(
        _chain(module, came_from)
        for module in came_from
        if module == FORBIDDEN_PACKAGE or module.startswith(f"{FORBIDDEN_PACKAGE}.")
    )
    assert not reached, f"detection or an alert sweep reaches the release sync: {reached}"


def test_no_module_a_root_reaches_reads_a_release_table() -> None:
    came_from = reachable_from(_roots())
    reading = []
    for module in sorted(came_from):
        path = module_file(module)
        if path is None:
            continue
        tables = set(_READS_A_RELEASE_TABLE.findall(path.read_text(encoding="utf-8")))
        if tables:
            reading.append(f"{sorted(tables)} in {_chain(module, came_from)}")
    assert not reading, f"detection or an alert sweep reads a release table: {reading}"


def test_delivery_and_the_task_module_stay_outside_the_roots() -> None:
    """D51: FR44's read sits in delivery precisely because no root reaches it."""
    came_from = reachable_from(_roots())
    assert not set(OUTSIDE_THE_ROOTS) & set(came_from)


def test_the_walk_sees_an_import_inside_a_function() -> None:
    """The negative control: a call into the sync from inside a function is caught."""
    worker = "nexovue_scheduler.jobs.worker"
    came_from = reachable_from([worker])
    assert FORBIDDEN_PACKAGE in came_from, "the walk missed the worker's import of releases"
