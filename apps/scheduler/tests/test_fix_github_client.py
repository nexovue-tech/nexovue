"""The Fix App's GitHub client offers only its named routes, on a fixed host (F5 plan T3.10;
spec AC7).

A method added without the checked-in `ROUTES` list fails here; no method writes in Step 3
(foundation item 33: write access waits for the founder's go); the client never follows a
redirect; production settings name GitHub's own hosts only.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from nexovue_scheduler.connections import fix_github
from nexovue_scheduler.connections.fix_github import FixEndpoints, FixGitHub

SOURCE = Path(fix_github.__file__)


def _public_routes() -> set[str]:
    methods = {
        name
        for name, member in inspect.getmembers(FixGitHub, inspect.isfunction)
        if not name.startswith("_")
    }
    functions = {"convert_manifest", "exchange_code", "user_of_token"}
    return methods | functions


def test_ac7_every_public_method_is_a_named_route_and_no_route_is_unlisted() -> None:
    listed = {name for _, _, name in fix_github.ROUTES}

    assert _public_routes() - {"installation_ids", "revoke"} == listed


def test_ac7_step_3_holds_no_write_route() -> None:
    writes = [route for route in fix_github.ROUTES if route[0] == "POST"]

    assert {name for _, _, name in writes} == {"convert_manifest", "read_token", "exchange_code"}
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)]
    assert not any(isinstance(value, str) and "/git/refs" in value for value in literals)


def test_ac7_the_client_never_follows_a_redirect() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    flags = [
        keyword.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "follow_redirects"
    ]

    assert flags and all(isinstance(flag, ast.Constant) and flag.value is False for flag in flags)


def test_ac7_production_names_github_s_own_hosts_only() -> None:
    endpoints = FixEndpoints()

    assert (endpoints.api, endpoints.web) == ("https://api.github.com", "https://github.com")
    assert endpoints.private_allowed is False
