"""Turning trace rows into the shapes the API promises.

No SQL text and no HTTP. What lives here is the arithmetic ClickHouse should
not be asked to do twice — the time window, the page boundary, and the
translation from stored encodings to the ones the contract names.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
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
DEFAULT_WINDOW: Final = timedelta(hours=1)

NANOSECONDS_PER_SECOND: Final = 1_000_000_000
NANOSECONDS_PER_MICROSECOND: Final = 1_000

# A trace id is 32 hex characters; the column is FixedString(32) and will not
# compare against anything else, so the shape is checked before the query runs.
TRACE_ID_LENGTH: Final = 32
_HEX_DIGITS: Final = frozenset("0123456789abcdefABCDEF")

# Python datetimes carry microseconds and the store carries nanoseconds, so the
# window derived from `span_trace_lookup` is widened by a second at each end. A day is
# one partition; a second either way costs nothing and removes a whole class of
# rounding bug at the boundary.
WINDOW_SLACK: Final = timedelta(seconds=1)

# One trace that renders as a waterfall is not a million spans. This bounds the
# response long before the repository's own row cap would.
MAX_SPANS_PER_TRACE: Final = 10_000

_SEVERITY_NAMES: Final[dict[int, str]] = {
    queries.SEVERITY_ERROR: "STATUS_CODE_ERROR",
    queries.SEVERITY_OK: "STATUS_CODE_OK",
    queries.SEVERITY_UNSET: "STATUS_CODE_UNSET",
}
UNKNOWN_STATUS: Final = "STATUS_CODE_UNSET"

TRACE_NOT_FOUND_DETAIL: Final = "No such trace."


def resolve_window(
    from_time: datetime | None, to_time: datetime | None
) -> tuple[datetime, datetime]:
    """The requested window, defaulting to the last hour."""
    end = to_time or datetime.now(UTC)
    start = from_time or end - DEFAULT_WINDOW
    if start >= end:
        raise InvalidRequestError("`from` must be earlier than `to`.")
    return start, end


def resolve_limit(limit: int | None) -> int:
    return DEFAULT_LIMIT if limit is None else min(max(1, limit), MAX_LIMIT)


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
    if not _is_trace_id(trace_id):
        raise NotFoundError(TRACE_NOT_FOUND_DETAIL)

    window = await _trace_window(repository, trace_id)
    if window is None:
        raise NotFoundError(TRACE_NOT_FOUND_DETAIL)

    window_start, window_end = window
    rows = await repository.query(
        queries.TRACE_SPANS_SQL,
        {
            "project_id": project_id,
            "trace_id": trace_id,
            "window_start": window_start,
            "window_end": window_end,
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
        start_time=_nanos_to_datetime(min(starts)),
        duration_ms=(max(ends) - min(starts)) / queries.NANOSECONDS_PER_MILLISECOND,
        span_count=len(spans),
        error_count=sum(1 for span in spans if span.status_code == "STATUS_CODE_ERROR"),
        spans=spans,
    )


async def _trace_window(
    repository: TenantScopedRepository, trace_id: str
) -> tuple[datetime, datetime] | None:
    """The span-timestamp range for a trace, from the id-to-time companion table.

    Without this the spans read would have to scan every partition in the
    retention window, because `spans` is ordered by service and hour, not by id.
    """
    bounds = await repository.query_one(queries.TRACE_TIME_BOUNDS_SQL, {"trace_id": trace_id})
    if bounds is None or not bounds["matches"]:
        return None
    return (
        _nanos_to_datetime(bounds["min_nanos"]) - WINDOW_SLACK,
        _nanos_to_datetime(bounds["max_nanos"]) + WINDOW_SLACK,
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
        trace_id=_text(row["trace_id"]),
        root_service=_text(row["root_service"]),
        root_name=_text(row["root_name"]),
        start_time=_nanos_to_datetime(row["start_time_nanos"]),
        duration_ms=float(row["duration_ms"]),
        span_count=int(row["span_count"]),
        error_count=int(row["error_count"]),
        status_code=_SEVERITY_NAMES.get(int(row["worst_severity"]), UNKNOWN_STATUS),
    )


def _to_span(row: dict[str, Any]) -> SpanDetail:
    return SpanDetail(
        span_id=_text(row["span_id"]),
        parent_span_id=_text(row["parent_span_id"]),
        service_name=_text(row["service_name"]),
        span_name=_text(row["span_name"]),
        span_kind=_text(row["span_kind"]),
        start_time=_nanos_to_datetime(row["start_time_nanos"]),
        duration_ms=float(row["duration_ms"]),
        status_code=_text(row["status_code"]),
        status_message=_text(row["status_message"]),
        http_method=_text(row["http_method"]),
        http_route=_text(row["http_route"]),
        http_status=int(row["http_status"]),
        attributes=_attributes(row["attributes_json"]),
    )


def _attributes(raw: object) -> dict[str, Any]:
    """`span_attrs` arrives as JSON text. Anything unreadable is no attributes."""
    try:
        parsed = json.loads(_text(raw))
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(value: object) -> str:
    """FixedString columns arrive as bytes; everything on the wire is text."""
    if isinstance(value, bytes):
        return value.rstrip(b"\x00").decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def _nanos_to_datetime(nanos: int) -> datetime:
    """Nanoseconds since the epoch, without going through a lossy float."""
    seconds, remainder = divmod(int(nanos), NANOSECONDS_PER_SECOND)
    return datetime.fromtimestamp(seconds, UTC) + timedelta(
        microseconds=remainder // NANOSECONDS_PER_MICROSECOND
    )


def _is_trace_id(value: str) -> bool:
    return len(value) == TRACE_ID_LENGTH and all(character in _HEX_DIGITS for character in value)
