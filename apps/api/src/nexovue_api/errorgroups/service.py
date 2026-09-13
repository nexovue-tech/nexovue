"""Turning error rows into the shapes the API promises.

No SQL text and no HTTP. What lives here is the part ClickHouse should not be
asked to do: widening the window onto our receive clock so partitions prune,
and digging the stacktrace out of the JSON it was left in.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Final

from nexovue_api import rollup
from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.errorgroups import queries
from nexovue_api.errorgroups.schemas import (
    GROUPED_BY_SDK,
    GROUPED_BY_TEMPLATE,
    ErrorGroupDetailResponse,
    ErrorGroupListResponse,
    ErrorGroupSummary,
    ErrorOccurrence,
    GroupTrend,
    GroupTrendPoint,
    GroupTrendResponse,
)
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.window import receive_window

DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200

# How many instances a detail view shows. Enough to see whether an error is one
# service or all of them; far fewer than a group can hold.
OCCURRENCE_SAMPLE_SIZE: Final = 20

# The OTLP names the stacktrace arrives under, whichever signal carried it.
EXCEPTION_EVENT_NAME: Final = "exception"
EXCEPTION_TYPE_ATTRIBUTE: Final = "exception.type"
EXCEPTION_MESSAGE_ATTRIBUTE: Final = "exception.message"
EXCEPTION_STACKTRACE_ATTRIBUTE: Final = "exception.stacktrace"

GROUP_NOT_FOUND_DETAIL: Final = "No such error group in this window."

# One group's row needs its own series; a page of fifty needs fifty. Sized from the
# shared bucket arithmetic so the cap moves with it rather than drifting from it.
MAX_TREND_ROWS: Final = MAX_LIMIT * rollup.TREND_POINTS


async def list_error_groups(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    service_name: str | None,
    from_time: datetime,
    to_time: datetime,
    limit: int,
) -> ErrorGroupListResponse:
    """The window's error groups, the most frequent first, for one service or all.

    The name narrows the statement rather than the result. Filtering a capped
    project-wide page in Python would silently under-report a quiet service's
    exceptions — the groups that fell off the end of the page would simply be missing,
    and nothing on the screen could say so.

    An unknown service name is an empty list rather than a 404, for the same reason
    the service reads give: a service can be silent in a window without ceasing to
    exist.
    """
    parameters = _window_parameters(project_id, from_time, to_time) | {"limit": limit}
    if service_name is not None:
        parameters["service_name"] = service_name

    rows = await repository.query(
        queries.error_groups_sql(one_group=False, one_service=service_name is not None),
        parameters,
    )
    return ErrorGroupListResponse(groups=[_to_summary(row) for row in rows])


async def get_error_group(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    group_id: str,
    from_time: datetime,
    to_time: datetime,
) -> ErrorGroupDetailResponse:
    """One group and the instances behind it, or a 404.

    A group id is derived from the rows in a window, so a group that existed
    yesterday is genuinely absent from today's — "not in this window" is the
    honest answer rather than "never happened".
    """
    parameters = _window_parameters(project_id, from_time, to_time)
    summary_row = await repository.query_one(
        queries.error_groups_sql(one_group=True, one_service=False),
        parameters | {"group_id": group_id, "limit": 1},
    )
    if summary_row is None:
        raise NotFoundError(GROUP_NOT_FOUND_DETAIL)

    occurrence_rows = await repository.query(
        queries.GROUP_OCCURRENCES_SQL,
        parameters | {"group_id": group_id, "limit": OCCURRENCE_SAMPLE_SIZE},
    )
    return ErrorGroupDetailResponse(
        group=_to_summary(summary_row),
        recent=[_to_occurrence(row) for row in occurrence_rows],
    )


def _window_parameters(project_id: str, from_time: datetime, to_time: datetime) -> dict[str, Any]:
    """The event-time window, plus the receive-time window that prunes for it."""
    received_from, received_to = receive_window(from_time, to_time)
    return {
        "project_id": project_id,
        "from_time": from_time,
        "to_time": to_time,
        "received_from": received_from,
        "received_to": received_to,
    }


def _to_summary(row: dict[str, Any]) -> ErrorGroupSummary:
    return ErrorGroupSummary(
        group_id=values.text(row["group_id"]),
        exception_type=values.text(row["exception_type"]),
        message_template=values.text(row["message_template"]),
        latest_message=values.text(row["latest_message"]),
        grouped_by=GROUPED_BY_SDK if int(row["grouped_by_sdk"]) else GROUPED_BY_TEMPLATE,
        occurrences=int(row["occurrences"]),
        first_seen=values.nanos_to_datetime(row["first_seen_nanos"]),
        last_seen=values.nanos_to_datetime(row["last_seen_nanos"]),
        services=[values.text(service) for service in row["services"]],
        sample_trace_id=values.present_trace_id(row["sample_trace_id"]),
        sample_span_id=values.present_span_id(row["sample_span_id"]),
        sample_source=values.text(row["sample_source"]),
    )


def _to_occurrence(row: dict[str, Any]) -> ErrorOccurrence:
    source = values.text(row["source"])
    message = values.text(row["exception_message"])
    return ErrorOccurrence(
        source=source,
        service_name=values.text(row["service_name"]),
        occurred_at=values.nanos_to_datetime(row["occurred_nanos"]),
        trace_id=values.present_trace_id(row["trace_ref"]),
        span_id=values.present_span_id(row["span_ref"]),
        exception_message=message,
        release=values.text(row["release"]),
        stacktrace=_stacktrace(
            source=source,
            detail_json=row["detail_json"],
            exception_type=values.text(row["exception_type"]),
            exception_message=message,
        ),
    )


def _stacktrace(
    *, source: str, detail_json: object, exception_type: str, exception_message: str
) -> str:
    """The stacktrace, from wherever the signal that carried it keeps one."""
    if source == queries.SOURCE_LOG:
        return values.attribute(values.json_object(detail_json), EXCEPTION_STACKTRACE_ATTRIBUTE)
    return _span_event_stacktrace(detail_json, exception_type, exception_message)


def _span_event_stacktrace(detail_json: object, exception_type: str, exception_message: str) -> str:
    """The stacktrace of the matching `exception` event in a span's events.

    A span can record several exceptions, so the one whose type and message the
    promoted columns hold is preferred; the first is the fallback, because a
    masked or truncated message would otherwise match nothing.
    """
    first_stacktrace = ""
    for event in values.json_array(detail_json):
        if not isinstance(event, dict) or event.get("name") != EXCEPTION_EVENT_NAME:
            continue
        attributes = event.get("attributes")
        if not isinstance(attributes, dict):
            continue

        stacktrace = values.attribute(attributes, EXCEPTION_STACKTRACE_ATTRIBUTE)
        matches = (
            values.attribute(attributes, EXCEPTION_TYPE_ATTRIBUTE) == exception_type
            and values.attribute(attributes, EXCEPTION_MESSAGE_ATTRIBUTE) == exception_message
        )
        if matches:
            return stacktrace
        first_stacktrace = first_stacktrace or stacktrace
    return first_stacktrace


async def group_trends(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    service_name: str | None,
    from_time: datetime,
    to_time: datetime,
) -> GroupTrendResponse:
    """Each group's history across the window, for the sparkline in its row.

    Same source, same window and the same deduplication as the list beside it, so a
    row's number and its line cannot disagree about what they are counting.
    """
    bucket_seconds = rollup.trend_bucket_seconds(from_time, to_time)
    parameters = _window_parameters(project_id, from_time, to_time) | {
        "bucket_seconds": bucket_seconds,
        "limit": MAX_TREND_ROWS,
    }
    if service_name is not None:
        parameters["service_name"] = service_name

    rows = await repository.query(
        queries.group_trends_sql(one_service=service_name is not None), parameters
    )

    by_group: dict[str, list[GroupTrendPoint]] = defaultdict(list)
    for row in rows:
        by_group[values.text(row["group_id"])].append(
            GroupTrendPoint(
                bucket=values.nanos_to_datetime(row["bucket_nanos"]),
                occurrences=int(row["occurrences"]),
            )
        )

    return GroupTrendResponse(
        window_start=from_time,
        window_end=to_time,
        bucket_seconds=bucket_seconds,
        groups=[
            GroupTrend(group_id=group_id, points=points)
            for group_id, points in sorted(by_group.items())
        ],
    )
