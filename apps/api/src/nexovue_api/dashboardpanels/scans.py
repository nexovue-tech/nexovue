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
from nexovue_api.dashboards.compile import (
    NODE_TREND,
    PROJECT_TREND,
    SERVICE_TREND,
    TOP_SERVICES_TREND,
    ScanKey,
)
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.infrastructure import service as infrastructure
from nexovue_api.infrastructure.schemas import NodeTrendPoint
from nexovue_api.services import service as services
from nexovue_api.services.schemas import OverviewPoint, ServiceTrend, TrendPoint


@dataclass(frozen=True, slots=True)
class Trend:
    """A trend as every time panel reads it: the bucket width and one point per bucket."""

    bucket_seconds: int
    points: list[OverviewPoint] | list[TrendPoint]


@dataclass(frozen=True, slots=True)
class ServiceTrends:
    """A top-N chart's lines: each chosen service's own trend, in the store's choice."""

    bucket_seconds: int
    services: list[ServiceTrend]


@dataclass(frozen=True, slots=True)
class NodeLine:
    """One node's one metric: the mean of its readings in each bucket."""

    bucket_seconds: int
    points: list[NodeTrendPoint]


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


async def top_services_trend(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
) -> Answer:
    """`TOP_SERVICES_TREND_SQL`: the services list's own ranking chooses the lines in the
    store, and each line is that service's trend (AC26)."""
    if scan.order is None or scan.count is None:
        raise ValueError("a top-N scan names its ordering and its count")
    answer = await services.top_services_trend(
        repository,
        project_id=project_id,
        order=scan.order,
        count=scan.count,
        from_time=window_start,
        to_time=window_end,
    )
    lines = ServiceTrends(bucket_seconds=answer.bucket_seconds, services=answer.services)
    rows = sum(len(line.points) for line in answer.services)
    return Answer(value=lines, rows=rows, read_at=datetime.now(UTC))


async def node_trend(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
) -> Answer:
    """`NODE_TREND_SQL`: one node and one metric, never the fleet read (AC58)."""
    if scan.node is None or scan.metric is None:
        raise ValueError("a node scan names its node and its metric")
    bucket_seconds, points = await infrastructure.node_trend(
        repository,
        project_id=project_id,
        node=infrastructure.NodeKey(
            host=scan.node.host,
            container=scan.node.container,
            reporter=scan.node.reporter,
            reporter_instance=scan.node.reporter_instance,
        ),
        metric_name=scan.metric,
        from_time=window_start,
        to_time=window_end,
    )
    line = NodeLine(bucket_seconds=bucket_seconds, points=points)
    return Answer(value=line, rows=len(points), read_at=datetime.now(UTC))


def _answer(trend: Trend) -> Answer:
    return Answer(value=trend, rows=len(trend.points), read_at=datetime.now(UTC))


READERS: Final[dict[str, Reader]] = {
    PROJECT_TREND: project_trend,
    SERVICE_TREND: service_trend,
    TOP_SERVICES_TREND: top_services_trend,
    NODE_TREND: node_trend,
}
# Readers of raw telemetry rather than rollups, held to the smaller process cap.
RAW_READERS: Final[frozenset[str]] = frozenset({NODE_TREND})
