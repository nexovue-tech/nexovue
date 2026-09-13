"""What the error-grouping endpoints put on the wire."""

from __future__ import annotations

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp

# How a group's identity was decided, so a reader knows whether they are looking
# at their own grouping or ours.
GROUPED_BY_SDK: str = "sdk_fingerprint"
GROUPED_BY_TEMPLATE: str = "type_and_message_template"


class PrecedingDeploy(BaseModel):
    """The last deploy to reach this group's services before it first appeared.

    **Before, not because.** Nothing here establishes cause. It is the most
    recent change we were *told* about, and an exception that began four minutes
    after one may have come from traffic, from a dependency, or from a change
    that never sent a deploy notification. Order is the fact; cause is not.

    Every screen that renders this must say what it is — the deploy before this
    appeared — and must never say, or let a reader infer, that it caused
    anything. `null` means no deploy of these services is recorded before that
    instant, which is also what a project that sends no deploy notifications
    reads as.
    """

    version: str
    service_name: str
    deployed_at: IsoTimestamp


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
    # Whether this group began inside the window rather than before it. Decided
    # here rather than on the screen, so the row's marker and the "only new"
    # filter cannot answer the same question differently.
    started_in_window: bool = False
    last_seen: IsoTimestamp
    services: list[str] = Field(default_factory=list)
    # Where to open one instance. Empty when the newest instance carried no
    # trace context, which an uninstrumented logger's records do not.
    sample_trace_id: str
    sample_span_id: str
    # "span" or "log" — which shape the newest instance arrived in.
    sample_source: str
    # The deploy before this group appeared, when one is recorded. Correlation
    # and nothing more; see `PrecedingDeploy`.
    preceding_deploy: PrecedingDeploy | None = None


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
    # The build this came from, which is what a source map is matched on. Empty
    # when the sender set no version — a browser SDK without `serviceVersion`,
    # or a service that deploys without stamping one — and a stacktrace with no
    # release cannot be symbolicated at all, so the screen has to know.
    release: str = ""


class ErrorGroupDetailResponse(BaseModel):
    group: ErrorGroupSummary
    # Newest first, so the first entry is the one a reader opens.
    recent: list[ErrorOccurrence] = Field(default_factory=list)


class GroupTrendPoint(BaseModel):
    """One bucket of a group's history."""

    bucket: IsoTimestamp
    # Counted exactly as the group's own `occurrences` is, so the sparkline and the
    # number beside it cannot disagree.
    occurrences: int


class GroupTrend(BaseModel):
    """One group's history across the window, oldest first."""

    group_id: str
    points: list[GroupTrendPoint]


class GroupTrendResponse(BaseModel):
    """What each row's sparkline is drawn from.

    `bucket_seconds` is returned rather than assumed: the server chooses it from the
    window length, so a client plotting against a fixed minute would mis-scale its own
    axis on any window wider than an hour.
    """

    window_start: IsoTimestamp
    window_end: IsoTimestamp
    bucket_seconds: int
    groups: list[GroupTrend] = Field(default_factory=list)
