"""Turning trace rows into the shapes the API promises.

No SQL text and no HTTP. What lives here is the arithmetic ClickHouse should
not be asked to do twice — the time window, the page boundary, and the
translation from stored encodings to the ones the contract names — plus the
decisions behind a filter: what a caller may ask for, and which column answers
it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
from nexovue_api.traces import queries
from nexovue_api.traces.cursor import TracePageCursor, decode_cursor
from nexovue_api.traces.queries import TraceFilters
from nexovue_api.traces.schemas import (
    OperationFacet,
    ServiceFacet,
    SpanDetail,
    SpanEvent,
    SpanLink,
    TraceDetailResponse,
    TraceEndpoint,
    TraceFacetsResponse,
    TraceListResponse,
    TraceSummary,
)

DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200

# One facet list is a set of filter options, not a report. Past a few hundred
# entries the control is unusable anyway, and the cap keeps the group-by cheap.
MAX_FACET_ROWS: Final = 500

# Python datetimes carry microseconds and the store carries nanoseconds, so the
# window derived from `span_trace_lookup` is widened by a second at each end. A day is
# one partition; a second either way costs nothing and removes a whole class of
# rounding bug at the boundary.
WINDOW_SLACK: Final = timedelta(seconds=1)

# One trace that renders as a waterfall is not a million spans. This bounds the
# response long before the repository's own row cap would.
MAX_SPANS_PER_TRACE: Final = 10_000

# What a filter value may be. Long enough for a fully-qualified RPC method or a
# URL template, short enough that nothing pathological reaches the store.
MAX_FILTER_VALUE_LENGTH: Final = 512

# An OTLP attribute key is a dotted name. Anything else is a typo or a probe,
# and answering it costs a scan — so it is refused before the query runs. The
# path is a bound parameter either way and could never be SQL.
_ATTRIBUTE_PATH: Final = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./:-]{0,254}$")

STATUS_FILTERS: Final = (queries.STATUS_ERROR, queries.STATUS_OK)

_SEVERITY_NAMES: Final[dict[int, str]] = {
    queries.SEVERITY_ERROR: queries.STATUS_ERROR,
    queries.SEVERITY_OK: queries.STATUS_OK,
    queries.SEVERITY_UNSET: queries.STATUS_UNSET,
}
UNKNOWN_STATUS: Final = queries.STATUS_UNSET

TRACE_NOT_FOUND_DETAIL: Final = "No such trace."
MALFORMED_ATTRIBUTE_PATH: Final = (
    "`attr` must be an attribute name such as `db.system` — letters, digits and `_ . / : -`."
)
ATTRIBUTE_NEEDS_A_VALUE: Final = "`attr` filters on a value, so `attr_value` is required with it."
UNKNOWN_STATUS_FILTER: Final = f"`status` must be one of {', '.join(STATUS_FILTERS)}."
DURATION_RANGE_INVERTED: Final = "`min_duration_ms` must not be greater than `max_duration_ms`."
NEGATIVE_DURATION: Final = "A duration filter must not be negative."


@dataclass(frozen=True, slots=True)
class TraceWindow:
    """When a trace happened, and when it reached us.

    `received_from` and `received_to` are our own clock, which is what every
    telemetry table is partitioned by. They are what lets a read
    keyed on a trace id prune partitions instead of scanning the retention
    window; the span timestamps cannot, because the customer supplies them.
    """

    span_start: datetime
    span_end: datetime
    received_from: datetime
    received_to: datetime


@dataclass(frozen=True, slots=True)
class TraceRow:
    """The strings one trace on the page is described by.

    Fetched for a page of ids rather than for the window: the read that finds
    the page admits no string at all, because a group-by over a window carrying
    strings is what `AGGREGATION_BOUND` exists to prevent.
    """

    root_service: str
    root_name: str
    services: list[str]
    endpoints: list[TraceEndpoint]


# A trace whose page row is missing — possible only if it dropped out between
# the two reads. Empty rather than absent: a trace with no name still has a
# duration worth showing.
EMPTY_TRACE_ROW: Final = TraceRow(root_service="", root_name="", services=[], endpoints=[])


def build_filters(
    *,
    service_name: str | None,
    operation: str | None,
    status: str | None,
    min_duration_ms: float | None,
    max_duration_ms: float | None,
    attribute_path: str | None,
    attribute_value: str | None,
) -> TraceFilters:
    """The filters a caller asked for, or a refusal naming the one that is wrong.

    Every value here was chosen by the caller and is bound as a parameter, so
    none of this is about SQL safety. It is about answering a malformed filter
    with a sentence instead of an empty page that looks like real data.
    """
    if status is not None and status not in STATUS_FILTERS:
        raise InvalidRequestError(UNKNOWN_STATUS_FILTER)

    for duration in (min_duration_ms, max_duration_ms):
        if duration is not None and duration < 0:
            raise InvalidRequestError(NEGATIVE_DURATION)
    if (
        min_duration_ms is not None
        and max_duration_ms is not None
        and min_duration_ms > max_duration_ms
    ):
        raise InvalidRequestError(DURATION_RANGE_INVERTED)

    path = _clean(attribute_path)
    value = _clean(attribute_value)
    if path is not None:
        if not _ATTRIBUTE_PATH.match(path):
            raise InvalidRequestError(MALFORMED_ATTRIBUTE_PATH)
        if value is None:
            raise InvalidRequestError(ATTRIBUTE_NEEDS_A_VALUE)

    return TraceFilters(
        service_name=_clean(service_name),
        operation=_clean(operation),
        attribute_path=path,
        attribute_value=value,
        status=status,
        min_duration_ms=min_duration_ms,
        max_duration_ms=max_duration_ms,
    )


def _clean(value: str | None) -> str | None:
    """A filter value, or None. Whitespace and over-long input are not filters."""
    if value is None:
        return None
    trimmed = value.strip()[:MAX_FILTER_VALUE_LENGTH]
    return trimmed or None


async def list_traces(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    filters: TraceFilters,
    limit: int,
    cursor: str | None,
) -> TraceListResponse:
    """One page of traces, newest first.

    Normally a sort-key read of `trace_summaries`, which the worker maintains
    one row per stored trace. A request naming an arbitrary attribute cannot be
    answered from that row — attribute paths are unbounded — so it falls back to
    grouping `spans`, which is what that costs and why. See `queries`.
    """
    if not filters.answerable_from_summary:
        return await _list_by_grouping_spans(
            repository,
            project_id=project_id,
            from_time=from_time,
            to_time=to_time,
            filters=filters,
            limit=limit,
            cursor=cursor,
        )
    return await _list_from_summaries(
        repository,
        project_id=project_id,
        from_time=from_time,
        to_time=to_time,
        filters=filters,
        limit=limit,
        cursor=cursor,
    )


async def _list_from_summaries(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    filters: TraceFilters,
    limit: int,
    cursor: str | None,
) -> TraceListResponse:
    """The page as a reverse read of the sort key, then its values.

    Two reads, deliberately. The first is bounded by the page rather than by the
    window and holds no aggregate at all; the second merges the rows a trace can
    have more than one of, for these fifty ids only.
    """
    page = decode_cursor(cursor) if cursor else None
    parameters: dict[str, Any] = {
        "project_id": project_id,
        "from_time": from_time,
        "to_time": to_time,
        "limit": limit,
        **_summary_parameters(filters),
    }
    if page is not None:
        parameters["cursor_nanos"] = page.start_time_nanos
        parameters["cursor_trace_id"] = page.trace_id

    positions = await repository.query(
        queries.trace_page_sql(filters, with_cursor=page is not None), parameters
    )
    merged = await _merged_summaries(repository, project_id=project_id, positions=positions)

    traces = [_to_merged_summary(merged[trace_id]) for trace_id in _ids_in_order(positions, merged)]
    return TraceListResponse(traces=traces, next_cursor=_next_cursor(positions, limit))


def _ids_in_order(positions: list[dict[str, Any]], merged: dict[str, dict[str, Any]]) -> list[str]:
    """The page's ids, newest first, each named once.

    A trace can hold more than one summary row, so the ordered read can name the
    same trace twice; collapsing them here rather than in the statement is what
    keeps that read in sort-key order — `queries` has the measurement. A trace
    with no merged row expired between the two reads, which is a row that has
    gone rather than an error to report.
    """
    named: list[str] = []
    seen: set[str] = set()
    for row in positions:
        trace_id = values.text(row["trace_id"])
        if trace_id in seen or trace_id not in merged:
            continue
        seen.add(trace_id)
        named.append(trace_id)
    return named


async def _merged_summaries(
    repository: TenantScopedRepository, *, project_id: str, positions: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Every summary row of this page's traces, folded into one answer each.

    The page's own span of start times is passed with the ids, because it is a
    primary-key range where the ids alone are fifty scattered bloom-filter
    probes — 27k rows read against 1.91M. The page is ordered, so the range is
    its last row to its first.
    """
    if not positions:
        return {}
    starts = [int(row["start_time_nanos"]) for row in positions]
    rows = await repository.query(
        queries.TRACE_PAGE_VALUES_SQL,
        {
            "project_id": project_id,
            "trace_ids": [values.text(row["trace_id"]) for row in positions],
            "page_from_nanos": min(starts),
            "page_to_nanos": max(starts),
        },
    )
    return {values.text(row["trace_id"]): row for row in rows}


