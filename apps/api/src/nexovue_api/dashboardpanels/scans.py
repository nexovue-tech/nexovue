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

from nexovue_platform.releases import EMPTY_DEPLOY_HISTORY, DeployHistory

from nexovue_api.dashboardpanels.shared_reads import Answer
from nexovue_api.dashboards.compile import (
    CHECK_TREND,
    DEPENDENCIES,
    ENDPOINT_TREND,
    ENDPOINTS_RANKED,
    ERROR_GROUPS,
    METRIC_REPORTS,
    METRIC_SERIES,
    NODE_TREND,
    PROJECT_TOTALS,
    PROJECT_TREND,
    SERVICE_TOTALS,
    SERVICE_TREND,
    SERVICES_RANKED,
    SLOW_STATEMENTS,
    STATEMENT_TREND,
    TOP_NODES_TREND,
    TOP_SERVICES_TREND,
    WEB_VITALS,
    ScanKey,
)
from nexovue_api.dashboards.definition import MAX_TABLE_ROWS
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.dbstats import service as dbstats
from nexovue_api.dbstats.schemas import StatementCost, StatementTrendPoint
from nexovue_api.errorgroups import service as errorgroups
from nexovue_api.errorgroups.schemas import ErrorGroupSummary
from nexovue_api.infrastructure import service as infrastructure
from nexovue_api.infrastructure.schemas import NodeTrendPoint
from nexovue_api.metrics import service as metrics
from nexovue_api.releases.service import CoverageBook
from nexovue_api.rum import service as rum
from nexovue_api.rum.schemas import WebVitalSummary
from nexovue_api.servicemap import service as servicemap
from nexovue_api.servicemap.schemas import ServiceEdge
from nexovue_api.services import service as services
from nexovue_api.services.schemas import (
    EndpointHealth,
    OverviewPoint,
    ServiceHealth,
    ServiceTrend,
    TrendPoint,
)
from nexovue_api.synthetics import service as synthetics
from nexovue_api.synthetics.schemas import CheckTrendPoint


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


@dataclass(frozen=True, slots=True)
class NodeLines:
    """A top-nodes chart's lines: each chosen node's own line, in the store's choice."""

    bucket_seconds: int
    lines: list[tuple[infrastructure.NodeKey, list[NodeTrendPoint]]]


@dataclass(frozen=True, slots=True)
class CheckLine:
    """One check's buckets: runs, successful runs and the mean duration of those."""

    bucket_seconds: int
    points: list[CheckTrendPoint]


@dataclass(frozen=True, slots=True)
class StatementLine:
    bucket_seconds: int
    points: list[StatementTrendPoint]


TableRows = (
    list[ServiceHealth]
    | list[EndpointHealth]
    | list[ServiceEdge]
    | list[ErrorGroupSummary]
    | list[StatementCost]
    | list[WebVitalSummary]
)


@dataclass(frozen=True, slots=True)
class Ranked:
    """A table's rows: its screen's first `MAX_TABLE_ROWS`, in the store's order."""

    rows: TableRows


@dataclass(frozen=True, slots=True)
class ScanContext:
    """What a reader needs from Postgres, read before any scan (design section 5, step 2):
    the deploys around the window and each service's deploy coverage, which the errors
    screen's rows carry. Empty unless an error groups table is on the page."""

    deploys: DeployHistory = EMPTY_DEPLOY_HISTORY
    coverage: CoverageBook | None = None


Reader = Callable[
    [TenantScopedRepository, str, ScanKey, datetime, datetime, ScanContext], Awaitable[Answer]
]


