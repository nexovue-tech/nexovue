"""Turning web vital rollup rows into the shapes the API promises.

No SQL text and no HTTP. What lives here is reading the 75th percentile out of
the merged quantile array, which arrives ordered as the rollup declared it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.rum import queries
from nexovue_api.rum.schemas import (
    BreakdownDimension,
    WebVitalBreakdownResponse,
    WebVitalGroup,
    WebVitalsResponse,
    WebVitalSummary,
)

# Where the 75th percentile sits in the array `quantilesMerge` returns. Derived
# from the rollup's own parameter list so the two cannot drift apart.
P75_POSITION: Final = queries.ROLLUP_QUANTILES.index(0.75)

# How many groups a breakdown returns. A page list is unbounded in principle:
# `url_path` is stored as the browser sent it, so a site with an order id in its
# paths produces a group per order. Ranking by readings first means the cut
# falls on the long tail, and `truncated` says it happened.
MAX_GROUPS: Final = 50

# Which statement answers which dimension. A lookup rather than a column name
# built into the SQL: the chokepoint's structural check refuses any statement
# assembled from a runtime value, and a caller choosing a column is exactly that.
_BREAKDOWN_SQL: Final = {
    BreakdownDimension.PAGE: queries.PAGE_BREAKDOWN_SQL,
    BreakdownDimension.DEVICE: queries.DEVICE_BREAKDOWN_SQL,
}


async def summarize_web_vitals(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
) -> WebVitalsResponse:
    """Each vital's p75 over the window, with the sample size behind it."""
    rows = await repository.query(
        queries.VITAL_SUMMARY_SQL,
        {"project_id": project_id, "from_time": from_time, "to_time": to_time},
    )
    return WebVitalsResponse(vitals=[_to_summary(row) for row in rows])


async def break_down_web_vitals(
    repository: TenantScopedRepository,
    *,
    dimension: BreakdownDimension,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
) -> WebVitalBreakdownResponse:
    """The same p75s, split by page or by device class, busiest group first.

    Every group is returned, however few readings it holds. The threshold that
    decides whether a p75 is worth believing is a presentation rule and lives in
    one place, on the screen; filtering here as well would be a second copy of
    it, and would also hide from a reader that a page was visited at all.
    """
    rows = await repository.query(
        _BREAKDOWN_SQL[dimension],
        {
            "project_id": project_id,
            "from_time": from_time,
            "to_time": to_time,
            # One past the limit, so a full page is told apart from a cut one.
            "group_limit": MAX_GROUPS + 1,
        },
    )
    return WebVitalBreakdownResponse(
        dimension=dimension,
        groups=[_to_group(row) for row in rows[:MAX_GROUPS]],
        truncated=len(rows) > MAX_GROUPS,
    )


def _to_group(row: dict[str, Any]) -> WebVitalGroup:
    """One group's three parallel arrays, zipped back into vitals.

    `groupArray` guarantees the three are the same length and aligned, and
    nothing else — the order between groups is not defined, so the vitals are
    sorted here rather than relied upon.
    """
    vitals = [
        WebVitalSummary(
            name=values.text(name),
            p75=float(quantiles[P75_POSITION]),
            readings=int(readings),
        )
        for name, readings, quantiles in zip(
            row["vital_names"], row["vital_readings"], row["vital_quantiles"], strict=True
        )
    ]
    return WebVitalGroup(
        key=values.text(row["group_key"]),
        vitals=sorted(vitals, key=lambda vital: vital.name),
    )


def _to_summary(row: dict[str, Any]) -> WebVitalSummary:
    return WebVitalSummary(
        name=values.text(row["vital_name"]),
        p75=float(row["quantiles"][P75_POSITION]),
        readings=int(row["readings"]),
    )