def _summary_parameters(filters: TraceFilters) -> dict[str, Any]:
    """Only the values the spliced predicates name, so an unused one is unbound.

    The service and the operation arrive joined, because the stored pair is what
    a span filter has to match: a trace whose service and whose operation sit on
    different spans is not a match.
    """
    bound: dict[str, Any] = {}
    if filters.service_name and filters.operation:
        bound["endpoint"] = filters.service_name + queries.ENDPOINT_SEPARATOR + filters.operation
    elif filters.service_name:
        bound["service_name"] = filters.service_name
    elif filters.operation:
        bound["operation_suffix"] = queries.ENDPOINT_SEPARATOR + filters.operation
    if filters.min_duration_ms is not None:
        bound["min_duration_ms"] = filters.min_duration_ms
    if filters.max_duration_ms is not None:
        bound["max_duration_ms"] = filters.max_duration_ms
    return bound


async def _list_by_grouping_spans(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    filters: TraceFilters,
    limit: int,
    cursor: str | None,
) -> TraceListResponse:
    """The fallback: assemble each trace from its spans across the whole window.

    Kept for the one filter no per-trace row can carry — an arbitrary attribute
    path. It is the expensive shape `trace_summaries` exists to avoid, bounded
    by `AGGREGATION_BOUND` rather than made cheap, and it is reached only when
    the caller asks for `attr`.
    """
    page = decode_cursor(cursor) if cursor else None
    parameters: dict[str, Any] = {
        "project_id": project_id,
        "from_time": from_time,
        "to_time": to_time,
        "limit": limit,
        **_span_filter_parameters(filters),
    }
    if page is not None:
        parameters["cursor_nanos"] = page.start_time_nanos
        parameters["cursor_trace_id"] = page.trace_id

    rows = await repository.query(
        queries.list_traces_sql(filters, with_cursor=page is not None), parameters
    )
    roots = await _root_spans(
        repository,
        project_id=project_id,
        from_time=from_time,
        to_time=to_time,
        trace_ids=[values.text(row["trace_id"]) for row in rows],
    )

    traces = [_to_summary(row, roots) for row in rows]
    return TraceListResponse(traces=traces, next_cursor=_next_cursor(rows, limit))


