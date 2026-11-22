"""The Fix App's GitHub client offers only its named routes, on a fixed host (F5 plan T3.10,
T7.2; spec AC7, FR23).

A method added without the checked-in `ROUTES` or `WRITE_ROUTES` list fails here. Every
write is on `FixWrites`, which cannot be built while write access is off in code (foundation
item 33; ADR-097); no write method can name a branch or a ref: each builds
`nexovue/suggestion-<id>` itself. The client never follows a redirect; production settings
name GitHub's own hosts only.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from nexovue_platform.fixes.write_gate import WRITE_ACCESS_GO

from nexovue_scheduler.connections import fix_github
from nexovue_scheduler.connections.fix_github import FixEndpoints, FixGitHub, FixWrites, WritesOff

SOURCE = Path(fix_github.__file__)


def _public(cls: type) -> set[str]:
    return {
        name for name, _ in inspect.getmembers(cls, inspect.isfunction) if not name.startswith("_")
    }


def _class(name: str) -> ast.ClassDef:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    [found] = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name]
    return found


def test_ac7_every_public_method_is_a_named_route_and_no_route_is_unlisted() -> None:
    listed = {name for _, _, name in fix_github.ROUTES}
    functions = {"convert_manifest", "exchange_code", "user_of_token"}

    assert _public(FixGitHub) - {"installation_ids", "revoke", "writes"} | functions == listed
    assert _public(FixWrites) == {name for _, _, name in fix_github.WRITE_ROUTES}


def test_ac7_every_write_is_on_fix_writes_and_the_reads_post_only_for_tokens() -> None:
    posts = {name for method, _, name in fix_github.ROUTES if method != "GET"}

    assert posts == {"convert_manifest", "read_token", "exchange_code", "pull_request_read_token"}
    assert {method for method, _, _ in fix_github.WRITE_ROUTES} == {"POST", "DELETE"}
    reads = ast.unparse(_class("FixGitHub"))
    assert '"POST"' not in reads and '"DELETE"' not in reads and '"PATCH"' not in reads


def test_ac7_no_write_method_can_name_a_branch_or_a_ref() -> None:
    """FR23: a write takes a suggestion's id, never a branch, ref or tag; every ref the
    module names is built by `fix_branch`."""
    for name in _public(FixWrites):
        parameters = set(inspect.signature(getattr(FixWrites, name)).parameters)
        assert not parameters & {"branch", "ref", "tag", "head", "url", "path_"}, name
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and "refs/heads/" in ast.unparse(node):
            assert "fix_branch(" in ast.unparse(node), ast.unparse(node)
    literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)]
    refs = {value for value in literals if isinstance(value, str) and value.startswith("refs/")}
    assert refs == {"refs/heads/"}


def test_ac7_the_draft_is_always_asked_for_as_a_draft() -> None:
    """FR23, AC27a: `POST /pulls` carries `draft: True`, a constant, and nothing switches it."""
    source = ast.unparse(_class("FixWrites"))
    assert "'draft': True" in source
    assert source.count("'draft'") == 1


def test_ac7_writes_cannot_be_built_while_write_access_is_off() -> None:
    """ADR-097: no write token and no write route without the founder's recorded go."""
    github = FixGitHub(FixEndpoints(), app_id=1, private_key="unused")

    assert WRITE_ACCESS_GO is None
    with pytest.raises(WritesOff):
        github.writes(WRITE_ACCESS_GO)


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
