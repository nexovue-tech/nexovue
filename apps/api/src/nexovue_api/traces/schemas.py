"""What the trace endpoints put on the wire.

Timestamps serialize to millisecond ISO 8601 with a `Z`. Millisecond precision
because that is what a waterfall renders and what a person reads; the store
keeps nanoseconds and `duration_ms` carries the sub-millisecond detail.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Final

from pydantic import BaseModel, Field, PlainSerializer

MILLISECOND_DIGITS: Final = 3
_MICROSECOND_DIGITS: Final = 6


def _to_iso_millis(value: datetime) -> str:
    """`2026-08-27T10:15:04.123Z` — always UTC, always three decimal places."""
    moment = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return (
        moment.strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{moment.microsecond:0{_MICROSECOND_DIGITS}d}"[:MILLISECOND_DIGITS]
        + "Z"
    )


IsoTimestamp = Annotated[datetime, PlainSerializer(_to_iso_millis, return_type=str)]


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
    attributes: dict[str, Any] = Field(default_factory=dict)


class TraceDetailResponse(BaseModel):
    trace_id: str
    start_time: IsoTimestamp
    duration_ms: float
    span_count: int
    error_count: int
    # Start-time order, so a client can render without sorting first.
    spans: list[SpanDetail]
