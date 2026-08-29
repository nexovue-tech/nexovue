"""Turning rollup rows into the shapes the service overview promises.

No SQL text and no HTTP. What lives here is the arithmetic ClickHouse should not
be asked for — counts turned into rates against the window that produced them —
and the unpacking of the stored quantile state.
"""

from __future__ import annotations

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
    ServiceListResponse,
)

# A project with more services than this is not a list a person reads; the cap
# exists so one request cannot become an unbounded response.
DEFAULT_SERVICE_LIMIT: Final = 100
MAX_SERVICE_LIMIT: Final = 500

# Endpoints outnumber services by a route table, so the page is larger.
DEFAULT_ENDPOINT_LIMIT: Final = 100
MAX_ENDPOINT_LIMIT: Final = 1_000


async def list_services(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    limit: int,
) -> ServiceListResponse:
    """Every service that reported in the window, worst first."""
    rows = await repository.query(
        queries.SERVICES_SQL,
        _window_parameters(project_id, from_time, to_time) | {"limit": limit},
    )
    minutes = rollup.window_minutes(from_time, to_time)
    return ServiceListResponse(
        window_start=from_time,
        window_end=to_time,
        services=[_to_service(row, minutes) for row in rows],
    )


async def list_endpoints(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    service_name: str | None,
    from_time: datetime,
    to_time: datetime,
    limit: int,
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

    rows = await repository.query(
        queries.endpoints_sql(one_service=service_name is not None), parameters
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