def _span_filter_parameters(filters: TraceFilters) -> dict[str, Any]:
    """Only the values the spliced predicates name, so an unused one is unbound."""
    bound: dict[str, Any] = {}
    if filters.service_name:
        bound["service_name"] = filters.service_name
    if filters.operation:
        bound["operation"] = filters.operation
    if filters.attribute_path:
        bound["attr_path"] = filters.attribute_path
        bound["attr_value"] = filters.attribute_value
    if filters.min_duration_ms is not None:
        bound["min_duration_ms"] = filters.min_duration_ms
    if filters.max_duration_ms is not None:
        bound["max_duration_ms"] = filters.max_duration_ms
    return bound


async def _root_spans(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    trace_ids: list[str],
) -> dict[str, TraceRow]:
    """What each trace on this page is known by, and what it touched."""
    if not trace_ids:
        return {}

    rows = await repository.query(
        queries.TRACE_ROOTS_SQL,
        {
            "project_id": project_id,
            "from_time": from_time,
            "to_time": to_time,
            "trace_ids": trace_ids,
        },
    )
    return {
        values.text(row["trace_id"]): TraceRow(
            root_service=values.text(row["root_service"]),
            root_name=values.text(row["root_name"]),
            services=[values.text(name) for name in row["span_services"]],
            endpoints=_pairs_to_endpoints(row["span_endpoints"]),
        )
        for row in rows
    }


