"""No dashboard module reaches any AI module or provider SDK (F20, T2.17, AC53, NFR8).

Telemetry is attacker-controlled text, and it reaches dashboards as service names and
error titles. No AI reads them here, and this keeps it so, through the repository's one
walk and one deny-list (`nexovue_platform.import_walk`; foundation section 1). The
control proves the walk finds an AI import when one is there. Its limit carries over:
a provider reached by plain HTTP is not visible to an import walk. F4's
`TICKET_HANDLERS` does not exist on this branch; once F4 merges, this suite adds that no
dashboard handler is in it.
"""

from __future__ import annotations

from typing import Final

from nexovue_platform.import_walk import ai_imports_reachable_from, package_modules

DASHBOARD_PACKAGES: Final = ("nexovue_api.dashboards", "nexovue_api.dashboardpanels")
# The MCP server, built on the `mcp` SDK: an AI module of this repository.
CONTROL: Final = "nexovue_api.assistant.server"


def test_the_walk_starts_from_every_dashboard_module() -> None:
    modules = [module for package in DASHBOARD_PACKAGES for module in package_modules(package)]

    assert "nexovue_api.dashboards.router" in modules
    assert "nexovue_api.dashboardpanels.router" in modules
    assert "nexovue_api.dashboardpanels.shared_reads" in modules


def test_ac53_no_dashboard_module_reaches_an_ai_module() -> None:
    modules = [module for package in DASHBOARD_PACKAGES for module in package_modules(package)]

    assert ai_imports_reachable_from(modules) == []


def test_ac53_the_control_is_found() -> None:
    reached = ai_imports_reachable_from([CONTROL])

    assert reached, "the walk found no AI import from the MCP server; it is not walking"
    assert any(chain.startswith("mcp") for chain in reached)
