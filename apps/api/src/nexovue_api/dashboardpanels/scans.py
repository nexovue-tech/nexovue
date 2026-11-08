"""One reader per scan key, each calling the existing service function behind a screen.

F20's first rule is that no number is computed twice (design, *Context and goal*): a
panel's numbers come from the same statement and the same row-to-model step as the
screen it came from (BR3). So a reader here only calls that function, with a
repository and the window, and turns its answer into the trend every chart draws.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from nexovue_api.dashboardpanels.shared_reads import Answer
from nexovue_api.dashboards.compile import PROJECT_TREND, SERVICE_TREND, ScanKey
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.services import service as services
from nexovue_api.services.schemas import OverviewPoint, TrendPoint


@dataclass(frozen=True, slots=True)
class Trend:
    """A trend as every time panel reads it: the bucket width and one point per bucket."""

    bucket_seconds: int
    points: list[OverviewPoint] | list[TrendPoint]


Reader = Callable[[TenantScopedRepository, str, ScanKey, datetime, datetime], Awaitable[Answer]]


async def project_trend(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
) -> Answer:
    """Overview's own trend read (`OVERVIEW_TREND_SQL`), the half that draws its charts."""
    trend = await services.project_trend(
        repository, project_id=project_id, from_time=window_start, to_time=window_end
    )
    return _answer(Trend(bucket_seconds=trend.bucket_seconds, points=trend.points))


async def service_trend(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
) -> Answer:
    """The service screen's trend read (`TREND_SQL`), bound to one named service."""
    answer = await services.service_trend(
        repository,
        project_id=project_id,
        service_name=scan.service,
        from_time=window_start,
        to_time=window_end,
    )
    points = answer.services[0].points if answer.services else []
    return _answer(Trend(bucket_seconds=answer.bucket_seconds, points=points))


def _answer(trend: Trend) -> Answer:
    return Answer(value=trend, rows=len(trend.points), read_at=datetime.now(UTC))


READERS: Final[dict[str, Reader]] = {
    PROJECT_TREND: project_trend,
    SERVICE_TREND: service_trend,
}
# Readers of raw telemetry rather than rollups, held to the smaller process cap. None yet.
RAW_READERS: Final[frozenset[str]] = frozenset()
