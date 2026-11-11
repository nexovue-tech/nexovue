"""F4 plan T2.7: the catalog held to the routes it names (design, section 2; AC12, AC14).

- every handler is on the read surface (`THE_SURFACE`), and its route is a GET;
- **built from each route's response model:** every field the catalog sends exists there,
  and one declared `number`, `closed`, `id` or `time` has that type in the model, so a free
  string cannot be declared a number; every free `str` or `dict` it sends is fenced;
- every argument's query name is a parameter of the route;
- `TICKET_HANDLERS` is the catalog's handlers, and every tool has a link;
- `findings.title` is never sent (U1).
"""

from __future__ import annotations

import json
import types
import typing
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

import environment  # noqa: F401 — sets what importing the app reads
import pytest
from fastapi.routing import APIRoute
from nexovue_platform.assistant_tools import (
    CATALOG,
    FENCED_KINDS,
    TICKET_HANDLERS,
    ChatTool,
    FieldRule,
)
from pydantic import BaseModel
from test_read_surface_structure import THE_SURFACE

from nexovue_api.main import app


def _routes() -> dict[str, APIRoute]:
    found: dict[str, APIRoute] = {}
    for route in app.routes:
        contexts = getattr(route, "effective_route_contexts", None)
        candidates = [route] if isinstance(route, APIRoute) else []
        if contexts is not None:
            candidates += [
                c.original_route for c in contexts() if isinstance(c.original_route, APIRoute)
            ]
        for each in candidates:
            module = each.endpoint.__module__.replace(".", "/")
            found[f"{module}.py:{each.endpoint.__name__}"] = each
    return found


ROUTES = _routes()


def _row_model(tool: ChatTool) -> type[BaseModel]:
    response = ROUTES[tool.handler].response_model
    assert isinstance(response, type) and issubclass(response, BaseModel)
    annotation = response.model_fields[tool.rows_at].annotation
    # `list[Row]`, or a union of lists (the findings feed serves people and tokens apart).
    options = (
        get_args(annotation)
        if get_origin(annotation) in (types.UnionType, typing.Union)
        else (annotation,)
    )
    rows = [get_args(option)[0] for option in options if get_origin(option) is list]
    assert rows, f"{tool.name}: {tool.rows_at} is not a list"
    # The narrowest row model: what a ticket, never a person, reads.
    narrowest = min(rows, key=lambda model: len(model.model_fields))
    assert isinstance(narrowest, type) and issubclass(narrowest, BaseModel)
    return narrowest


def _plain(annotation: Any) -> set[Any]:
    """The types an annotation admits, None left out."""
    if get_origin(annotation) in (types.UnionType, typing.Union):
        return {kind for option in get_args(annotation) for kind in _plain(option)} - {type(None)}
    return {annotation}


def _fits(rule: FieldRule, annotation: Any) -> bool:
    admitted = _plain(annotation)
    if rule.kind == "number":
        return admitted <= {int, float}
    if rule.kind == "time":
        return admitted <= {datetime} or "Timestamp" in str(annotation)
    if rule.kind == "closed":
        return all(get_origin(kind) is Literal or kind is bool for kind in admitted)
    if rule.kind == "id":
        return all(kind is str or getattr(kind, "__name__", "") == "UUID" for kind in admitted)
    return True


@pytest.mark.parametrize("tool", CATALOG, ids=lambda tool: tool.name)
def test_every_tool_is_a_get_on_the_read_surface(tool: ChatTool) -> None:
    assert tool.handler in THE_SURFACE
    route = ROUTES[tool.handler]
    assert route.methods == {"GET"}
    assert THE_SURFACE[tool.handler] == f"GET {tool.route.removeprefix('/api/v1')}"


@pytest.mark.parametrize("tool", CATALOG, ids=lambda tool: tool.name)
def test_every_field_sent_has_a_kind_its_model_type_allows(tool: ChatTool) -> None:
    model = _row_model(tool)
    for name, rule in tool.fields.items():
        assert name in model.model_fields, f"{tool.name}.{name} is not in {model.__name__}"
        annotation = model.model_fields[name].annotation
        assert _fits(rule, annotation), f"{tool.name}.{name} is {annotation}, not {rule.kind}"
        if _plain(annotation) & {str, dict} and rule.kind not in ("closed", "id", "time"):
            assert rule.kind in FENCED_KINDS, f"{tool.name}.{name} is free text and unfenced"


@pytest.mark.parametrize("tool", CATALOG, ids=lambda tool: tool.name)
def test_every_argument_is_a_parameter_of_its_route(tool: ChatTool) -> None:
    route = ROUTES[tool.handler]
    accepted = {param.alias or param.name for param in route.dependant.query_params}
    for name, rule in tool.arguments.items():
        assert rule.query in accepted, f"{tool.name}.{name} sends {rule.query}"
    assert "project_id" in accepted, "the client binds the question's project"


def test_tickets_reach_the_catalog_and_nothing_else() -> None:
    assert {tool.handler for tool in CATALOG} == TICKET_HANDLERS


@pytest.mark.parametrize("tool", CATALOG, ids=lambda tool: tool.name)
def test_every_tool_has_a_link_and_sends_no_finding_title(tool: ChatTool) -> None:
    assert tool.link.path.startswith("/")
    assert "title" not in tool.fields
    assert all(field.kind != "team_text" for field in tool.fields.values())


@pytest.mark.parametrize("tool", CATALOG, ids=lambda tool: tool.name)
def test_every_list_says_it_is_not_a_count(tool: ChatTool) -> None:
    if tool.name in ("list_findings", "search_logs"):
        assert "not a count" in tool.description


CONSOLE_ROUTES = (
    Path(__file__).resolve().parents[2] / "web" / "src" / "app" / "routes" / "console-routes.json"
)


def test_every_step_link_opens_a_screen_the_console_has() -> None:
    """A step's link is built by the API from the catalog; one naming a path the console
    does not route opens nothing. `route-manifest.test.ts` holds the list to the router."""
    routes = set(json.loads(CONSOLE_ROUTES.read_text(encoding="utf-8")))
    for tool in CATALOG:
        assert tool.link.path in routes, (tool.name, tool.link.path)