async def trace_facets(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
) -> TraceFacetsResponse:
    """Which services and operations this window holds, so the filters offer only those."""
    rows = await repository.query(
        queries.TRACE_FACETS_SQL,
        {
            "project_id": project_id,
            "from_time": from_time,
            "to_time": to_time,
            "limit": MAX_FACET_ROWS,
        },
    )
    return TraceFacetsResponse(services=_service_facets(rows), operations=_operation_facets(rows))


def _service_facets(rows: list[dict[str, Any]]) -> list[ServiceFacet]:
    """One row per service, folded up from the (service, operation) grid.

    Trace counts are summed rather than re-counted: a trace crossing two
    operations of one service is counted twice, which overstates a busy
    service. It ranks a dropdown, and ranking is all it is used for.
    """
    spans: dict[str, int] = {}
    traces: dict[str, int] = {}
    for row in rows:
        name = values.text(row["service_name"])
        spans[name] = spans.get(name, 0) + int(row["spans"])
        traces[name] = traces.get(name, 0) + int(row["traces"])

    ranked = sorted(spans.items(), key=lambda entry: (-entry[1], entry[0]))
    return [
        ServiceFacet(service_name=name, spans=count, traces=traces[name]) for name, count in ranked
    ]


def _operation_facets(rows: list[dict[str, Any]]) -> list[OperationFacet]:
    return [
        OperationFacet(
            service_name=values.text(row["service_name"]),
            operation=values.text(row["span_name"]),
            spans=int(row["spans"]),
            traces=int(row["traces"]),
        )
        for row in rows
    ]


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


def _next_cursor(rows: list[dict[str, Any]], limit: int) -> str | None:
    """A cursor only when the page was full; a short page is the last one.

    Built from the ordered row rather than from the response, so the keyset
    names a position in the statement's own order even when a trace dropped out
    between the two reads.
    """
    if len(rows) < limit or not rows:
        return None
    return TracePageCursor(
        start_time_nanos=int(rows[-1]["start_time_nanos"]),
        trace_id=values.text(rows[-1]["trace_id"]),
    ).encode()


def _to_merged_summary(row: dict[str, Any]) -> TraceSummary:
    """One row of `trace_summaries`, already folded across the trace's rows."""
    return TraceSummary(
        trace_id=values.text(row["trace_id"]),
        root_service=values.text(row["root_service"]),
        root_name=values.text(row["root_name"]),
        start_time=values.nanos_to_datetime(row["start_time_nanos"]),
        duration_ms=float(row["duration_ms"]),
        span_count=int(row["span_count"]),
        error_count=int(row["error_count"]),
        status_code=_SEVERITY_NAMES.get(int(row["worst_severity"]), UNKNOWN_STATUS),
        span_services=sorted(values.text(name) for name in row["span_services"]),
        span_endpoints=_split_stored_endpoints(row["span_endpoints"]),
    )


def _to_summary(row: dict[str, Any], roots: dict[str, TraceRow]) -> TraceSummary:
    trace_id = values.text(row["trace_id"])
    # Both reads cover the same window for the same tenant, so a page trace
    # always has a root here. Empty rather than absent if that ever stops being
    # true: a trace with no name still has a duration worth showing.
    touched = roots.get(trace_id, EMPTY_TRACE_ROW)
    root_service, root_name = touched.root_service, touched.root_name
    return TraceSummary(
        trace_id=trace_id,
        root_service=root_service,
        root_name=root_name,
        start_time=values.nanos_to_datetime(row["start_time_nanos"]),
        duration_ms=float(row["duration_ms"]),
        span_count=int(row["span_count"]),
        error_count=int(row["error_count"]),
        status_code=_SEVERITY_NAMES.get(int(row["worst_severity"]), UNKNOWN_STATUS),
        span_services=sorted(values.text(name) for name in touched.services),
        span_endpoints=touched.endpoints,
    )


