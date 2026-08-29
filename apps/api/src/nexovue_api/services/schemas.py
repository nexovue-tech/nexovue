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


class ServiceListResponse(BaseModel):
    # The window is echoed because the caller may have sent none and taken the
    # default; every rate below is per minute of exactly this window.
    window_start: IsoTimestamp
    window_end: IsoTimestamp
    # An empty window is an empty list, not a 404 — "nothing yet" is the normal
    # state during onboarding.
    services: list[ServiceHealth] = Field(default_factory=list)


class EndpointListResponse(BaseModel):
    window_start: IsoTimestamp
    window_end: IsoTimestamp
    endpoints: list[EndpointHealth] = Field(default_factory=list)
