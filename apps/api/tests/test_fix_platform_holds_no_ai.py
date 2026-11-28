"""F5's rules hold no AI and do no I/O (F5 plan T2.9; design *The rules*, D18).

The rules run in three rooms: the AI job, the API and the connections room. They are
what stands between a model's answer and a person or a repository, so they must not
depend on the AI code they judge, and must reach nothing outside the process: a rule
that could call out could be made to.

**The AI half is already held** by `test_fix_facts_hold_no_ai.py` (T1.5), which walks
the whole `nexovue_platform.fixes` package against the shared deny-list, where
`nexovue_platform.ai` is named (F3 T1.8). This suite does not repeat that walk. It adds
what T1.5 does not cover: that the walk includes every Step 2 module, and that none of
them, nor anything they import from this repository, imports a network client or starts
a process. The driver half is `test_cross_service_contracts.py`'s.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from nexovue_platform.import_walk import (
    NETWORK_CLIENTS,
    denied,
    direct_imports_matching,
    imports_of,
    module_file,
    package_modules,
)

FIXES_PACKAGE: Final = "nexovue_platform.fixes"
THE_RULES: Final = (
    "nexovue_platform.fixes.policy",
    "nexovue_platform.fixes.checks",
    "nexovue_platform.fixes.diff",
    "nexovue_platform.fixes.patterns",
    "nexovue_platform.fixes.secrets",
    "nexovue_platform.fixes.prose",
    "nexovue_platform.fixes.statements",
)
# Starting a process or touching files is I/O as much as a socket is.
PROCESS_AND_FILES: Final = frozenset({"subprocess", "os", "shutil", "pathlib", "tempfile"})


def reachable_repository_modules(start: list[str]) -> list[str]:
    """`start` and every module of this repository it imports, transitively."""
    seen, queue = dict.fromkeys(start), list(start)
    while queue:
        name = queue.pop()
        path = module_file(name)
        if path is None:
            continue
        for imported in imports_of(name, path):
            if imported not in seen and module_file(imported) is not None:
                seen[imported] = None
                queue.append(imported)
    return sorted(seen)


def test_every_step_two_module_is_in_the_package_walk() -> None:
    assert set(THE_RULES) <= set(package_modules(FIXES_PACKAGE))


def test_the_rules_import_no_network_client_process_or_file_access() -> None:
    modules = reachable_repository_modules(package_modules(FIXES_PACKAGE))

    assert "nexovue_platform.evidence.aliases" in modules  # the walk leaves the package
    assert direct_imports_matching(modules, NETWORK_CLIENTS | PROCESS_AND_FILES) == []


def test_the_control_an_ai_import_in_a_rules_module_would_be_denied(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text("from nexovue_platform.ai import provider\nimport httpx\n")
    found = imports_of("nexovue_platform.fixes.planted", planted)

    assert any(denied(name) for name in found)
    assert "httpx" in found and "httpx" in NETWORK_CLIENTS


def test_the_control_the_rules_are_read_from_source() -> None:
    path = module_file("nexovue_platform.fixes.checks")

    assert path is not None
    assert isinstance(ast.parse(path.read_text(encoding="utf-8")), ast.Module)
