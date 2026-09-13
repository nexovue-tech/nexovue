"""What the service overview endpoints put on the wire."""

from __future__ import annotations

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp


class ServiceHealth(BaseModel):
    """One service over the window, as the overview screen shows it."""

    service_name: str
    request_count: int
    error_count: int
    # Rates, so two windows of different lengths can be compared without the
    # reader dividing by the window in their head.
    requests_per_minute: float
    # A fraction, not a percentage: 0.05 is one call in twenty.
    error_rate: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


class EndpointHealth(BaseModel):
    """One endpoint of one service. Worst first is the order that matters."""

    service_name: str
    # SERVER or CONSUMER — an HTTP route and a queue handler can share a name.
    span_kind: str
    endpoint: str
    request_count: int
    error_count: int
    requests_per_minute: float
    error_rate: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


class TrendPoint(BaseModel):
    """One bucket of a service's history.

    The same three numbers the aggregate carries, so the sparkline and the row
    it sits in cannot disagree about what they are showing.
    """

    bucket: IsoTimestamp
    requests_per_minute: float
    error_rate: float
    p95_ms: float


class ServiceTrend(BaseModel):
    """One service's history across the window, oldest first."""

    service_name: str
    points: list[TrendPoint]


class ServiceTrendResponse(BaseModel):
    """What a row's sparkline is drawn from.

    `bucket_seconds` is returned rather than assumed: the server chooses it from
    the window length, so a client that plotted against a minute would silently
    mis-scale its own x axis on any window wider than a few hours.
    """

    window_start: IsoTimestamp
    window_end: IsoTimestamp
    bucket_seconds: int
    services: list[ServiceTrend]


class ServiceListResponse(BaseModel):
    # The window is echoed because the caller may have sent none and taken the
    # default; every rate below is per minute of exactly this window.
    window_start: IsoTimestamp
    window_end: IsoTimestamp
    # An empty window is an empty list, not a 404 — "nothing yet" is the normal
    # state during onboarding.
    services: list[ServiceHealth] = Field(default_factory=list)


class ServiceHealthResponse(BaseModel):
    """One named service over the window, for the screen that is only about it.

    `service` is null when nothing by that name reported, and that is not a 404: the
    rollup knows which services reported in *this* window, not which exist. A service
    that has stopped exporting, or was renamed, is silent here rather than missing.
    """

    window_start: IsoTimestamp
    window_end: IsoTimestamp
    service: ServiceHealth | None = None


class EndpointListResponse(BaseModel):
    window_start: IsoTimestamp
    window_end: IsoTimestamp
    endpoints: list[EndpointHealth] = Field(default_factory=list)


class OverviewPoint(BaseModel):
    """One bucket of the whole project's history.

    Carries all three percentiles, unlike a service's own `TrendPoint`: this is
    the dashboard's duration panel, and the shape of a latency distribution is
    the three lines together — a p99 pulling away from a p95 is the reading.
    """

    bucket: IsoTimestamp
    requests_per_minute: float
    error_rate: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


class ProjectTotals(BaseModel):
    """Every service in the window, merged — never summed from the list.

    `service_count` is `uniqExact` over the rollup rather than the length of the
    service list, which is capped: a project with a hundred and one services
    would otherwise report a hundred and no screen could say why.
    """

    service_count: int
    request_count: int
    error_count: int
    requests_per_minute: float
    error_rate: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


class ServiceOverviewResponse(BaseModel):
    """The dashboard's own read: the project over the window, and across it."""

    window_start: IsoTimestamp
    window_end: IsoTimestamp
    # Chosen by the server from the window length, as the trend read does.
    bucket_seconds: int
    totals: ProjectTotals
    points: list[OverviewPoint] = Field(default_factory=list)
