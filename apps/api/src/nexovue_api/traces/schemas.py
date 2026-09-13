"""What the trace endpoints put on the wire."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp


class TraceEndpoint(BaseModel):
    """One (service, operation) pair a trace touched.

    Split rather than shipped joined: the store keeps the pair as one string
    with a unit separator between the halves, and a client that had to know that
    would be parsing our storage format. `GET /` means nothing without the
    service that served it, which is why the pair is kept together at all.
    """

    service_name: str
    operation: str


class TraceSummary(BaseModel):
    """One row of the trace list: the trace as its root span describes it."""

    trace_id: str
    root_service: str
    root_name: str
    start_time: IsoTimestamp
    duration_ms: float
    span_count: int
    error_count: int
    # The worst status any span in the trace reported. This *is* the stored
    # `worst_severity`, mapped back to a name — the store keeps a number because
    # a max over a number is a merge it performs for free.
    status_code: str
    # Every service and every (service, operation) the trace touched, not only
    # its root. A row can say what a request went through without opening it.
    #
    # These already narrow the list — `SUMMARY_SERVICE_CLAUSE` filters on them —
    # so a reader can go from seeing a service on a row to seeing every trace
    # that touched it. Sorted, because the store returns them unordered and an
    # answer that reshuffles between identical requests is not one.
    span_services: list[str] = Field(default_factory=list)
    span_endpoints: list[TraceEndpoint] = Field(default_factory=list)


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


class SpanEvent(BaseModel):
    """One thing that happened inside a span, at a moment inside its duration.

    An exception is the common one, and its stacktrace lives in `attributes`
    rather than in a field of its own — this is the OTLP shape, and flattening
    it here would make the two disagree.
    """

    at: IsoTimestamp
    name: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class SpanLink(BaseModel):
    """Another span this one points at, across traces.

    A queue consumer links back to the producer's span: the two are causally
    related and are not the same trace, which is the case `edge_kind = queue`
    on the service map is derived from.
    """

    trace_id: str
    span_id: str
    attributes: dict[str, Any] = Field(default_factory=dict)


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
    # What the span threw, when it threw. Stored on the span itself rather than
    # only inside `events`, which is what lets a detail pane lead with it
    # without parsing a JSON blob first. Empty strings on a span that did not
    # fail — never null, for the same reason `parent_span_id` is not.
    exception_type: str = ""
    exception_message: str = ""
    # The build this span ran on, from `service.version`. Empty when the service
    # did not say. It answers "did a deploy cause this" for one span, without
    # leaving the trace.
    deployment_version: str = ""
    # Stored as one JSON string and three parallel arrays respectively; both are
    # taken apart here so a client never parses our storage shapes.
    #
    # Returned in full on a trace detail and never in a list: measured over six
    # hours, the worst single trace carries 28 KiB of events and the mean span
    # carries 33 bytes, which is nothing beside the spans themselves. A *list*
    # read is the one that cannot afford them — `errorgroups/queries.py` refuses
    # a stacktrace in one for that reason.
    events: list[SpanEvent] = Field(default_factory=list)
    links: list[SpanLink] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class TraceDetailResponse(BaseModel):
    trace_id: str
    start_time: IsoTimestamp
    duration_ms: float
    span_count: int
    error_count: int
    # Start-time order, so a client can render without sorting first.
    spans: list[SpanDetail]
