"""The starting points a dashboard opens from (F20, FR39, *The templates*).

Each is a dashboard document, checked by the same validator as a save (FR7, AC80), and
opened as an unsaved draft: opening one writes no row (AC38).

*Start from Overview* is partial until plan Step 5 builds number and table panels: it
holds Overview's three charts, and gains the four totals and worst services then.
"""

from __future__ import annotations

from typing import Final

from nexovue_api.dashboards.definition import DashboardDocument

START_FROM_OVERVIEW: Final = "start_from_overview"


def _project_trend(signal: str) -> dict[str, object]:
    return {
        "id": f"p_{signal}",
        "kind": "project_trend",
        "title": None,
        "width": 4,
        "height": "medium",
        "settings": {"signal": signal},
    }


TEMPLATES: Final[dict[str, tuple[str, DashboardDocument]]] = {
    START_FROM_OVERVIEW: (
        "Start from Overview",
        DashboardDocument.model_validate(
            {
                "title": "Overview",
                "description": "",
                "definition": {
                    "schema_version": 1,
                    "refresh_seconds": 60,
                    "default_range": None,
                    "service_picker": False,
                    "links": [],
                    "panels": [_project_trend(signal) for signal in ("rate", "errors", "latency")],
                },
            }
        ),
    ),
}
