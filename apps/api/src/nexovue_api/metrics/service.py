"""Turning five tables of points into one catalogue."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.metrics import queries
from nexovue_api.metrics.schemas import MetricCatalogueResponse, MetricSummary
from nexovue_api.window import receive_window

# A catalogue is a list of names, not a report. Past a few hundred it stops
# being readable anyway, and the cap keeps the group-by across five tables
# bounded however many names a customer invents.
MAX_METRICS: Final = 500


async def list_metrics(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
) -> MetricCatalogueResponse:
    """Every metric name this project reported in the window, busiest first.

    Busiest first because a catalogue is read to find something, and the thing
    somebody is looking for is almost never the quietest name in it.
    """
    received_from, received_to = receive_window(from_time, to_time)
    rows = await repository.query(
        queries.CATALOGUE_SQL,
        {
            "project_id": project_id,
            "from_time": from_time,
            "to_time": to_time,
            "received_from": received_from,
            "received_to": received_to,
            # One past the cap, so a full catalogue is told apart from a cut one.
            "limit": MAX_METRICS + 1,
        },
    )

    return MetricCatalogueResponse(
        window_start=from_time,
        window_end=to_time,
        metrics=[_to_summary(row) for row in rows[:MAX_METRICS]],
        truncated=len(rows) > MAX_METRICS,
    )


def _to_summary(row: dict[str, Any]) -> MetricSummary:
    shapes = [values.text(shape) for shape in row["shapes"]]
    return MetricSummary(
        metric_name=values.text(row["metric_name"]),
        shapes=shapes,
        unit=values.text(row["metric_unit"]),
        series=int(row["series"]),
        points=int(row["points"]),
        last_seen=values.nanos_to_datetime(row["latest_nanos"]),
        # Readable when *every* shape it arrived in can be drawn. A name sent as
        # both a histogram and a summary cannot be drawn as one thing, and
        # calling it readable would promise a reader something the screen cannot
        # keep.
        readable=bool(shapes) and all(shape in queries.READABLE_SHAPES for shape in shapes),
    )
