"""F4 plan T2.7 and T3.1: the catalog held to the routes it names (design, section 2; AC12,
AC14, AC22).

- every handler is on the read surface (`THE_SURFACE`), and its route is a GET;
- **built from each route's response model:** every field the catalog sends exists in one of
  its row sources, and one declared `number`, `closed`, `id` or `time` has that type there, so
  a free string cannot be declared a number; every free `str` or `dict` it sends is fenced;
- every argument is a query parameter or a path slot of the route, and a path slot has a
  closed shape;
- the question's project reaches every route: as `project_id`, or from the ticket's scope;
- each windowed tool's retention clock is the clock of the table its route reads;
- `TICKET_HANDLERS` is the catalog's handlers, and every tool links to a console route that
  carries what the design's destination table says;
- `findings.title`, deploy `metadata` and check `config` are never sent (U1, AC12).
"""

from __future__ import annotations

import inspect
import json
import re
import types
import typing
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, get_args, get_origin

import environment  # noqa: F401 — sets what importing the app reads
import pytest
from fastapi.routing import APIRoute
from nexovue_platform.assistant_tools import (
    CATALOG,
    FENCED_KINDS,
    PATH_SHAPES,
    RANGE_NOT_APPLIED,
    TICKET_HANDLERS,
    ChatTool,
    FieldRule,
    RetentionClock,
)
from pydantic import BaseModel
from test_read_surface_structure import THE_SURFACE

from nexovue_api.assistant.service import _step_view
from nexovue_api.data.postgres.models import AssistantStep
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
API_SOURCE = Path(__file__).resolve().parents[1] / "src"


def _options(annotation: Any) -> tuple[Any, ...]:
    if get_origin(annotation) in (types.UnionType, typing.Union):
        return get_args(annotation)
    return (annotation,)


def _model_of(annotation: Any) -> type[BaseModel] | None:
    """The row model an annotation holds: a model, a list of one, or either or None."""
    for option in _options(annotation):
        if get_origin(option) is list:
            option = get_args(option)[0]
        if isinstance(option, type) and issubclass(option, BaseModel):
            return option
    return None


def _response(tool: ChatTool) -> type[BaseModel]:
    """The narrowest response model: what a ticket, never a person, reads (the findings
    routes serve people and tokens apart)."""
    response = ROUTES[tool.handler].response_model
    models = [m for m in _options(response) if isinstance(m, type) and issubclass(m, BaseModel)]
    assert models, f"{tool.name}: no response model"
    return min(models, key=lambda model: len(model.model_fields))


def _row_models(tool: ChatTool) -> list[type[BaseModel]]:
    response = _response(tool)
    models: list[type[BaseModel]] = []
    for source in tool.sources:
        if source == "":
            model: type[BaseModel] | None = response
        else:
            assert source in response.model_fields, f"{tool.name}: no {source} in the response"
            model = _model_of(response.model_fields[source].annotation)
        assert model is not None, f"{tool.name}: {source} holds no rows"
        if tool.flatten is not None:
            nested = _model_of(model.model_fields[tool.flatten].annotation)
            assert nested is not None, f"{tool.name}: {tool.flatten} holds no rows"
            models.append(nested)
        models.append(model)
    return models


def _annotation(models: list[type[BaseModel]], name: str) -> list[Any]:
    """The field's annotation in each row model holding it; a dotted name walks nested ones."""
    found: list[Any] = []
    for model in models:
        current: type[BaseModel] | None = model
        annotation: Any = None
        for part in name.split("."):
            if current is None or part not in current.model_fields:
                annotation = None
                break
            annotation = current.model_fields[part].annotation
            current = _model_of(annotation)
        if annotation is not None:
            found.append(annotation)
    return found


def _plain(annotation: Any) -> set[Any]:
    """The types an annotation admits, None left out; a list counts as what it holds."""
    if get_origin(annotation) in (types.UnionType, typing.Union):
        return {kind for option in get_args(annotation) for kind in _plain(option)} - {type(None)}
    if get_origin(annotation) is list:
        return _plain(get_args(annotation)[0])
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


# Fields named as ids without an `_id` ending: a row's own id, and the log's own hash.
ID_NAMES: Final = frozenset({"id", "row_key"})


