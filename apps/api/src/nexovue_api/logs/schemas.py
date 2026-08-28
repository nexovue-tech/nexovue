"""What the log-correlation endpoint puts on the wire."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp


class TraceLink(BaseModel):
    """The other direction: the trace a log record points at.

    `exists` is false when we hold logs for the trace but not the trace itself
    — its spans were sampled away, or they expired first. The link should not
    be offered in that case, and the caller learns so without a second request.
    """

    trace_id: str
    exists: bool
    start_time: IsoTimestamp | None = None
    end_time: IsoTimestamp | None = None


class LogRecord(BaseModel):
    """One log record, carrying the trace context it was emitted under."""

    observed_at: IsoTimestamp
    service_name: str
    severity: str
    severity_number: int
    body: str
    # Empty string when the record carried no trace context, never null. An
    # uninstrumented logger writes records with neither.
    trace_id: str
    span_id: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class LogsForTraceResponse(BaseModel):
    """Both directions of the correlation in one answer.

    `trace` is what a log links back to; `logs` is what a trace links out to.
    The two share the one `span_trace_lookup` read that bounds the query, so
    answering both costs no more than answering either.
    """

    trace: TraceLink
    # Timestamp order, so a client can render without sorting first.
    logs: list[LogRecord] = Field(default_factory=list)
