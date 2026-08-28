"""What the error-grouping endpoints put on the wire."""

from __future__ import annotations

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp

# How a group's identity was decided, so a reader knows whether they are looking
# at their own grouping or ours.
GROUPED_BY_SDK: str = "sdk_fingerprint"
GROUPED_BY_TEMPLATE: str = "type_and_message_template"


class ErrorGroupSummary(BaseModel):
    """One exception, however many times and wherever it happened."""

    group_id: str
    exception_type: str
    # The message with ids, numbers, hosts and addresses collapsed to tokens.
    # This is what the group is; `latest_message` is what one instance said.
    message_template: str
    latest_message: str
    grouped_by: str
    # De-duplicated: an SDK on `logs/dup` emits one exception twice, once as a
    # span event and once as a log record, and that is one occurrence.
    occurrences: int
    first_seen: IsoTimestamp
    last_seen: IsoTimestamp
    services: list[str] = Field(default_factory=list)
    # Where to open one instance. Empty when the newest instance carried no
    # trace context, which an uninstrumented logger's records do not.
    sample_trace_id: str
    sample_span_id: str
    # "span" or "log" — which shape the newest instance arrived in.
    sample_source: str


class ErrorGroupListResponse(BaseModel):
    groups: list[ErrorGroupSummary] = Field(default_factory=list)


class ErrorOccurrence(BaseModel):
    """One instance of a grouped exception."""

    source: str
    service_name: str
    occurred_at: IsoTimestamp
    # Empty string when the record carried no trace context, never null.
    trace_id: str
    span_id: str
    exception_message: str
    # Read out of the span's `events` or the log record's attributes, where it
    # is stored whole rather than promoted. Empty when the SDK sent none.
    stacktrace: str


class ErrorGroupDetailResponse(BaseModel):
    group: ErrorGroupSummary
    # Newest first, so the first entry is the one a reader opens.
    recent: list[ErrorOccurrence] = Field(default_factory=list)
