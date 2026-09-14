"""Searching stored logs: free text, filters, and a window.

`router.py` answers "which logs belong to this trace". This answers the other
question a log screen asks — "which logs match what I typed" — and the two share
nothing but the table and the record shape.

**Why a separate module rather than four edits.** The package convention is
router / service / queries / schemas, and this holds all four layers for one
endpoint pair. P1-43 was scoped to new files in this package, so folding these
back into the existing four is a follow-up, not a decision.

**What bounds the read.** `logs` is partitioned on `received_at`, our own clock,
while the caller asks about `timestamp`, the customer's. Both
predicates are present and neither is redundant: `received_at` prunes partitions
and `timestamp` answers the question that was asked.

**Why substring rather than the text index.** `idx_body` is a token index, so
`hasToken(lower(body), 'author')` finds nothing while `hasToken(..., 'authorized')`
finds 32,809 rows — measured against the dev store. A log search box where typing
half a word finds nothing is a broken log search box, so the predicate is
`positionCaseInsensitive` and the window is what keeps the scan bounded. The
index earns its place the day this grows a token-mode search; it is not used now.
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from nexovue_api.auth.dependencies import CurrentIdentity, TenantRepository
from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.deps import DbSession
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.logs.schemas import LogRecord
from nexovue_api.projects.service import require_project
from nexovue_api.window import clamp_limit, receive_window, resolve_window
from nexovue_api.wire import to_iso_millis

DEFAULT_LIMIT: Final = 200
MAX_LIMIT: Final = 1_000

# One facet list is a set of filter options, not a report. Past a few hundred
# services the control is unusable anyway, and the cap keeps the group-by cheap.
MAX_FACET_ROWS: Final = 500

# The OTLP severity floor a caller may ask for. 0 means "everything"; 24 is FATAL,
# the highest number the specification defines.
MIN_SEVERITY_FLOOR: Final = 0
MIN_SEVERITY_CEILING: Final = 24

MALFORMED_CURSOR_DETAIL: Final = "That cursor is not one this endpoint issued."

# ------------------------------------------------------------------------ SQL

# ClickHouse's own parameter syntax is braces, so an optional predicate is
# spliced on a comment marker rather than with str.format.
_SERVICE_MARKER: Final = "/* service */"
_SEVERITY_MARKER: Final = "/* severity */"
_SEARCH_MARKER: Final = "/* search */"
_CURSOR_MARKER: Final = "/* cursor */"

SERVICE_CLAUSE: Final = "AND service_name = {service_name:String}"
SEVERITY_CLAUSE: Final = "AND severity_number >= {min_severity:Int32}"
SEARCH_CLAUSE: Final = "AND positionCaseInsensitive(body, {search:String}) > 0"

# Keyset, not offset. `timestamp` alone is not unique — a batch of records can
# share one nanosecond — so the page boundary carries a hash of the row's own
# columns as the tiebreaker. Two byte-identical records at the same instant do
# collide; they are the same line twice, and one of them is dropped.
CURSOR_CLAUSE: Final = "AND (observed_nanos, row_key) < ({cursor_nanos:Int64}, {cursor_key:UInt64})"

_ROW_KEY: Final = "cityHash64(timestamp, service_name, severity_number, body, trace_id, span_id)"

_SEARCH_TEMPLATE: Final = f"""
SELECT
    toUnixTimestamp64Nano(timestamp) AS observed_nanos,
    {_ROW_KEY} AS row_key,
    service_name,
    severity,
    severity_number,
    body,
    replaceAll(trace_id, '\\0', '') AS trace_ref,
    replaceAll(span_id, '\\0', '') AS span_ref,
    toJSONString(log_attrs) AS attributes_json
FROM logs
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND received_at >= {{received_from:DateTime64(9)}}
  AND received_at < {{received_to:DateTime64(9)}}
  AND timestamp >= {{from_time:DateTime64(9)}}
  AND timestamp < {{to_time:DateTime64(9)}}
  {_SERVICE_MARKER}
  {_SEVERITY_MARKER}
  {_SEARCH_MARKER}
  {_CURSOR_MARKER}
ORDER BY observed_nanos DESC, row_key DESC
LIMIT {{limit:UInt32}}
"""

# The filter options, counted over the same window the results come from — a
# service that sent nothing in this hour should not be offered as a filter.
FACETS_SQL: Final = """
SELECT
    service_name,
    severity,
    severity_number,
    count() AS records
