"""The read surface is a list somebody decided on (F36, FR17, D8).

A route is on the surface **if and only if** it depends on `CurrentReader`
instead of `CurrentIdentity`. That makes "what can an API token reach?" a
question about the source rather than about which routes happened to work: this
suite reads every router, collects the handlers that take a `CurrentReader`, and
holds them against the list below.

So adding a route to the surface is a visible change to a checked-in list, in
the same review as the route — and the list is what the customer documentation
publishes. Removing one is equally loud.

Structural, like `test_authorization_coverage.py`: it parses the source, so a
route added next month is judged the day it exists.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final

import pytest

from nexovue_api import auth

API_SOURCE: Final = Path(auth.__file__).resolve().parents[1]
ROUTERS: Final = sorted(
    path for path in API_SOURCE.rglob("*.py") if "router" in path.read_text(encoding="utf-8")
)

READER: Final = "CurrentReader"
IDENTITY: Final = "CurrentIdentity"

# The documented read surface. Every entry is a GET an API token may reach, and
# nothing else may be reached by one. Keyed `<module path>:<handler>`, with the
# route it serves, so the list reads as the documentation does.
THE_SURFACE: Final = {
    "projects/router.py:list_projects": "GET /projects",
    "services/router.py:list_services": "GET /services",
    "services/router.py:get_service": "GET /services/{service}",
    "traces/router.py:list_traces": "GET /traces",
    "traces/router.py:get_trace": "GET /traces/{trace_id}",
    "logs/search.py:search": "GET /logs/search",
    "metrics/router.py:list_metrics": "GET /metrics",
    "metrics/router.py:read_histogram": "GET /metrics/{metric}/histogram",
    "errorgroups/router.py:list_error_groups": "GET /error-groups",
    "errorgroups/router.py:get_error_group": "GET /error-groups/{group_id}",
    "alerts/router.py:list_incidents": "GET /alerts/incidents",
    "alerts/router.py:get_incident": "GET /alerts/incidents/{incident_id}",
    "checks/router.py:list_checks": "GET /checks",
    "checks/router.py:get_check": "GET /checks/{check_id}",
    "synthetics/router.py:list_check_results": "GET /synthetics/checks",
    "synthetics/router.py:list_runs": "GET /synthetics/checks/{check_id}/runs",
    "deploys/router.py:list_deploys": "GET /deploys",
}


def _module(path: Path) -> str:
    return str(path.relative_to(API_SOURCE))


def _handlers() -> list[tuple[str, ast.AsyncFunctionDef | ast.FunctionDef, list[str]]]:
    """Every handler in every router, with the decorators it carries."""
    found = []
    for path in ROUTERS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            decorators = [ast.unparse(decorator) for decorator in node.decorator_list]
            if any(".get(" in decorator or "router." in decorator for decorator in decorators):
                found.append((f"{_module(path)}:{node.name}", node, decorators))
    return found


def _annotations(node: ast.AsyncFunctionDef | ast.FunctionDef) -> str:
    return " ".join(
        ast.unparse(argument.annotation)
        for argument in node.args.args + node.args.kwonlyargs
        if argument.annotation is not None
    )


def _takes_a_reader() -> dict[str, str]:
    return {
        key: annotations
        for key, node, _ in _handlers()
        if READER in (annotations := _annotations(node))
    }


def test_the_surface_is_exactly_the_documented_list() -> None:
    reached = _takes_a_reader()
    assert sorted(reached) == sorted(THE_SURFACE), (
        "the routes taking CurrentReader are not the documented read surface. "
        "Adding one means an API token can now read it: update THE_SURFACE here "
        "and the customer documentation in docs/product/setup/api.md, in this change."
    )


@pytest.mark.parametrize("key", sorted(THE_SURFACE))
def test_a_surface_route_asks_for_a_reader_and_not_a_person(key: str) -> None:
    """Both would be a route that reads as gated and is not: `CurrentIdentity`
    refuses a token, so a handler carrying both could never serve one."""
    annotations = _takes_a_reader()
    assert key in annotations, f"{key} is on the surface but takes no {READER}"
    assert IDENTITY not in annotations[key], f"{key} takes both {READER} and {IDENTITY}"


@pytest.mark.parametrize("key", sorted(THE_SURFACE))
def test_every_surface_route_is_a_get(key: str) -> None:
    """A token reads (FR15). A surface route that mutated would be reachable by one."""
    decorators = {handler: found for handler, _, found in _handlers()}
    assert key in decorators, f"{key} names no handler"
    methods = [
        method
        for decorator in decorators[key]
        for method in re.findall(r"router\.(\w+)\(", decorator)
    ]
    assert methods, f"{key} carries no route decorator"
    assert set(methods) == {"get"}, f"{key} serves {sorted(set(methods))}, not only GET"
