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
from nexovue_api.rum.schemas import WebVitalsResponse, WebVitalSummary

# Where the 75th percentile sits in the array `quantilesMerge` returns. Derived
# from the rollup's own parameter list so the two cannot drift apart.
P75_POSITION: Final = queries.ROLLUP_QUANTILES.index(0.75)


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


def _to_summary(row: dict[str, Any]) -> WebVitalSummary:
    return WebVitalSummary(
        name=values.text(row["vital_name"]),
        p75=float(row["quantiles"][P75_POSITION]),
        readings=int(row["readings"]),
    )
