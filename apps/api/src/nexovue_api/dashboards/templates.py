"""The starting points a dashboard opens from (F20, FR39, FR40, spec *The templates*).

Each is a dashboard document, checked by the same validator as a save (FR7, AC80), and
opened as an unsaved draft: opening one writes no row (AC38). Their panels, widths and
worst-case reads are the spec's table, which `test_dashboard_definition.py` holds them to.

A saved template is found by its title, which is unique in an organization ignoring case
(D20): that is how the list says "Open" and a service's screen says "Open Service health
for checkout" rather than offering a second copy (FR39, FR41).
"""

from __future__ import annotations

from typing import Any, Final

from nexovue_api.dashboards.definition import DashboardDocument

START_FROM_OVERVIEW: Final = "start_from_overview"
SERVICE_HEALTH: Final = "service_health"
INFRASTRUCTURE: Final = "infrastructure"

# Three charts to a row, four numbers to a row, and a table across the grid (FR49).
CHART_WIDTH: Final = 4
NUMBER_WIDTH: Final = 3
HALF_WIDTH: Final = 6
FULL_WIDTH: Final = 12
# The spec's rows for every template table.
TABLE_ROWS: Final = 10
REFRESH_SECONDS: Final = 60
PICKER: Final = {"picker": True}
SIGNALS: Final = ("rate", "errors", "latency")
# Overview's four totals (`OverviewScreen.tsx`, `Totals`), in its order.
OVERVIEW_TOTALS: Final = ("service_count", "requests_per_minute", "error_rate", "p95_ms")
# The infrastructure screen's fleet metrics, by their OpenTelemetry names.
HOST_METRICS: Final = (
    "system.cpu.utilization",
    "system.memory.utilization",
    "system.filesystem.usage",
)
TOP_NODES: Final = 5


def _panel(panel_id: str, kind: str, width: int, height: str, **settings: Any) -> dict[str, Any]:
    return {
        "id": panel_id,
        "kind": kind,
        "title": None,
        "width": width,
        "height": height,
        "settings": settings,
    }


def _document(title: str, panels: list[dict[str, Any]], *, picker: bool) -> DashboardDocument:
    return DashboardDocument.model_validate(
        {
            "title": title,
            "description": "",
            "definition": {
                "schema_version": 1,
                "refresh_seconds": REFRESH_SECONDS,
                "default_range": None,
                "service_picker": picker,
                "links": [],
                "panels": panels,
            },
        }
    )


def _start_from_overview() -> DashboardDocument:
    """Overview's three charts, its four totals and its worst services (FR40)."""
    charts = [
        _panel(f"p_{signal}", "project_trend", CHART_WIDTH, "medium", signal=signal)
        for signal in SIGNALS
    ]
    totals = [
        _panel(
            f"n_{measure}", "project_number", NUMBER_WIDTH, "small", measure=measure, compare=False
        )
        for measure in OVERVIEW_TOTALS
    ]
    worst = _panel("t_worst", "services_table", FULL_WIDTH, "large", order="worst", rows=TABLE_ROWS)
    return _document("Overview", [*charts, *totals, worst], picker=False)


def _service_health() -> DashboardDocument:
    """One service at a time, chosen by the picker, so one dashboard serves every service
    rather than a copy each (D7)."""
    charts = [
        _panel(f"s_{signal}", "service_trend", CHART_WIDTH, "medium", service=PICKER, signal=signal)
        for signal in SIGNALS
    ]
    p95 = _panel(
        "n_p95",
        "service_number",
        NUMBER_WIDTH,
        "small",
        service=PICKER,
        measure="p95_ms",
        compare=True,
    )
    findings = _panel("f_open", "open_findings", NUMBER_WIDTH, "small", service=PICKER)
    endpoints = _panel(
        "t_endpoints",
        "endpoints_table",
        HALF_WIDTH,
        "large",
        service=PICKER,
        order="slowest",
        rows=TABLE_ROWS,
    )
    errors = _panel(
        "t_errors", "error_groups_table", FULL_WIDTH, "large", service=PICKER, rows=TABLE_ROWS
    )
    return _document("Service health", [*charts, p95, findings, endpoints, errors], picker=True)


def _infrastructure() -> DashboardDocument:
    """The busiest machines on each host metric, chosen in the store at read time, since a
    template cannot name a customer's machines (D32)."""
    charts = [
        _panel(
            f"h_{index}", "top_nodes_trend", CHART_WIDTH, "medium", metric=metric, count=TOP_NODES
        )
        for index, metric in enumerate(HOST_METRICS, start=1)
    ]
    return _document("Infrastructure", charts, picker=False)


# FR39's order: the list offers them in this order, then a blank dashboard.
TEMPLATES: Final[dict[str, tuple[str, DashboardDocument]]] = {
    START_FROM_OVERVIEW: ("Start from Overview", _start_from_overview()),
    SERVICE_HEALTH: ("Service health", _service_health()),
    INFRASTRUCTURE: ("Infrastructure", _infrastructure()),
}
