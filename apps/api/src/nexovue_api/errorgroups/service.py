"""Turning error rows into the shapes the API promises.

No SQL text and no HTTP. What lives here is the part ClickHouse should not be
asked to do: widening the window onto our receive clock so partitions prune,
and digging the stacktrace out of the JSON it was left in.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_api import rollup
from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.deploys.service import EMPTY_DEPLOY_HISTORY, DeployHistory
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
    PrecedingDeploy,
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
    new_only: bool = False,
    deploys: DeployHistory = EMPTY_DEPLOY_HISTORY,
) -> ErrorGroupListResponse:
    """The window's error groups, the most frequent first, for one service or all.

    The name narrows the statement rather than the result. Filtering a capped
    project-wide page in Python would silently under-report a quiet service's
    exceptions — the groups that fell off the end of the page would simply be missing,
    and nothing on the screen could say so.

    An unknown service name is an empty list rather than a 404, for the same reason
    the service reads give: a service can be silent in a window without ceasing to
    exist.

    `deploys` is the project's deploys around this window, already read from the
    control plane. An empty history is a legitimate answer — most projects send
    no deploy notifications at all — and every group then carries none.
    """
    parameters = _window_parameters(project_id, from_time, to_time) | {
        "limit": limit,
        "new_after_nanos": _new_after_nanos(from_time, to_time),
    }
    if service_name is not None:
        parameters["service_name"] = service_name

    rows = await repository.query(
        queries.error_groups_sql(
            one_group=False, one_service=service_name is not None, new_only=new_only
        ),
        parameters,
    )
    return ErrorGroupListResponse(
        groups=[_to_summary(row, deploys) for row in rows],
        # A full page is the cap biting: there may be more this answer does not hold.
        truncated=len(rows) >= limit,
    )


async def get_error_group(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    group_id: str,
    from_time: datetime,
    to_time: datetime,
    deploys: DeployHistory = EMPTY_DEPLOY_HISTORY,
) -> ErrorGroupDetailResponse:
    """One group and the instances behind it, or a 404.

    A group id is derived from the rows in a window, so a group that existed
    yesterday is genuinely absent from today's — "not in this window" is the
    honest answer rather than "never happened".
    """
    parameters = _window_parameters(project_id, from_time, to_time)
    summary_row = await repository.query_one(
        queries.error_groups_sql(one_group=True, one_service=False),
        parameters
        | {
            "group_id": group_id,
            "limit": 1,
            "new_after_nanos": _new_after_nanos(from_time, to_time),
        },
    )
    if summary_row is None:
        raise NotFoundError(GROUP_NOT_FOUND_DETAIL)

    occurrence_rows = await repository.query(
        queries.GROUP_OCCURRENCES_SQL,
        parameters | {"group_id": group_id, "limit": OCCURRENCE_SAMPLE_SIZE},
    )
    return ErrorGroupDetailResponse(
        group=_to_summary(summary_row, deploys),
        recent=[_to_occurrence(row) for row in occurrence_rows],
    )


def _new_after_nanos(from_time: datetime, to_time: datetime) -> int:
    """The instant after which a first occurrence counts as starting here.

    A margin into the window rather than its edge, because `first_seen` is the
    minimum over the rows the window holds: a bug that has run for a week reports
    the window's own start and would otherwise look like a new incident. The
    margin is a fraction of the window, so it scales with what is being asked.
    """
    margin = (to_time - from_time).total_seconds() * queries.WINDOW_EDGE_FRACTION
    return int((from_time.timestamp() + margin) * queries.NANOS_PER_SECOND)


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


def _to_summary(row: dict[str, Any], deploys: DeployHistory) -> ErrorGroupSummary:
    first_seen = values.nanos_to_datetime(row["first_seen_nanos"])
    services = [values.text(service) for service in row["services"]]

    # `first_seen` is the earliest occurrence *inside the requested window*, so a
    # group that has run for a week reports the window's own start and gets the
    # deploy before that. That is the honest answer to what this asks — the last
    # change before the earliest instance anybody can see — rather than a claim
    # about a first occurrence nobody read.
    return ErrorGroupSummary(
        group_id=values.text(row["group_id"]),
        exception_type=values.text(row["exception_type"]),
        message_template=values.text(row["message_template"]),
        latest_message=values.text(row["latest_message"]),
        grouped_by=GROUPED_BY_SDK if int(row["grouped_by_sdk"]) else GROUPED_BY_TEMPLATE,
        occurrences=int(row["occurrences"]),
        first_seen=first_seen,
        started_in_window=bool(row["started_in_window"]),
        last_seen=values.nanos_to_datetime(row["last_seen_nanos"]),
        services=services,
        sample_trace_id=values.present_trace_id(row["sample_trace_id"]),
        sample_span_id=values.present_span_id(row["sample_span_id"]),
        sample_source=values.text(row["sample_source"]),
        preceding_deploy=_preceding_deploy(deploys, services=services, at=first_seen),
    )


def _preceding_deploy(
    deploys: DeployHistory, *, services: list[str], at: datetime
) -> PrecedingDeploy | None:
    """The deploy shape the wire carries, from the one the control plane holds.

    Its own three fields rather than the deploy list's whole row: what a reader
    needs beside an exception is which build, of which service, and when — and a
    delivery id and a metadata blob beside an error group are noise.
    """
    deploy = deploys.preceding(services=services, at=at)
    if deploy is None:
        return None
    return PrecedingDeploy(
        version=deploy.version,
        service_name=deploy.service_name,
        deployed_at=deploy.deployed_at,
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

    counted: dict[str, dict[datetime, int]] = defaultdict(dict)
    for row in rows:
        counted[values.text(row["group_id"])][values.nanos_to_datetime(row["bucket_nanos"])] = int(
            row["occurrences"]
        )

    buckets = _buckets_in(from_time, to_time, bucket_seconds)
    return GroupTrendResponse(
        window_start=from_time,
        window_end=to_time,
        bucket_seconds=bucket_seconds,
        groups=[
            GroupTrend(
                group_id=group_id,
                points=[
                    GroupTrendPoint(bucket=bucket, occurrences=found.get(bucket, 0))
                    for bucket in buckets
                ],
            )
            for group_id, found in sorted(counted.items())
        ],
    )


def _buckets_in(from_time: datetime, to_time: datetime, bucket_seconds: int) -> list[datetime]:
    """Every bucket in the window, including the ones nothing happened in.

    The statement returns only buckets that hold an occurrence, which for an error group
    is most of them missing: four exceptions in an hour is four rows, and a sparkline
    drawn from four points is not a shape — it is four points.

    **A quiet bucket is a real reading here, not an absent one.** Zero errors happened,
    and that is the shape: four in the first minutes and none since is a group that
    stopped, which is the opposite claim to four spread evenly. Contrast a duration,
    where a missing bucket means nothing was measured and the line must break instead.
    """
    step = timedelta(seconds=bucket_seconds)
    start_epoch = int(from_time.timestamp()) // bucket_seconds * bucket_seconds
    bucket = datetime.fromtimestamp(start_epoch, tz=UTC)
    if bucket < from_time:
        bucket += step

    buckets: list[datetime] = []
    while bucket < to_time:
        buckets.append(bucket)
        bucket += step
    return buckets
