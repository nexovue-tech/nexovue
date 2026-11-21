"""Where GitHub is, and who may move it (F22 plan T4.5; AC89; design, *Test harness*).

A tracker's base address is a constructor argument only: `tracker_for` passes the
module's constant, and no setting or environment variable can point the adapter
elsewhere, so a planted variable cannot send a connection's token to another host. The
suites reach the simulated GitHub by building the adapter themselves, and the fault
hook the harness replaces does nothing in production. Jira's half is AC138, with Step 7.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

from nexovue_scheduler.connections import github, sync
from nexovue_scheduler.connections.trackers import StoredConnection, tracker_for

PACKAGE: Final = Path(github.__file__).parent
SOURCE: Final = PACKAGE.parent
TEST_ONLY: Final = ("tracker_harness", "tracker_fake", "recording_site")


def _stored() -> StoredConnection:
    return StoredConnection(
        kind="github",
        credential="not a key",
        github_app_id=1,
        github_installation_id=2,
        github_app_slug="nexovue-acme",
    )


@pytest.mark.parametrize(
    "variable",
    ["GITHUB_API", "GITHUB_API_URL", "NEXOVUE_GITHUB_API", "GITHUB_BASE_URL", "HTTPS_PROXY"],
)
def test_no_setting_moves_github(monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    monkeypatch.setenv(variable, "https://evil.example")
    tracker = tracker_for(_stored(), private_allowed=False)
    assert isinstance(tracker, github.GitHubTracker)
    assert tracker._app.endpoint.base_url == github.GITHUB_API == "https://api.github.com"


def test_the_adapter_reads_no_setting_or_environment() -> None:
    tree = ast.parse((PACKAGE / "github.py").read_text())
    names = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute | ast.Name)
    }
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    } | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not {"environ", "getenv", "get_settings"} & names
    assert not {"os", "nexovue_scheduler.settings"} & imported


def test_production_never_imports_the_harness_or_the_fake() -> None:
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert not [name for name in modules if name.split(".")[-1] in TEST_ONLY], path


def test_the_fault_hook_does_nothing_in_production() -> None:
    assert sync.fault is sync._no_fault
    sync.fault("before_create")
