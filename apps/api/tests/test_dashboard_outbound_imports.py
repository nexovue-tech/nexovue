"""Dashboards open no connection out of the install (F20, T2.19, AC142's structural half,
NFR13).

No module in either package imports a network client directly. What the browser asks
for is the other half, a browser check (plan T6.10). An import walk cannot see a client
reached through another module, which the reviewer catches.
"""

from __future__ import annotations

from nexovue_platform.import_walk import NETWORK_CLIENTS, direct_imports_matching, package_modules

DASHBOARD_PACKAGES = ("nexovue_api.dashboards", "nexovue_api.dashboardpanels")


def test_ac142_no_dashboard_module_imports_a_network_client() -> None:
    modules = [module for package in DASHBOARD_PACKAGES for module in package_modules(package)]

    assert len(modules) > 10, "the packages were not found"
    assert direct_imports_matching(modules, NETWORK_CLIENTS) == []


def test_the_check_finds_a_client_where_one_is_imported() -> None:
    """A control: the mail sender reaches its provider over HTTP."""
    found = direct_imports_matching(package_modules("nexovue_platform.email"), NETWORK_CLIENTS)

    assert found
