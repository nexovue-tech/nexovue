"""Turning trace rows into the shapes the API promises.

No SQL text and no HTTP. What lives here is the arithmetic ClickHouse should
not be asked to do twice — the time window, the page boundary, and the
translation from stored encodings to the ones the contract names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.traces import queries
from nexovue_api.traces.cursor import TracePageCursor, decode_cursor
from nexovue_api.traces.schemas import (
    SpanDetail,
    TraceDetailResponse,
    TraceListResponse,
    TraceSummary,
)

DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200

# Python datetimes carry microseconds and the store carries nanoseconds, so the
# window derived from `span_trace_lookup` is widened by a second at each end. A day is
# one partition; a second either way costs nothing and removes a whole class of
# rounding bug at the boundary.
WINDOW_SLACK: Final = timedelta(seconds=1)

# One trace that renders as a waterfall is not a million spans. This bounds the
# response long before the repository's own row cap would.
MAX_SPANS_PER_TRACE: Final = 10_000

_SEVERITY_NAMES: Final[dict[int, str]] = {
    queries.SEVERITY_ERROR: queries.STATUS_ERROR,
    queries.SEVERITY_OK: queries.STATUS_OK,
    queries.SEVERITY_UNSET: queries.STATUS_UNSET,
}
UNKNOWN_STATUS: Final = queries.STATUS_UNSET

TRACE_NOT_FOUND_DETAIL: Final = "No such trace."


@dataclass(frozen=True, slots=True)
class TraceWindow:
    """When a trace happened, and when it reached us.

    `received_from` and `received_to` are our own clock, which is what every
    telemetry table is partitioned by (ADR 0067). They are what lets a read
    keyed on a trace id prune partitions instead of scanning the retention
    window; the span timestamps cannot, because the customer supplies them.
    """

    span_start: datetime
    span_end: datetime
    received_from: datetime
    received_to: datetime


async def list_traces(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    limit: int,
    cursor: str | None,
) -> TraceListResponse:
    """One page of traces, newest first."""
    page = decode_cursor(cursor) if cursor else None
    parameters: dict[str, Any] = {
        "project_id": project_id,
        "from_time": from_time,
        "to_time": to_time,
        "limit": limit,
    }
    if page is not None:
        parameters["cursor_nanos"] = page.start_time_nanos
        parameters["cursor_trace_id"] = page.trace_id

    rows = await repository.query(queries.list_traces_sql(with_cursor=page is not None), parameters)
    traces = [_to_summary(row) for row in rows]
    return TraceListResponse(traces=traces, next_cursor=_next_cursor(rows, traces, limit))


async def get_trace(
    repository: TenantScopedRepository, *, project_id: str, trace_id: str
) -> TraceDetailResponse:
    """One trace and its spans, or a 404 that says nothing about other tenants."""
    if not values.is_trace_id(trace_id):
        raise NotFoundError(TRACE_NOT_FOUND_DETAIL)

    window = await trace_window(repository, trace_id)
    if window is None:
        raise NotFoundError(TRACE_NOT_FOUND_DETAIL)

    rows = await repository.query(
        queries.TRACE_SPANS_SQL,
        {
            "project_id": project_id,
            "trace_id": trace_id,
            "window_start": window.span_start,
            "window_end": window.span_end,
            "received_from": window.received_from,
            "limit": MAX_SPANS_PER_TRACE,
        },
    )
    # Empty here means the trace exists for this tenant but not in this project.
    if not rows:
        raise NotFoundError(TRACE_NOT_FOUND_DETAIL)

    spans = [_to_span(row) for row in rows]
    starts = [row["start_time_nanos"] for row in rows]
    ends = [
        row["start_time_nanos"] + int(row["duration_ms"] * queries.NANOSECONDS_PER_MILLISECOND)
        for row in rows
    ]
    return TraceDetailResponse(
        trace_id=trace_id,
        start_time=values.nanos_to_datetime(min(starts)),
        duration_ms=(max(ends) - min(starts)) / queries.NANOSECONDS_PER_MILLISECOND,
        span_count=len(spans),
        error_count=sum(1 for span in spans if span.status_code == queries.STATUS_ERROR),
        spans=spans,
    )


async def trace_window(repository: TenantScopedRepository, trace_id: str) -> TraceWindow | None:
    """When a trace happened and when it reached us, or None if we never saw it.

    Without this a read keyed on a trace id would scan every partition in the
    retention window, because the telemetry tables are ordered by service and
    time, not by id. Every signal correlated to a trace goes through here.
    """
    bounds = await repository.query_one(queries.TRACE_TIME_BOUNDS_SQL, {"trace_id": trace_id})
    if bounds is None or not bounds["matches"]:
        return None
    return TraceWindow(
        span_start=values.nanos_to_datetime(bounds["min_nanos"]) - WINDOW_SLACK,
        span_end=values.nanos_to_datetime(bounds["max_nanos"]) + WINDOW_SLACK,
        received_from=values.nanos_to_datetime(bounds["received_from_nanos"]) - WINDOW_SLACK,
        received_to=values.nanos_to_datetime(bounds["received_to_nanos"]) + WINDOW_SLACK,
    )


def _next_cursor(rows: list[dict[str, Any]], traces: list[TraceSummary], limit: int) -> str | None:
    """A cursor only when the page was full; a short page is the last one."""
    if len(rows) < limit or not rows:
        return None
    return TracePageCursor(
        start_time_nanos=int(rows[-1]["start_time_nanos"]),
        trace_id=traces[-1].trace_id,
    ).encode()


def _to_summary(row: dict[str, Any]) -> TraceSummary:
    return TraceSummary(
        trace_id=values.text(row["trace_id"]),
        root_service=values.text(row["root_service"]),
        root_name=values.text(row["root_name"]),
        start_time=values.nanos_to_datetime(row["start_time_nanos"]),
        duration_ms=float(row["duration_ms"]),
        span_count=int(row["span_count"]),
        error_count=int(row["error_count"]),
        status_code=_SEVERITY_NAMES.get(int(row["worst_severity"]), UNKNOWN_STATUS),
    )


def _to_span(row: dict[str, Any]) -> SpanDetail:
    return SpanDetail(
        span_id=values.text(row["span_id"]),
        parent_span_id=values.text(row["parent_span_id"]),
        service_name=values.text(row["service_name"]),
        span_name=values.text(row["span_name"]),
        span_kind=values.text(row["span_kind"]),
        start_time=values.nanos_to_datetime(row["start_time_nanos"]),
        duration_ms=float(row["duration_ms"]),
        status_code=values.text(row["status_code"]),
        status_message=values.text(row["status_message"]),
        http_method=values.text(row["http_method"]),
        http_route=values.text(row["http_route"]),
        http_status=int(row["http_status"]),
        attributes=values.json_object(row["attributes_json"]),
    )
