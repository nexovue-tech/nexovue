"""What the trace endpoints put on the wire."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp


class TraceSummary(BaseModel):
    """One row of the trace list: the trace as its root span describes it."""

    trace_id: str
    root_service: str
    root_name: str
    start_time: IsoTimestamp
    duration_ms: float
    span_count: int
    error_count: int
    # The worst status any span in the trace reported.
    status_code: str


class TraceListResponse(BaseModel):
    traces: list[TraceSummary]
    # Null when this page is the last one. An empty window is an empty list, not
    # a 404 — "nothing yet" is the normal state during onboarding.
    next_cursor: str | None = None


class ServiceFacet(BaseModel):
    """One service that sent something in the window, and how much."""

    service_name: str
    spans: int
    traces: int


class OperationFacet(BaseModel):
    """One span name, kept with its service: `GET /` means nothing alone."""

    service_name: str
    operation: str
    spans: int
    traces: int


class TraceFacetsResponse(BaseModel):
    """The filter options this window actually contains, busiest first.

    Counted with no filters applied, which is what lets a screen tell "you
    filtered everything out" apart from "nothing was sent here at all" — and
    name which filter did it.
    """

    services: list[ServiceFacet] = Field(default_factory=list)
    operations: list[OperationFacet] = Field(default_factory=list)


class SpanDetail(BaseModel):
    """One span, flat. The browser builds the tree; the API does not nest."""

    span_id: str
    # Empty string for a root span, never null.
    parent_span_id: str
    service_name: str
    span_name: str
    span_kind: str
    start_time: IsoTimestamp
    duration_ms: float
    status_code: str
    status_message: str
    http_method: str
    http_route: str
    http_status: int
    # Empty on an HTTP span, and the HTTP three are empty on an RPC one. The
    # two code spaces are kept apart: a gRPC status is 0-16, not 200-599.
    rpc_system: str
    rpc_method: str
    rpc_status: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class TraceDetailResponse(BaseModel):
    trace_id: str
    start_time: IsoTimestamp
    duration_ms: float
    span_count: int
    error_count: int
    # Start-time order, so a client can render without sorting first.
    spans: list[SpanDetail]
