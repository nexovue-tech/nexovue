"""Turning rollup rows into the shapes the service overview promises.

No SQL text and no HTTP. What lives here is the arithmetic ClickHouse should not
be asked for — counts turned into rates against the window that produced them —
and the unpacking of the stored quantile state.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Final

from nexovue_api import rollup
from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.services import queries
from nexovue_api.services.schemas import (
    EndpointHealth,
    EndpointListResponse,
    ServiceHealth,
    ServiceHealthResponse,
    ServiceListResponse,
    ServiceTrend,
    ServiceTrendResponse,
    TrendPoint,
)

# A project with more services than this is not a list a person reads; the cap
# exists so one request cannot become an unbounded response.
DEFAULT_SERVICE_LIMIT: Final = 100
MAX_SERVICE_LIMIT: Final = 500

# Grouping by `service_name` with one name bound can only produce one row; the limit
# says so to ClickHouse rather than trusting the shape.
ONE_SERVICE_LIMIT: Final = 1

# Endpoints outnumber services by a route table, so the page is larger.
DEFAULT_ENDPOINT_LIMIT: Final = 100
MAX_ENDPOINT_LIMIT: Final = 1_000


async def service_trend(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    service_name: str | None,
    from_time: datetime,
    to_time: datetime,
) -> ServiceTrendResponse:
    """Each service's history across the window, for the sparkline in its row.

    Same source and same window as the aggregate beside it, so a row and its
    trend can never disagree about what they are describing.
    """
    bucket_seconds = rollup.trend_bucket_seconds(from_time, to_time)
    parameters = _window_parameters(project_id, from_time, to_time) | {
        "bucket_seconds": bucket_seconds
    }
    if service_name is not None:
        parameters["service_name"] = service_name

    rows = await repository.query(
        queries.trend_sql(one_service=service_name is not None), parameters
    )

    # Each bucket is its own small window, so a rate is against the bucket
    # rather than the whole span — otherwise a quiet minute inside a busy hour
    # would read as the hour's average and the line would be flat.
    bucket_minutes = bucket_seconds / rollup.MIN_BUCKET_SECONDS
    by_service: dict[str, list[TrendPoint]] = defaultdict(list)
    for row in rows:
        latency = rollup.Latency(row["latency_ms"])
        by_service[row["service_name"]].append(
            TrendPoint(
                bucket=row["bucket"],
                requests_per_minute=rollup.per_minute(row["request_count"], bucket_minutes),
                error_rate=rollup.failure_ratio(row["error_count"], row["request_count"]),
                p95_ms=latency.p95_ms,
            )
        )

    return ServiceTrendResponse(
        window_start=from_time,
        window_end=to_time,
        bucket_seconds=bucket_seconds,
        services=[
            ServiceTrend(service_name=name, points=points)
            for name, points in sorted(by_service.items())
        ],
    )


# The two span kinds the rollup keeps apart. A screen asking for "HTTP, not
# queues" is asking for SERVER; the list is here so a typo is a 422 at the edge
# rather than an empty answer the reader would read as "nothing is failing".
SPAN_KINDS: Final = ("SERVER", "CONSUMER")

# How the ranking is ordered. "worst" leads with failures, "slowest" with the
# slow tail — two different questions of the same rows, and both are the store's
# to answer because `LIMIT` cuts whichever order it was given.
ORDERINGS: Final = ("worst", "slowest")


async def list_services(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    limit: int,
    span_kind: str | None = None,
    with_errors: bool = False,
    order: str = "worst",
) -> ServiceListResponse:
    """Every service that reported in the window, worst first."""
    return ServiceListResponse(
        window_start=from_time,
        window_end=to_time,
        services=await _read_services(
            repository,
            project_id=project_id,
            service_name=None,
            from_time=from_time,
            to_time=to_time,
            limit=limit,
            span_kind=span_kind,
            with_errors=with_errors,
            order=order,
        ),
    )


async def get_service(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    service_name: str,
    from_time: datetime,
    to_time: datetime,
) -> ServiceHealthResponse:
    """One service's aggregate over the window, or nothing when it was silent.

    An unknown name is an empty answer rather than a 404, for the same reason
    `list_endpoints` gives: the rollup only knows which services reported in *this*
    window, so "no rows" means "silent here", which is not "does not exist".
    """
    found = await _read_services(
        repository,
        project_id=project_id,
        service_name=service_name,
        from_time=from_time,
        to_time=to_time,
        limit=ONE_SERVICE_LIMIT,
    )
    return ServiceHealthResponse(
        window_start=from_time,
        window_end=to_time,
        service=found[0] if found else None,
    )


async def _read_services(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    service_name: str | None,
    from_time: datetime,
    to_time: datetime,
    limit: int,
    span_kind: str | None = None,
    with_errors: bool = False,
    order: str = "worst",
) -> list[ServiceHealth]:
    """The ranking, for the whole project or for one named service.

    One statement either way. The detail screen and the overview therefore show the
    same numbers for the same service, because they are the same read.
    """
    parameters = _window_parameters(project_id, from_time, to_time) | {"limit": limit}
    if service_name is not None:
        parameters["service_name"] = service_name
    if span_kind is not None:
        parameters["span_kind"] = span_kind

    rows = await repository.query(
        queries.services_sql(
            one_service=service_name is not None,
            span_kind=span_kind is not None,
            with_errors=with_errors,
            order=order,
        ),
        parameters,
    )
    minutes = rollup.window_minutes(from_time, to_time)
    return [_to_service(row, minutes) for row in rows]


async def list_endpoints(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    service_name: str | None,
    from_time: datetime,
    to_time: datetime,
    limit: int,
    span_kind: str | None = None,
    with_errors: bool = False,
    order: str = "worst",
) -> EndpointListResponse:
    """Endpoints ranked by how bad they are, for one service or for the project.

    An unknown service name is an empty list rather than a 404: the rollup only
    knows which services reported in *this* window, so "no rows" means "silent
    here", which is not the same as "does not exist" and must not be reported
    as if it were.
    """
    parameters = _window_parameters(project_id, from_time, to_time) | {"limit": limit}
    if service_name is not None:
        parameters["service_name"] = service_name
    if span_kind is not None:
        parameters["span_kind"] = span_kind

    rows = await repository.query(
        queries.endpoints_sql(
            one_service=service_name is not None,
            span_kind=span_kind is not None,
            with_errors=with_errors,
            order=order,
        ),
        parameters,
    )
    minutes = rollup.window_minutes(from_time, to_time)
    return EndpointListResponse(
        window_start=from_time,
        window_end=to_time,
        endpoints=[_to_endpoint(row, minutes) for row in rows],
    )


def _window_parameters(project_id: str, from_time: datetime, to_time: datetime) -> dict[str, Any]:
    return {"project_id": project_id, "from_time": from_time, "to_time": to_time}


def _to_service(row: dict[str, Any], minutes: float) -> ServiceHealth:
    requests = int(row["request_count"])
    errors = int(row["error_count"])
    latency = rollup.Latency(row["latency_ms"])
    return ServiceHealth(
        service_name=values.text(row["service_name"]),
        request_count=requests,
        error_count=errors,
        requests_per_minute=rollup.per_minute(requests, minutes),
        error_rate=rollup.failure_ratio(errors, requests),
        p50_ms=latency.p50_ms,
        p95_ms=latency.p95_ms,
        p99_ms=latency.p99_ms,
    )


def _to_endpoint(row: dict[str, Any], minutes: float) -> EndpointHealth:
    requests = int(row["request_count"])
    errors = int(row["error_count"])
    latency = rollup.Latency(row["latency_ms"])
    return EndpointHealth(
        service_name=values.text(row["service_name"]),
        span_kind=values.text(row["span_kind"]),
        endpoint=values.text(row["span_name"]),
        request_count=requests,
        error_count=errors,
        requests_per_minute=rollup.per_minute(requests, minutes),
        error_rate=rollup.failure_ratio(errors, requests),
        p50_ms=latency.p50_ms,
        p95_ms=latency.p95_ms,
        p99_ms=latency.p99_ms,
    )