def _slots(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


@pytest.mark.parametrize("tool", CATALOG, ids=lambda tool: tool.name)
def test_every_tool_is_a_get_on_the_read_surface(tool: ChatTool) -> None:
    assert tool.handler in THE_SURFACE
    route = ROUTES[tool.handler]
    assert route.methods == {"GET"}
    assert _slots(THE_SURFACE[tool.handler]) == f"GET {_slots(tool.route.removeprefix('/api/v1'))}"


@pytest.mark.parametrize("tool", CATALOG, ids=lambda tool: tool.name)
def test_every_field_sent_has_a_kind_its_model_type_allows(tool: ChatTool) -> None:
    models = _row_models(tool)
    for name, rule in tool.fields.items():
        annotations = _annotation(models, name)
        assert annotations, f"{tool.name}.{name} is in no row model of {tool.sources}"
        for annotation in annotations:
            assert _fits(rule, annotation), f"{tool.name}.{name} is {annotation}, not {rule.kind}"
            # An id is a key nexovue or a protocol made, never a free string a sender wrote.
            if rule.kind == "id":
                assert name.rsplit(".", 1)[-1] in ID_NAMES or name.endswith("_id"), name
            if _plain(annotation) & {str, dict} and rule.kind not in ("closed", "id", "time"):
                assert rule.kind in FENCED_KINDS, f"{tool.name}.{name} is free text and unfenced"


@pytest.mark.parametrize("tool", CATALOG, ids=lambda tool: tool.name)
def test_every_argument_is_a_parameter_or_a_shaped_slot_of_its_route(tool: ChatTool) -> None:
    route = ROUTES[tool.handler]
    accepted = {param.alias or param.name for param in route.dependant.query_params}
    slots = {param.name for param in route.dependant.path_params}
    assert set(re.findall(r"\{([a-z_]+)\}", tool.route)) == slots
    for name, rule in tool.arguments.items():
        if rule.in_path:
            assert rule.query in slots, f"{tool.name}.{name} fills {rule.query}"
            assert rule.required
            assert rule.kind in PATH_SHAPES or rule.kind in ("service_name", "metric_name")
        else:
            assert rule.query in accepted, f"{tool.name}.{name} sends {rule.query}"
    assert slots == {rule.query for rule in tool.arguments.values() if rule.in_path}
    if tool.takes_window:
        assert {"from", "to"} <= accepted and {"from", "to"} <= set(tool.arguments)


@pytest.mark.parametrize("tool", CATALOG, ids=lambda tool: tool.name)
def test_the_question_s_project_reaches_every_route(tool: ChatTool) -> None:
    """The client sends the question's own project where the route takes one; a route
    taking none must compare the ticket's scope with what it loads (AC8)."""
    accepted = {param.alias or param.name for param in ROUTES[tool.handler].dependant.query_params}
    if tool.project_in_query:
        assert "project_id" in accepted, "the client binds the question's project"
    else:
        assert "project_id" not in accepted
        source = inspect.getsource(ROUTES[tool.handler].endpoint)
        assert "project_scope=reader.project_scope" in source or "reader=reader" in source


# The ClickHouse tables each windowed tool's route reads, by the package holding its queries,
# and the clock each table expires on (design, *The aged-out check*; migrations 000001-5,
# 000008, 000014, 000035, 000036).
TABLE_CLOCK: Final[dict[str, RetentionClock]] = {
    "logs": "raw",
    "spans": "raw",
    "trace_summaries": "raw",
    "metrics_histogram": "raw",
    "metrics_gauge": "raw",
    "metrics_sum": "raw",
    "endpoint_stats_1m": "derived",
    "service_edges_1m": "derived",
    "service_stats_1m": "derived_capped",
    "detection_readings": "derived_capped",
}
READS: Final[dict[str, tuple[str, tuple[str, ...]]]] = {
    "list_services": ("services", ("endpoint_stats_1m",)),
    "get_service": ("services", ("endpoint_stats_1m",)),
    "list_service_endpoints": ("services", ("endpoint_stats_1m",)),
    "get_service_trend": ("services", ("endpoint_stats_1m",)),
    "get_service_map": ("servicemap", ("service_edges_1m",)),
    "list_error_groups": ("errorgroups", ("spans", "logs")),
    "get_error_group": ("errorgroups", ("spans", "logs")),
    "search_logs": ("logs", ("logs",)),
    "list_traces": ("traces", ("trace_summaries", "spans")),
    # The metric catalogue unions every metrics table by name (`metrics/queries.py`).
    "list_metrics": ("metrics", ("metrics_histogram", "metrics_gauge", "metrics_sum")),
    "get_metric_histogram": ("metrics", ("metrics_histogram",)),
    "list_hosts": ("infrastructure", ("metrics_gauge",)),
    "get_host_trend": ("infrastructure", ("metrics_gauge",)),
}
# Windowed tools reading Postgres records, which keep their own purge.
POSTGRES_WINDOWED: Final = frozenset({"list_releases", "get_service_detection"})
_ORDER: Final[tuple[RetentionClock, ...]] = ("derived", "derived_capped", "raw")


@pytest.mark.parametrize("tool", CATALOG, ids=lambda tool: tool.name)
def test_each_windowed_tool_runs_on_its_own_table_s_clock(tool: ChatTool) -> None:
    """T3.1: a raw table ages out first, so a tool reading one ends aged out where a rollup
    tool still reads (AC24)."""
    if not tool.takes_window or tool.name in POSTGRES_WINDOWED:
        assert tool.retention == "none"
        return
    package, tables = READS[tool.name]
    source = "".join(
        path.read_text(encoding="utf-8")
        for path in (API_SOURCE / "nexovue_api" / package).rglob("*.py")
    )
    for table in tables:
        assert table in source, f"{package} does not read {table}"
    clocks = {TABLE_CLOCK[table] for table in tables}
    # The shortest-lived table read decides.
    assert tool.retention == max(clocks, key=_ORDER.index)


def test_tickets_reach_the_catalog_and_nothing_else() -> None:
    assert {tool.handler for tool in CATALOG} == TICKET_HANDLERS


NEVER_SENT: Final = frozenset({"title", "metadata", "config", "detail", "notifications", "url"})


@pytest.mark.parametrize("tool", CATALOG, ids=lambda tool: tool.name)
def test_no_tool_sends_a_title_metadata_or_a_check_s_config(tool: ChatTool) -> None:
    assert not {part for name in tool.fields for part in name.split(".")} & NEVER_SENT
    assert all(field.kind != "team_text" for field in tool.fields.values())


# Rollups, counted before sampling: the tools counts come from (BR11, AC23).
ROLLUP_LISTS: Final = frozenset({"list_services", "list_service_endpoints", "list_hosts"})


@pytest.mark.parametrize("tool", CATALOG, ids=lambda tool: tool.name)
def test_every_list_says_it_is_not_a_count(tool: ChatTool) -> None:
    """BR11: a list is cut or sampled, so counting its rows gives a wrong number that looks
    right."""
    if tool.name.startswith(("list_", "search_")) and tool.name not in ROLLUP_LISTS:
        assert "not a count" in tool.description, tool.name
    if tool.sampled:
        # A sampled list's numbers are never "counted from telemetry" (BR11).
        assert (
            all(rule.basis != "telemetry" or rule.kind != "number" for rule in tool.fields.values())
            or tool.name == "search_logs"
        )


CONSOLE_ROUTES = (
    Path(__file__).resolve().parents[2] / "web" / "src" / "app" / "routes" / "console-routes.json"
)


def test_every_step_link_opens_a_screen_the_console_has() -> None:
    """A step's link is built by the API from the catalog; one naming a path the console
    does not route opens nothing. `route-manifest.test.ts` holds the list to the router."""
    routes = set(json.loads(CONSOLE_ROUTES.read_text(encoding="utf-8")))
    for tool in CATALOG:
        assert tool.link.path in routes, (tool.name, tool.link.path)


# The design's destination table (*Where each step leads*), as data: where each tool goes,
# whether its link carries the range, and the sentence it shows where the range is not
# applied. A change to either side fails here (AC22).
DESTINATIONS: Final[dict[str, tuple[str, bool, str | None]]] = {
    "list_findings": ("/", False, None),
    "get_finding": ("/findings/$findingId", False, None),
    "get_finding_explanation": ("/findings/$findingId", False, None),
    "list_services": ("/services", False, RANGE_NOT_APPLIED),
    "get_service": ("/services/$name", True, None),
    "get_service_detection": ("/services/$name", True, None),
    "list_service_endpoints": ("/services/$name", True, None),
    "get_service_trend": ("/services/$name", True, None),
    "get_service_map": (
        "/service-map",
        False,
        "This shows the map now; the range is not applied.",
    ),
    "list_releases": ("/releases", True, None),
    "get_release_comparison": ("/releases/$releaseId", False, RANGE_NOT_APPLIED),
    "list_incidents": ("/alerts", False, RANGE_NOT_APPLIED),
    "get_incident": ("/alerts/incidents/$incidentId", False, RANGE_NOT_APPLIED),
    "list_checks": ("/synthetics", False, RANGE_NOT_APPLIED),
    "list_error_groups": ("/errors", True, None),
    # `/errors` opens the group from `?group=` (F3 AC105), so this link is filtered.
    "get_error_group": ("/errors", True, None),
    "search_logs": ("/logs", True, None),
    "list_traces": ("/traces", True, None),
    "get_trace": ("/traces/$traceId", False, None),
    "list_metrics": ("/metrics", False, RANGE_NOT_APPLIED),
    "get_metric_histogram": ("/metrics", False, RANGE_NOT_APPLIED),
    "list_hosts": ("/infrastructure", True, None),
    "get_host_trend": ("/infrastructure", True, None),
}


@pytest.mark.parametrize("tool", CATALOG, ids=lambda tool: tool.name)
def test_every_tool_goes_where_the_design_s_table_says(tool: ChatTool) -> None:
    path, carries_range, caveat = DESTINATIONS[tool.name]
    assert (tool.link.path, tool.link.carries_range, tool.link.caveat) == (
        path,
        carries_range,
        caveat,
    )
    assert carries_range is False or tool.takes_window, "a range is carried only when read"
    params = set(re.findall(r"\$([A-Za-z]+)", tool.link.path))
    assert params == set(tool.link.params), tool.name
    for argument in (*tool.link.params.values(), *tool.link.carries):
        assert argument in tool.arguments, f"{tool.name} links {argument}, which it never takes"
        if argument in tool.link.params.values():
            assert tool.arguments[argument].required


def test_the_catalog_names_every_tool_once_and_covers_the_table() -> None:
    assert len({tool.name for tool in CATALOG}) == len(CATALOG)
    assert {tool.name for tool in CATALOG} == set(DESTINATIONS)


@pytest.mark.parametrize("tool", [t for t in CATALOG if t.quote_keys], ids=lambda t: t.name)
def test_a_quotable_tool_sends_the_keys_its_quote_is_read_back_by(tool: ChatTool) -> None:
    assert set(tool.quote_keys) <= set(tool.fields)
    assert any(tool.quotable(name) for name in tool.fields)


STEP_LINKS = (
    Path(__file__).resolve().parents[2] / "web" / "src" / "features" / "assistant" / "step-links.ts"
)


def test_the_panel_offers_a_link_for_every_destination_the_catalog_names() -> None:
    """The panel shows no link for a path outside its `STEP_TARGETS`, so a catalog link it
    does not list would read as a step with nowhere to go."""
    source = STEP_LINKS.read_text(encoding="utf-8")
    listed = set(re.findall(r'^\s+"(/[^"]*)",$', source, flags=re.MULTILINE))
    assert {tool.link.path for tool in CATALOG} == listed


def _step(tool: str, arguments: dict[str, Any], **columns: Any) -> AssistantStep:
    return AssistantStep(
        position=1,
        tool=tool,
        arguments=arguments,
        display_arguments=None,
        from_time=columns.get("from_time"),
        to_time=columns.get("to_time"),
        row_count=columns.get("row_count", 3),
        was_cut=False,
        state=columns.get("state", "read"),
        failure=columns.get("failure"),
    )


def test_a_step_link_fills_its_route_and_carries_only_a_shown_phrase() -> None:
    """AC22, AC62, as the API builds them from a stored step."""
    project = uuid.uuid4()
    trace = _step_view(_step("get_trace", {"trace_id": "a1" * 16}), project_id=project)
    assert trace.link is not None
    assert (trace.link.path, trace.link.params) == ("/traces/$traceId", {"traceId": "a1" * 16})
    assert trace.link.query == {"project": str(project)}

    at = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    shown = _step("search_logs", {"q": "timed out"}, from_time=at, to_time=at)
    hidden = _step("search_logs", {"q": None}, from_time=at, to_time=at)
    shown_link = _step_view(shown, project_id=project).link
    hidden_link = _step_view(hidden, project_id=project).link
    assert shown_link is not None and hidden_link is not None
    assert shown_link.query["q"] == "timed out"
    assert "q" not in hidden_link.query

    # A route parameter the step does not hold gives no link rather than another screen.
    assert _step_view(_step("get_trace", {}), project_id=project).link is None


def test_a_kept_since_note_reads_as_a_date_and_never_as_text() -> None:
    aged = _step("search_logs", {}, state="aged_out", failure="kept_since:2026-11-05")
    view = _step_view(aged, project_id=uuid.uuid4())
    assert (str(view.kept_since), view.failure) == ("2026-11-05", None)
    odd = _step_view(_step("search_logs", {}, failure="kept_since:<b>"), project_id=uuid.uuid4())
    assert (odd.kept_since, odd.failure) == (None, None)