FROM logs
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND received_at >= {received_from:DateTime64(9)}
  AND received_at < {received_to:DateTime64(9)}
  AND timestamp >= {from_time:DateTime64(9)}
  AND timestamp < {to_time:DateTime64(9)}
GROUP BY service_name, severity, severity_number
ORDER BY records DESC
LIMIT {limit:UInt32}
"""


@dataclass(frozen=True, slots=True)
class LogFilters:
    """What narrows a search beyond the window. Every field is optional."""

    service_name: str | None = None
    min_severity: int | None = None
    search: str | None = None


def search_sql(filters: LogFilters, *, with_cursor: bool) -> str:
    """The search statement carrying only the predicates this call needs."""
    return (
        _SEARCH_TEMPLATE.replace(_SERVICE_MARKER, SERVICE_CLAUSE if filters.service_name else "")
        .replace(_SEVERITY_MARKER, SEVERITY_CLAUSE if filters.min_severity else "")
        .replace(_SEARCH_MARKER, SEARCH_CLAUSE if filters.search else "")
        .replace(_CURSOR_MARKER, CURSOR_CLAUSE if with_cursor else "")
    )


# --------------------------------------------------------------------- cursor

_CURSOR_SEPARATOR: Final = ":"
_CURSOR_ENCODING: Final = "ascii"


@dataclass(frozen=True, slots=True)
class LogPageCursor:
    """Where the next page starts: the last record of the previous one."""

    observed_nanos: int
    row_key: int

    def encode(self) -> str:
        raw = f"{self.observed_nanos}{_CURSOR_SEPARATOR}{self.row_key}".encode(_CURSOR_ENCODING)
        return base64.urlsafe_b64encode(raw).decode(_CURSOR_ENCODING).rstrip("=")


def decode_cursor(value: str) -> LogPageCursor:
    """Read a cursor, or reject it. A malformed one is a client bug, not a page."""
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode(_CURSOR_ENCODING)).decode(_CURSOR_ENCODING)
        nanos, separator, row_key = raw.partition(_CURSOR_SEPARATOR)
        if not separator:
            raise ValueError(raw)
        return LogPageCursor(observed_nanos=int(nanos), row_key=int(row_key))
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        raise InvalidRequestError(MALFORMED_CURSOR_DETAIL) from error


# ------------------------------------------------------------------- the wire


class LogLine(LogRecord):
    """A log record plus the key that identifies it wherever it appears.

    `row_key` is a hash of the record's own columns — `logs` has no primary key.
    It is a string on the wire because it is a UInt64 and JavaScript cannot hold
    one exactly. Search and the live tail both carry it, which is what lets a
    screen recognise a line the tail sends that the search already showed.
    """

    row_key: str


class LogSearchResponse(BaseModel):
    """One page of matching records, newest first."""

    logs: list[LogLine] = Field(default_factory=list)
    # Absent when this page is the last one.
    next_cursor: str | None = None


class ServiceFacet(BaseModel):
    service_name: str
    records: int


class SeverityFacet(BaseModel):
    severity: str
    severity_number: int
    records: int


class LogFacetsResponse(BaseModel):
    """The filter options this window actually contains, commonest first."""

    services: list[ServiceFacet] = Field(default_factory=list)
    severities: list[SeverityFacet] = Field(default_factory=list)


# ----------------------------------------------------------------- the service


def to_line(row: dict[str, Any]) -> LogLine:
    """One stored row as the record the contract names."""
    return LogLine(
        row_key=str(row["row_key"]),
        observed_at=values.nanos_to_datetime(row["observed_nanos"]),
        service_name=values.text(row["service_name"]),
        severity=values.text(row["severity"]),
        severity_number=int(row["severity_number"]),
        body=values.text(row["body"]),
        trace_id=values.present_trace_id(row["trace_ref"]),
        span_id=values.present_span_id(row["span_ref"]),
        attributes=values.json_object(row["attributes_json"]),
    )


def _filter_parameters(filters: LogFilters) -> dict[str, Any]:
    """Only the values the spliced predicates name, so an unused one is unbound."""
    bound: dict[str, Any] = {}
    if filters.service_name:
        bound["service_name"] = filters.service_name
    if filters.min_severity:
        bound["min_severity"] = filters.min_severity
    if filters.search:
        bound["search"] = filters.search
    return bound


async def search_logs(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    filters: LogFilters,
    limit: int,
    cursor: str | None,
) -> LogSearchResponse:
    """One page of matching records, newest first."""
    page = decode_cursor(cursor) if cursor else None
    received_from, received_to = receive_window(from_time, to_time)

    parameters: dict[str, Any] = {
        "project_id": project_id,
        "received_from": received_from,
        "received_to": received_to,
        "from_time": from_time,
        "to_time": to_time,
        "limit": limit,
        **_filter_parameters(filters),
    }
    if page is not None:
        parameters["cursor_nanos"] = page.observed_nanos
        parameters["cursor_key"] = page.row_key

    rows = await repository.query(search_sql(filters, with_cursor=page is not None), parameters)
    return LogSearchResponse(
        logs=[to_line(row) for row in rows], next_cursor=_next_cursor(rows, limit)
    )


# **Deliberately more than one screen holds and far less than a window can**, for
# the reason `traces/service.py` gives at the same cap: an export exists to be taken
# away and worked on, so a page of two hundred is a worse answer than the screen
# already gives, and a million rows is a request that runs until something times out
# and leaves nobody with a file. Past this the filters are the tool, not the download.
MAX_EXPORTED_LOGS: Final = 50_000

LOG_COLUMNS: Final = (
    "observed_at",
    "severity",
    "severity_number",
    "service_name",
    "body",
    "trace_id",
    "span_id",
    "attributes",
)


async def logs_as_csv(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    filters: LogFilters,
) -> tuple[str, bool]:
    """The filtered records as a file, and whether it was cut short.

    Paged through with the search's own cursor rather than by raising its limit:
    the cap exists because a single unbounded read is what `performance.md`
    refuses, and an export is not an exception to that — it is the same read,
    more times.

    Returns the truncation flag rather than hiding it. A file that silently stops
    is worse than one that says where it stopped, and the caller writes that into
    the response for the screen to repeat.
    """
    sheet = io.StringIO()
    writer = csv.writer(sheet)
    writer.writerow(LOG_COLUMNS)

    written = 0
    cursor: str | None = None
    while written < MAX_EXPORTED_LOGS:
        page = await search_logs(
            repository,
            project_id=project_id,
            from_time=from_time,
            to_time=to_time,
            filters=filters,
            limit=min(MAX_LIMIT, MAX_EXPORTED_LOGS - written),
            cursor=cursor,
        )
        for line in page.logs:
            writer.writerow(
                [
                    # **Written through the wire's own serializer.** `IsoTimestamp`
                    # carries one, but pydantic only applies it when serializing, and
                    # `csv.writer` takes the raw datetime — so the file would have
                    # carried `2026-08-30 07:51:58.688120+00:00` where every API
                    # answer carries `2026-08-30T07:51:58.688Z`. One product, one
                    # timestamp.
                    to_iso_millis(line.observed_at),
                    line.severity,
                    line.severity_number,
                    line.service_name,
                    line.body,
                    line.trace_id,
                    line.span_id,
                    # One column rather than a column per key: log attributes are
                    # per-record, so a spread would be as wide as the union of every
                    # record in the file and empty almost everywhere.
                    json.dumps(line.attributes, sort_keys=True) if line.attributes else "",
                ]
            )
        written += len(page.logs)
        if page.next_cursor is None:
            return sheet.getvalue(), False
        cursor = page.next_cursor

    return sheet.getvalue(), True


def _next_cursor(rows: list[dict[str, Any]], limit: int) -> str | None:
    """A cursor only when a full page came back — a short page is the last one."""
    if len(rows) < limit:
        return None
    last = rows[-1]
    return LogPageCursor(
        observed_nanos=int(last["observed_nanos"]), row_key=int(last["row_key"])
    ).encode()


async def log_facets(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
) -> LogFacetsResponse:
    """Which services and severities this window holds, so the filters offer only those."""
    received_from, received_to = receive_window(from_time, to_time)
    rows = await repository.query(
        FACETS_SQL,
        {
            "project_id": project_id,
            "received_from": received_from,
            "received_to": received_to,
            "from_time": from_time,
            "to_time": to_time,
            "limit": MAX_FACET_ROWS,
        },
    )
    return LogFacetsResponse(services=_service_facets(rows), severities=_severity_facets(rows))


def _service_facets(rows: list[dict[str, Any]]) -> list[ServiceFacet]:
    counts: dict[str, int] = {}
    for row in rows:
        name = values.text(row["service_name"])
        counts[name] = counts.get(name, 0) + int(row["records"])
    ranked = sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))
    return [ServiceFacet(service_name=name, records=records) for name, records in ranked]


def _severity_facets(rows: list[dict[str, Any]]) -> list[SeverityFacet]:
    counts: dict[tuple[str, int], int] = {}
    for row in rows:
        key = (values.text(row["severity"]), int(row["severity_number"]))
        counts[key] = counts.get(key, 0) + int(row["records"])
    # Loudest first: a severity filter is chosen by rank, not by frequency.
    ranked = sorted(counts.items(), key=lambda entry: -entry[0][1])
    return [
        SeverityFacet(severity=severity, severity_number=number, records=records)
        for (severity, number), records in ranked
    ]


# ------------------------------------------------------------------ the routes

router = APIRouter()

ProjectId = Annotated[uuid.UUID, Query(description="Project to read logs from")]
FromTime = Annotated[datetime | None, Query(alias="from")]
ToTime = Annotated[datetime | None, Query(alias="to")]


@router.get("/search", summary="Log records matching a search, newest first")
async def search(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
    q: Annotated[str | None, Query(description="Case-insensitive substring of the body")] = None,
    service: Annotated[str | None, Query(description="Exactly one service name")] = None,
    min_severity: Annotated[
        int | None,
        Query(
            ge=MIN_SEVERITY_FLOOR,
            le=MIN_SEVERITY_CEILING,
            description="OTLP severity number floor: 9 INFO, 13 WARN, 17 ERROR",
        ),
    ] = None,
    limit: Annotated[int | None, Query(ge=1, le=MAX_LIMIT)] = None,
    cursor: Annotated[str | None, Query(description="Opaque marker from a previous page")] = None,
) -> LogSearchResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await search_logs(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
        filters=LogFilters(service_name=service, min_severity=min_severity, search=q),
        limit=clamp_limit(limit, default=DEFAULT_LIMIT, maximum=MAX_LIMIT),
        cursor=cursor,
    )


# The same records as a file. A suffix rather than a `format` parameter, matching
# `traces/router.py` and `billing/router.py` — the convention `docs/engineering/api.md`
# records — and a route rather than something the browser assembles, because a link to
# a response carrying `Content-Disposition: attachment` is downloaded by the browser
# itself.
LOGS_CSV_PATH: Final = "/export.csv"
CSV_CONTENT_TYPE: Final = "text/csv; charset=utf-8"


@router.get(LOGS_CSV_PATH, response_class=Response, summary="The same records as a file")
async def download(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
    q: Annotated[str | None, Query(description="Case-insensitive substring of the body")] = None,
    service: Annotated[str | None, Query(description="Exactly one service name")] = None,
    min_severity: Annotated[
        int | None, Query(ge=MIN_SEVERITY_FLOOR, le=MIN_SEVERITY_CEILING)
    ] = None,
) -> Response:
    """The filtered records as CSV, with the same filters the screen is showing.

    **Every filter is taken again rather than a "download what you last asked
    for" token**, for the reason the traces export gives: the link carries the
    question, so it is the same question a reader can paste to a colleague, and
    there is no server-side memory of a search to go stale between looking and
    downloading.

    **This reads storage, which runs behind the tail.** Records reach ClickHouse
    through the pipeline, so the newest lines on the screen may not be in this
    file yet. The screen offers its own buffer as the other half of the answer.
    """
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    sheet, truncated = await logs_as_csv(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
        filters=LogFilters(service_name=service, min_severity=min_severity, search=q),
    )

    return Response(
        content=sheet,
        media_type=CSV_CONTENT_TYPE,
        headers={
            "content-disposition": f'attachment; filename="{project.slug}-logs.csv"',
            # A file that silently stops is worse than one that says so. The screen
            # reads this to tell a reader their filters, not the export, are the tool.
            "x-nexovue-truncated": "true" if truncated else "false",
        },
    )


@router.get("/facets", summary="The services and severities this window holds")
async def facets(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> LogFacetsResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await log_facets(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
    )
