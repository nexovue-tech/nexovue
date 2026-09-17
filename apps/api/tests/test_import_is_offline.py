"""An import makes no outbound request and runs no code, by construction (AC10, BR5).

Structural: the package imports no HTTP client, socket library or JavaScript
engine, so there is nothing for a document to persuade into fetching or running.
"""

from __future__ import annotations

import ast
import json
import re
import socket
from pathlib import Path
from typing import Final

import pytest

from nexovue_api import checkimports
from nexovue_api.checkimports import openapi
from nexovue_api.errors.exceptions import InvalidRequestError

PACKAGE: Final = Path(checkimports.__file__).parent

FORBIDDEN_MODULES: Final = frozenset(
    {
        "httpx",
        "requests",
        "urllib.request",
        "http.client",
        "socket",
        "pycurl",
        "aiohttp",
        "py_mini_racer",
        "dukpy",
        "quickjs",
        "js2py",
    }
)


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _is_forbidden(module: str) -> bool:
    return any(module == banned or module.startswith(f"{banned}.") for banned in FORBIDDEN_MODULES)


MODULES: Final = sorted(PACKAGE.glob("*.py"))


def test_the_scan_found_the_parsers() -> None:
    """A structural test that matched nothing passes for the wrong reason."""
    names = {path.stem for path in MODULES}
    assert {"curl", "openapi", "postman", "yaml_safe", "plan", "service", "router"} <= names


@pytest.mark.parametrize("module", MODULES, ids=lambda path: path.name)
def test_no_import_module_can_reach_the_network_or_run_code(module: Path) -> None:
    forbidden = sorted(name for name in _imported_modules(module) if _is_forbidden(name))
    assert forbidden == [], f"{module.name} imports {forbidden}"


@pytest.fixture
def no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("an import tried to open a connection")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def _description(response: dict[str, object]) -> str:
    return json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "T", "version": "1"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {"/a": {"get": {"responses": {"200": response}}}},
        }
    )


@pytest.mark.usefixtures("no_sockets")
def test_a_remote_reference_is_refused_without_any_connection() -> None:
    with pytest.raises(InvalidRequestError, match=re.escape("https://evil.example/schema.json")):
        openapi.preview(_description({"$ref": "https://evil.example/schema.json"}), server_url=None)


@pytest.mark.usefixtures("no_sockets")
def test_a_valid_description_is_validated_and_planned_without_any_connection() -> None:
    plan = openapi.preview(_description({"description": "ok"}), server_url=None)

    assert [item.url for item in plan.items] == ["https://api.example.com/a"]
