"""Correlating logs with the trace they were emitted under.

No SQL text and no HTTP. The work here is bounding the read: `logs` is
partitioned on our receive clock, so the query needs a `received_at` range, and
the only cheap source of one for a given trace is `span_trace_lookup`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.logs import queries
from nexovue_api.logs.schemas import LogRecord, LogsForTraceResponse, TraceLink
from nexovue_api.traces.service import trace_window
from nexovue_api.window import receive_window

DEFAULT_LIMIT: Final = 200
MAX_LIMIT: Final = 1_000

MALFORMED_TRACE_ID: Final = "`trace_id` must be 32 hexadecimal characters."
NO_TRACE_CONTEXT: Final = "That log record carries no trace context, so it belongs to no trace."


async def logs_for_trace(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    trace_id: str,
    from_time: datetime,
    to_time: datetime,
    limit: int,
) -> LogsForTraceResponse:
    """Every log record emitted under one trace, and the trace it points back at.

    The caller's window is only a fallback. When the trace is known the read is
    bounded by when *that trace* arrived, which is a far smaller range than any
    window a person would ask for.
    """
    _require_trace_id(trace_id)

    window = await trace_window(repository, trace_id)
    if window is None:
        received_from, received_to = receive_window(from_time, to_time)
        link = TraceLink(trace_id=trace_id, exists=False)
    else:
        received_from, received_to = receive_window(window.received_from, window.received_to)
        link = TraceLink(
            trace_id=trace_id,
            exists=True,
            start_time=window.span_start,
            end_time=window.span_end,
        )

    rows = await repository.query(
        queries.LOGS_FOR_TRACE_SQL,
        {
            "project_id": project_id,
            "trace_id": trace_id,
            "received_from": received_from,
            "received_to": received_to,
            "limit": limit,
        },
    )
    return LogsForTraceResponse(trace=link, logs=[_to_record(row) for row in rows])


def _require_trace_id(trace_id: str) -> None:
    """Reject before querying what cannot match, and say which it is.

    An OTLP log record carries an all-zero trace id when it was written outside
    any span. That is a well-formed id meaning "no trace", and it deserves a
    different answer from a typo.
    """
    if not values.is_trace_id(trace_id):
        raise InvalidRequestError(MALFORMED_TRACE_ID)
    if trace_id.lower() == values.ABSENT_TRACE_ID:
        raise InvalidRequestError(NO_TRACE_CONTEXT)


def _to_record(row: dict[str, Any]) -> LogRecord:
    return LogRecord(
        observed_at=values.nanos_to_datetime(row["observed_nanos"]),
        service_name=values.text(row["service_name"]),
        severity=values.text(row["severity"]),
        severity_number=int(row["severity_number"]),
        body=values.text(row["body"]),
        trace_id=values.present_trace_id(row["trace_ref"]),
        span_id=values.present_span_id(row["span_ref"]),
        attributes=values.json_object(row["attributes_json"]),
    )