def _split_stored_endpoints(stored: Any) -> list[TraceEndpoint]:
    """`trace_summaries.span_endpoints`, which packs each pair into one string.

    A pair the separator is missing from is dropped rather than guessed at: the
    worker writes both halves together, so a string without one did not come
    from there, and inventing a service name for it would put a service on the
    row that never ran.
    """
    endpoints = []
    for pair in stored:
        service_name, separator, operation = values.text(pair).partition(queries.ENDPOINT_SEPARATOR)
        if not separator:
            continue
        endpoints.append(TraceEndpoint(service_name=service_name, operation=operation))
    return _ordered(endpoints)


def _pairs_to_endpoints(stored: Any) -> list[TraceEndpoint]:
    """The same field read from `spans`, where each pair arrives as a tuple.

    Nothing is packed on this path, so nothing has to be taken apart — which is
    the point: the separator is the worker's storage detail, and only the read
    of its table should know about it.
    """
    return _ordered(
        TraceEndpoint(service_name=values.text(pair[0]), operation=values.text(pair[1]))
        for pair in stored
    )


def _ordered(endpoints: Iterable[TraceEndpoint]) -> list[TraceEndpoint]:
    """Sorted, because both stores return these unordered.

    `groupUniqArray` has no order to preserve, so without this the same request
    twice can answer with the same set arranged differently — which reads as a
    change and makes a response impossible to diff.
    """
    return sorted(endpoints, key=lambda entry: (entry.service_name, entry.operation))


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
        rpc_system=values.text(row["rpc_system"]),
        rpc_method=values.text(row["rpc_method"]),
        rpc_status=values.text(row["rpc_status"]),
        exception_type=values.text(row["exception_type"]),
        exception_message=values.text(row["exception_message"]),
        deployment_version=values.text(row["deployment_version"]),
        events=_to_events(row["events_json"]),
        links=_to_links(row),
        attributes=values.json_object(row["attributes_json"]),
    )


def _to_events(stored: object) -> list[SpanEvent]:
    """`spans.events`, which the worker stores as one OTLP-shaped JSON string.

    Each entry carries its own clock. An entry missing a timestamp is dropped
    rather than placed at the span's start: an event's position inside the span
    is the whole reason a timeline is worth drawing, and putting an unplaced one
    at the beginning would invent that position.
    """
    events = []
    for entry in values.json_array(stored):
        if not isinstance(entry, dict):
            continue
        at = _event_time(entry.get("timestamp"))
        if at is None:
            continue
        attributes = entry.get("attributes")
        events.append(
            SpanEvent(
                at=at,
                name=values.text(entry.get("name")),
                attributes=attributes if isinstance(attributes, dict) else {},
            )
        )
    # Ordered, because a timeline out of order is not one. The store preserves
    # the exporter's order and an exporter is not required to sort.
    return sorted(events, key=lambda event: event.at)


def _event_time(raw: object) -> datetime | None:
    """An OTLP event timestamp, or None when it is not one."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_links(row: dict[str, Any]) -> list[SpanLink]:
    """Three parallel arrays, zipped back into the links they describe.

    `strict=False` on purpose: the arrays come from one nested column and cannot
    disagree in length, and a `strict=True` zip would turn an impossible state
    into a 500 rather than a shorter list. A link with no trace id is dropped —
    it points nowhere, and offering it would be offering a dead end.
    """
    links = []
    for trace_id, span_id, attributes in zip(
        row["link_trace_ids"], row["link_span_ids"], row["link_attributes"], strict=False
    ):
        target = values.text(trace_id)
        if not target:
            continue
        links.append(
            SpanLink(
                trace_id=target,
                span_id=values.text(span_id),
                attributes=values.json_object(attributes),
            )
        )
    return links
