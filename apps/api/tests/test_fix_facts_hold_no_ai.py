"""Rungs 1 and 2 import no AI module (plan T1.5; spec BR12, the import half of AC33).

The deploy line and the brief must work with AI off, stopped or failing, so nothing they
run may reach a provider SDK or one of this repository's AI modules. Walked through the
repository's one walk and one deny-list (`nexovue_platform.import_walk`; foundation
section 1). The control proves the walk finds an AI import when one is there. Its limit
carries over: a provider reached by plain HTTP is not visible to an import walk.
`lifespan.py` already types the MCP server as a Protocol (F19 D55, F20 D38), so the walk
from an API route needs no exception.
"""

from __future__ import annotations

from typing import Final

from nexovue_platform.import_walk import ai_imports_reachable_from, package_modules

# Rung 1's additions and the brief, the route that serves them, and F5's shared package.
RUNG_ONE_AND_TWO: Final = (
    "nexovue_api.findings.fix_facts",
    "nexovue_api.findings.fix_read",
    "nexovue_api.findings.fix_schemas",
    "nexovue_api.findings.router",
)
FIXES_PACKAGE: Final = "nexovue_platform.fixes"
# The MCP server, built on the `mcp` SDK: an AI module of this repository.
CONTROL: Final = "nexovue_api.assistant.server"


def test_the_walk_starts_from_the_brief_and_the_deploy_line() -> None:
    modules = package_modules(FIXES_PACKAGE)

    assert "nexovue_platform.fixes.brief" in modules


def test_ac33_rungs_one_and_two_reach_no_ai_module() -> None:
    modules = [*RUNG_ONE_AND_TWO, *package_modules(FIXES_PACKAGE)]

    assert ai_imports_reachable_from(modules) == []


def test_ac33_the_control_is_found() -> None:
    reached = ai_imports_reachable_from([CONTROL])

    assert reached, "the walk found no AI import from the MCP server; it is not walking"
    assert any(chain.startswith("mcp") for chain in reached)