async def project_trend(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
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
    context: ScanContext,
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
    context: ScanContext,
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
    return _kept(lines, sum(len(line.points) for line in answer.services))


async def node_trend(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
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
    return _kept(NodeLine(bucket_seconds=bucket_seconds, points=points), len(points))


async def top_nodes_trend(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
) -> Answer:
    """`TOP_NODES_TREND_SQL`: the nodes highest on a metric, chosen in the store (AC26)."""
    if scan.metric is None or scan.count is None:
        raise ValueError("a top-nodes scan names its metric and its count")
    bucket_seconds, lines = await infrastructure.top_nodes_trend(
        repository,
        project_id=project_id,
        metric_name=scan.metric,
        count=scan.count,
        from_time=window_start,
        to_time=window_end,
    )
    rows = sum(len(points) for _, points in lines)
    return _kept(NodeLines(bucket_seconds=bucket_seconds, lines=lines), rows)


async def endpoint_trend(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
) -> Answer:
    """`ENDPOINT_TREND_SQL`: one endpoint of one service (T5.1a)."""
    if scan.service is None or scan.span_kind is None or scan.endpoint is None:
        raise ValueError("an endpoint scan names its service, span kind and endpoint")
    answer = await services.endpoint_trend(
        repository,
        project_id=project_id,
        service_name=scan.service,
        span_kind=scan.span_kind,
        endpoint=scan.endpoint,
        from_time=window_start,
        to_time=window_end,
    )
    points = answer.services[0].points if answer.services else []
    return _answer(Trend(bucket_seconds=answer.bucket_seconds, points=points))


async def check_trend(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
) -> Answer:
    """`CHECK_TREND_SQL`: one check, by the id the read resolved from its name (AC95)."""
    if scan.check_id is None:
        raise ValueError("a check scan names the check it resolved")
    bucket_seconds, points = await synthetics.check_trend(
        repository,
        project_id=project_id,
        check_id=scan.check_id,
        from_time=window_start,
        to_time=window_end,
    )
    return _kept(CheckLine(bucket_seconds=bucket_seconds, points=points), len(points))


async def statement_trend(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
) -> Answer:
    """`STATEMENT_TREND_SQL`: one statement's hash on one server (T5.1d)."""
    if scan.statement is None:
        raise ValueError("a statement scan names its statement")
    bucket_seconds, points = await dbstats.statement_trend(
        repository,
        project_id=project_id,
        query_hash=scan.statement.query_hash,
        server_address=scan.statement.server_address,
        server_port=scan.statement.server_port,
        from_time=window_start,
        to_time=window_end,
    )
    return _kept(StatementLine(bucket_seconds=bucket_seconds, points=points), len(points))


async def project_totals(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
) -> Answer:
    """Overview's totals (`OVERVIEW_SQL`), the half that draws its four numbers."""
    covered = await services.covered_project_totals(
        repository, project_id=project_id, from_time=window_start, to_time=window_end
    )
    return _kept(covered, 1)


async def service_totals(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
) -> Answer:
    """The service screen's own row (`services_sql`, one service)."""
    if scan.service is None:
        raise ValueError("a service number names its service")
    covered = await services.service_totals(
        repository,
        project_id=project_id,
        service_name=scan.service,
        from_time=window_start,
        to_time=window_end,
    )
    return _kept(covered, 1)


async def services_ranked(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
) -> Answer:
    """The services list, worst or slowest first, its first page."""
    answer = await services.list_services(
        repository,
        project_id=project_id,
        from_time=window_start,
        to_time=window_end,
        limit=MAX_TABLE_ROWS,
        order=scan.order or "worst",
    )
    return _ranked(answer.services)


async def endpoints_ranked(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
) -> Answer:
    """The endpoints list, for one service or the project, its first page."""
    answer = await services.list_endpoints(
        repository,
        project_id=project_id,
        service_name=scan.service,
        from_time=window_start,
        to_time=window_end,
        limit=MAX_TABLE_ROWS,
        order=scan.order or "worst",
    )
    return _ranked(answer.endpoints)


async def dependencies(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
) -> Answer:
    """The service map's busiest edges, or only those that failed."""
    answer = await servicemap.get_service_map(
        repository,
        project_id=project_id,
        from_time=window_start,
        to_time=window_end,
        limit=MAX_TABLE_ROWS,
        failing_only=bool(scan.failing_only),
    )
    return _ranked(answer.edges)


async def error_groups(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
) -> Answer:
    """The errors screen's groups, most frequent first, for one service or the project,
    each with the deploy before it and its service's coverage, as the screen's own rows."""
    answer = await errorgroups.list_error_groups(
        repository,
        project_id=project_id,
        service_name=scan.service,
        from_time=window_start,
        to_time=window_end,
        limit=MAX_TABLE_ROWS,
        deploys=context.deploys,
        coverage=context.coverage,
    )
    return _ranked(answer.groups)


async def slow_statements(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
) -> Answer:
    """The database screen's slowest statements."""
    answer = await dbstats.slow_statements(
        repository,
        project_id=project_id,
        from_time=window_start,
        to_time=window_end,
        limit=MAX_TABLE_ROWS,
    )
    return _ranked(answer.statements)


async def web_vitals(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
) -> Answer:
    """The browser screen's Core Web Vitals, for one device class or all."""
    answer = await rum.summarize_web_vitals(
        repository,
        project_id=project_id,
        from_time=window_start,
        to_time=window_end,
        device_class=scan.device_class,
    )
    return _ranked(answer.vitals)


async def metric_series(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
) -> Answer:
    """One gauge or sum as one line: a chosen series, or every series combined (T8.2)."""
    if scan.metric is None or scan.shape not in SERIES_SHAPES or scan.aggregation is None:
        raise ValueError("a metric series scan names its metric, shape and aggregation")
    line = await metrics.metric_series(
        repository,
        project_id=project_id,
        metric_name=scan.metric,
        shape=SERIES_SHAPES[scan.shape],
        service_name=scan.service,
        series_hash=scan.series_hash,
        aggregation=AGGREGATIONS[scan.aggregation],
        from_time=window_start,
        to_time=window_end,
    )
    return _kept(line, len(line.points))


async def metric_reports(
    repository: TenantScopedRepository,
    project_id: str,
    scan: ScanKey,
    window_start: datetime,
    window_end: datetime,
    context: ScanContext,
) -> Answer:
    """Whether the metric reports under any attributes: a chosen series' second read."""
    if scan.metric is None or scan.shape not in SERIES_SHAPES:
        raise ValueError("a metric reports scan names its metric and shape")
    reports = await metrics.metric_reports(
        repository,
        project_id=project_id,
        metric_name=scan.metric,
        shape=SERIES_SHAPES[scan.shape],
        service_name=scan.service,
        from_time=window_start,
        to_time=window_end,
    )
    return _kept(reports, 1)


# The definition's values, as the metrics service's own literals.
SERIES_SHAPES: Final[dict[str, metrics.SeriesShape]] = {"gauge": "gauge", "sum": "sum"}
AGGREGATIONS: Final[dict[str, metrics.Aggregation]] = {
    "mean": "mean",
    "min": "min",
    "max": "max",
    "sum": "sum",
}


def _answer(trend: Trend) -> Answer:
    return _kept(trend, len(trend.points))


def _ranked(rows: TableRows) -> Answer:
    return _kept(Ranked(rows=rows), len(rows))


def _kept(value: object, rows: int) -> Answer:
    return Answer(value=value, rows=rows, read_at=datetime.now(UTC))


READERS: Final[dict[str, Reader]] = {
    PROJECT_TREND: project_trend,
    SERVICE_TREND: service_trend,
    TOP_SERVICES_TREND: top_services_trend,
    NODE_TREND: node_trend,
    TOP_NODES_TREND: top_nodes_trend,
    ENDPOINT_TREND: endpoint_trend,
    CHECK_TREND: check_trend,
    STATEMENT_TREND: statement_trend,
    PROJECT_TOTALS: project_totals,
    SERVICE_TOTALS: service_totals,
    SERVICES_RANKED: services_ranked,
    ENDPOINTS_RANKED: endpoints_ranked,
    DEPENDENCIES: dependencies,
    ERROR_GROUPS: error_groups,
    SLOW_STATEMENTS: slow_statements,
    WEB_VITALS: web_vitals,
    METRIC_SERIES: metric_series,
    METRIC_REPORTS: metric_reports,
}
# Readers of raw telemetry rather than rollups (FR6's "Raw data" rows), held to the
# smaller process cap and to a window limit (FR17).
RAW_READERS: Final[frozenset[str]] = frozenset(
    {
        NODE_TREND,
        TOP_NODES_TREND,
        CHECK_TREND,
        STATEMENT_TREND,
        ERROR_GROUPS,
        SLOW_STATEMENTS,
        METRIC_SERIES,
        METRIC_REPORTS,
    }
)
# Raw readers of one series, which may read the longer window (FR17).
SINGLE_SERIES_READERS: Final[frozenset[str]] = frozenset({NODE_TREND, CHECK_TREND})
