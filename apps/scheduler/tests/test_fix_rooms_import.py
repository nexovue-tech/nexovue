"""Each room holds only its own code (F5 plan T3.15; spec AC5, AC6b).

- **AC5:** from what `scheduler-connections` loads, no import reaches `nexovue_platform.ai`
  or `nexovue_scheduler.insight`; from what `scheduler-insight` loads, none reaches
  `nexovue_scheduler.connections` or `fix_github`. Its stated limit: a provider reached by
  plain HTTP is invisible to an import walk; the connections room's HTTP goes through the
  guarded client to GitHub's hosts only (`test_fix_github_client.py`).
- **AC6b:** only `scheduler-connections` registers `connections.*` and `fixes.sweep_fixes`;
  the detection and alert roles' set (`TASK_MODULES`) holds neither. A task in the wrong
  set is claimed and fails, every interval, for ever.
- **AC4** is held by `test_compose_contract.py`: `scheduler-connections` holds exactly its
  own variables (no AI, check, email or object-store key) and only it and the API hold the
  connection key.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from nexovue_platform.import_walk import imports_of, module_file

from nexovue_scheduler.jobs import registry

F5_CONNECTIONS_MODULES: Final = (
    "nexovue_scheduler.connections.source_pack",
    "nexovue_scheduler.connections.fix_app",
    "nexovue_scheduler.connections.fix_link",
    "nexovue_scheduler.connections.fix_github",
    "nexovue_scheduler.connections.fix_sweep",
)


def _reachable(start: tuple[str, ...]) -> set[str]:
    """Every repository module reachable by import from `start`."""
    seen: set[str] = set()
    queue = list(start)
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        path = module_file(name)
        if path is None:
            continue
        queue.extend(imports_of(name, path))
    return seen


def _reaches(modules: set[str], package: str) -> list[str]:
    return sorted(name for name in modules if name == package or name.startswith(package + "."))


def test_ac5_the_connections_room_reaches_no_ai_code() -> None:
    reached = _reachable((*registry.TASK_SETS["connections"], *F5_CONNECTIONS_MODULES))

    assert "nexovue_scheduler.connections.source_pack" in reached
    assert _reaches(reached, "nexovue_platform.ai") == []
    assert _reaches(reached, "nexovue_scheduler.insight") == []


def test_ac5_the_ai_room_reaches_no_repository_code() -> None:
    reached = _reachable(registry.TASK_SETS["insight"])

    assert "nexovue_scheduler.insight.suggest_general_fix" in reached
    assert _reaches(reached, "nexovue_scheduler.connections") == []


def test_ac5_the_walk_finds_an_import_when_one_is_there() -> None:
    reached = _reachable(("nexovue_scheduler.connections.fix_app",))

    assert "nexovue_scheduler.connections.fix_github" in reached


def test_ac6b_only_the_connections_set_holds_rung_3_s_tasks() -> None:
    for module in F5_CONNECTIONS_MODULES[:3] + F5_CONNECTIONS_MODULES[4:]:
        assert module in registry.TASK_SETS["connections"], module
        assert module not in registry.TASK_MODULES, module
        assert module not in registry.TASK_SETS["insight"], module


def test_ac6b_rung_2b_s_light_sweep_holds_no_repository_code() -> None:
    path = Path(registry.__file__).parents[1] / "fixes" / "sweep.py"
    imported = {
        node.module
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not any(name.startswith("nexovue_scheduler.connections") for name in imported)
