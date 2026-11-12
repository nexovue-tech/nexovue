"""Releases: the list, one release, and its comparison (F28 plan T1.14).

The list and the release read Postgres alone, so they answer while ClickHouse is down
and cost no telemetry read per row (AC81). The comparison reads the version rollup for
one service, up to the settled minute and never past it (AC35), and hands the minutes
to `nexovue_platform.releases.choose_method`, the one definition the scheduler shares.

Every release route loads the release by organization and id, then proves its project
readable, which refuses a token outside its project and a deleted project with the same
404 a stranger gets (design, *Routes*; AC26).
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform import releases as method
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.dependencies import Identity, Reader
from nexovue_api.data.clickhouse.base import TenantScopedRepository, server_error_code
from nexovue_api.data.postgres.models import Project, Release, ReleasePeriod
from nexovue_api.detection import queries as detection_queries
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
from nexovue_api.findings.queries import derived_retention_days
from nexovue_api.projects.config import KEEP_ERRORS
from nexovue_api.projects.service import require_readable_project
from nexovue_api.releases import queries
from nexovue_api.releases.schemas import (
    Activity,
    Basis,
    BlockView,
    ComparisonView,
    CoverageList,
    DeployFactView,
    ErrorKind,
    FindingWindowView,
    LaterFinding,
    LatestRelease,
    Marker,
    MarkerList,
    NamingDiffers,
    PeriodView,
    ReadingView,
    ReleaseList,
    ReleaseRow,
    ReleaseView,
    Rollout,
    RolloutMinute,
    RolloutSeries,
    SenderDeploy,
    SenderDetails,
    ServiceCoverage,
    SideView,
    StoredFlag,
    TrafficView,
    VersionErrorGroup,
    VersionErrors,
)
from nexovue_api.window import RECEIVE_CLOCK_SLACK

NO_SUCH_RELEASE: Final = "No such release"
NO_SUCH_PERIOD: Final = "This release has no such period."
NO_SUCH_FINDING: Final = "No such finding in this release's project."
BAD_CURSOR: Final = "This page link is not one we gave out."
DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200
# A release counted this recently is serving (spec, *A release's activity*; a guess).
SERVING_WITHIN: Final = timedelta(minutes=10)
WINDOW_HOURS: Final = tuple(int(window / timedelta(hours=1)) for window in method.WINDOWS)
_CURSOR_SEPARATOR: Final = "|"
# FR46 reads releases first heard of in this long (a guess).
NAMING_LOOKBACK: Final = timedelta(hours=24)
# Findings listed under one period (FR34); past it the page says there are more.
LATER_FINDINGS_CAP: Final = 50
_MINUTE: Final = timedelta(minutes=1)


# ------------------------------------------------------------------ the list


async def list_releases(
    session: AsyncSession,
    *,
    reader: Reader,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    service: str | None,
    version: str | None,
    cursor: str | None,
    limit: int,
    now: datetime,
) -> ReleaseList:
    project = await require_readable_project(session, reader=reader, project_id=project_id)
    keys: dict[str, Any] = {
        "org_id": reader.org_id,
        "project_id": project.id,
        "start": start,
        "end": end,
        "service": service,
        "version": version,
    }
    rows = await queries.releases_in_window(
        session, **keys, after=_read_cursor(cursor), limit=limit + 1
    )
    total, services = await queries.window_totals(session, **keys)
    page = rows[:limit]
    latest = None
    if total == 0:
        found = await queries.latest_release(
            session,
            org_id=reader.org_id,
            project_id=project.id,
            service=service,
            version=version,
        )
        latest = (
            None
            if found is None
            else LatestRelease(
                id=found[0].id,
                service_name=found[0].service_name,
                version=found[0].version,
                started_at=found[1],
            )
        )
    differs = await queries.naming_differs(
        session, org_id=reader.org_id, project_id=project.id, since=now - NAMING_LOOKBACK
    )
    return ReleaseList(
        releases=[_row(row, now) for row in page],
        total=total,
        services=services,
        next_cursor=_cursor_of(page[-1]) if len(rows) > limit else None,
        has_any=total > 0
        or latest is not None
        or await queries.has_releases(session, org_id=reader.org_id, project_id=project.id),
        latest=latest,
        window_from=start,
        window_to=end,
        naming_differs=[
            NamingDiffers(
                service_name=row.service_name,
                reported_version=row.reported_version,
                seen_version=row.seen_version,
            )
            for row in differs
        ],
    )


def _row(row: Any, now: datetime) -> ReleaseRow:
    return ReleaseRow(
        id=row.id,
        period_id=row.period_id,
        service_name=row.service_name,
        version=row.version,
        started_at=row.started_at,
        ended_at=row.ended_at,
        start_basis=row.start_basis,
        basis=basis_of(row.first_seen_at, row.first_reported_at),
        activity=activity_of(
            first_seen_at=row.first_seen_at,
            last_seen_at=row.last_seen_at,
            latest_period_ended=row.latest_ended_at is not None,
            now=now,
        ),
        already_running=row.already_running,
        is_return=row.is_return,
        last_seen_at=row.last_seen_at,
        last_state=row.last_state,
        flags=stored_flags(row.last_reading),
        versions_combined=bool((row.last_reading or {}).get("versions_combined")),
    )


def stored_flags(reading: Mapping[str, Any] | None) -> list[StoredFlag]:
    """FR55, AC81: a flag for each measure the stored reading has `higher` by version,
    with its counts; none for any other reading, or none stored."""
    if reading is None:
        return []
    this, previous = reading.get("this") or {}, reading.get("previous") or {}
    return [
        StoredFlag(
            measure=measure,
            this_requests=int(this.get("requests") or 0),
            this_errors=int(this.get("errors") or 0),
            this_p95_ms=this.get("p95_ms"),
            previous_requests=int(previous.get("requests") or 0),
            previous_errors=int(previous.get("errors") or 0),
            previous_p95_ms=previous.get("p95_ms"),
        )
        for measure in method.higher_measures(reading)
    ]


def basis_of(first_seen_at: datetime | None, first_reported_at: datetime | None) -> Basis:
    if first_seen_at is not None and first_reported_at is not None:
        return "both"
    return "seen" if first_seen_at is not None else "reported"


def activity_of(
    *,
    first_seen_at: datetime | None,
    last_seen_at: datetime | None,
    latest_period_ended: bool,
    now: datetime,
) -> Activity:
    """Spec, *A release's activity*: never counted, replaced, counted lately, or idle."""
    if first_seen_at is None:
        return "not_seen"
    if latest_period_ended:
        return "replaced"
    if last_seen_at is not None and now - last_seen_at <= SERVING_WITHIN:
        return "serving"
    return "idle"


def _cursor_of(row: Any) -> str:
    raw = f"{row.started_at.isoformat()}{_CURSOR_SEPARATOR}{row.period_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _read_cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if cursor is None:
        return None
    try:
        started, period_id = base64.urlsafe_b64decode(cursor).decode().split(_CURSOR_SEPARATOR)
        return datetime.fromisoformat(started), uuid.UUID(period_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as refused:
        raise InvalidRequestError(BAD_CURSOR) from refused


# --------------------------------------------------------------- one release


async def _readable_release(
    session: AsyncSession, *, reader: Reader, release_id: uuid.UUID
) -> Release:
    release = await queries.find_release(session, org_id=reader.org_id, release_id=release_id)
    if release is None:
        raise NotFoundError(NO_SUCH_RELEASE)
    try:
        await require_readable_project(session, reader=reader, project_id=release.project_id)
    except NotFoundError as hidden:
        # Another project's, or a deleted one's, reads as no such release.
        raise NotFoundError(NO_SUCH_RELEASE) from hidden
    return release


async def read_release(
    session: AsyncSession, *, reader: Reader, release_id: uuid.UUID, now: datetime
) -> ReleaseView:
    release = await _readable_release(session, reader=reader, release_id=release_id)
    periods = await queries.periods_of(session, org_id=reader.org_id, release_id=release.id)
    latest = periods[-1] if periods else None
    ended = latest is not None and latest.ended_at is not None
    replaced = (
        await queries.replaced_by(
            session, org_id=reader.org_id, release=release, ended_at=latest.ended_at
        )
        if latest is not None and latest.ended_at is not None
        else None
    )
    return ReleaseView(
        id=release.id,
        project_id=release.project_id,
        service_name=release.service_name,
        version=release.version,
        basis=basis_of(release.first_seen_at, release.first_reported_at),
        activity=activity_of(
            first_seen_at=release.first_seen_at,
            last_seen_at=release.last_seen_at,
            latest_period_ended=ended,
            now=now,
        ),
        replaced_by=replaced,
        first_seen_at=release.first_seen_at,
        last_seen_at=release.last_seen_at,
        first_reported_at=release.first_reported_at,
        last_reported_at=release.last_reported_at,
        report_count=release.report_count,
        last_state=release.last_state,
        periods=[
            await _period(session, release=release, period=period) for period in reversed(periods)
        ],
        deploys=[
            DeployFactView(
                id=row.id,
                deployed_at=row.deployed_at,
                received_at=row.created_at,
                state=row.state,
                commit_sha=row.commit_sha,
            )
            for row in await queries.deploys_of(
                session, org_id=reader.org_id, release_id=release.id
            )
        ],
    )


async def _period(session: AsyncSession, *, release: Release, period: ReleasePeriod) -> PeriodView:
    """A period, with the findings on its service that started while it served (FR34,
    AC41): from its start's own minute, so a finding in that minute is after it, and
    never one that started before."""
    found = await queries.findings_from(
        session,
        org_id=release.org_id,
        project_id=release.project_id,
        service_name=release.service_name,
        start=_minute_of(period.started_at),
        end=period.ended_at,
        limit=LATER_FINDINGS_CAP + 1,
    )
    return PeriodView(
        id=period.id,
        started_at=period.started_at,
        ended_at=period.ended_at,
        start_basis=period.start_basis,
        previous_version=period.previous_version,
        already_running=period.already_running,
        is_return=period.is_return,
        later_findings=[
            LaterFinding(
                id=row.id,
                title=row.title,
                severity=row.severity,
                state=row.state,
                started_at=row.started_at,
            )
            for row in found[:LATER_FINDINGS_CAP]
        ],
        later_findings_truncated=len(found) > LATER_FINDINGS_CAP,
    )


def _minute_of(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


# ---------------------------------------------------------------- comparison


@dataclass(frozen=True, slots=True)
class _Compared:
    """The period a comparison reads, and the stretch of rows it reads."""

    release: Release
    period: ReleasePeriod
    window: timedelta
    to: datetime
    as_of: bool
    kept_days: int


async def _readable_period(
    session: AsyncSession, *, reader: Reader, release_id: uuid.UUID, period_id: uuid.UUID | None
) -> tuple[Release, ReleasePeriod]:
    release = await _readable_release(session, reader=reader, release_id=release_id)
    periods = await queries.periods_of(session, org_id=reader.org_id, release_id=release.id)
    return release, _chosen(periods, period_id)


async def _compared(
    session: AsyncSession,
    *,
    release: Release,
    period: ReleasePeriod,
    window: timedelta,
    now: datetime,
) -> _Compared:
    """Where reads of this period stop: the window's end or the settled minute (AC35)."""
    sweep = await detection_queries.detection_sweep(session)
    to, as_of = method.comparison_end(
        period.started_at + window, sweep.settled_minute if sweep else None, now
    )
    kept_days = min(
        await derived_retention_days(session, org_id=release.org_id) or method.VERSION_ROLLUP_DAYS,
        method.VERSION_ROLLUP_DAYS,
    )
    return _Compared(release, period, window, to, as_of, kept_days)


async def compare(
    session: AsyncSession,
    repository: TenantScopedRepository,
    *,
    reader: Reader,
    release_id: uuid.UUID,
    period_id: uuid.UUID | None,
    window_hours: int | None,
    finding_id: uuid.UUID | None,
    now: datetime,
) -> ComparisonView:
    """One period against its previous version, by the design's ordered list, with what
    the numbers cannot hide: minutes missing from the version rows (FR28), versions the
    worker combined (FR17), two environments (FR45), and last week (FR25)."""
    release, period = await _readable_period(
        session, reader=reader, release_id=release_id, period_id=period_id
    )
    finding = (
        None
        if finding_id is None
        else await _finding_after(session, release=release, period=period, finding_id=finding_id)
    )
    window = _window_of(window_hours, period, finding)
    compared = await _compared(session, release=release, period=period, window=window, now=now)
    expired_before = now - timedelta(days=compared.kept_days)
    start = method.read_start(period.started_at, window)
    rows, totals = await _rows_and_totals(repository, compared, start)
    decided = method.choose_method(
        method.PeriodToCompare(
            started_at=period.started_at,
            version=release.version,
            previous_version=period.previous_version,
            already_running=period.already_running,
            previous_started_at=await _previous_started(session, release, period),
            reported_at=method.reported_moment(period.started_at, period.start_basis),
        ),
        rows,
        to=compared.to,
        window=window,
        expired_before=expired_before,
        totals=totals,
    )
    spans = method.compared_spans(decided)
    last_week = await _last_week(repository, release, decided, expired_before)
    differing = method.minutes_differing(rows, totals, spans)
    return ComparisonView(
        period_id=period.id,
        version=release.version,
        previous_version=period.previous_version,
        reason=decided.reason,
        lead=decided.lead,
        as_of=compared.to if compared.as_of else None,
        compared_to=compared.to,
        window_hours=_hours(window),
        blocks=[_block(block, method.last_week_of(block, last_week)) for block in decided.blocks],
        traffic=None
        if decided.traffic is None
        else TrafficView(
            this_version=_side(decided.traffic[0]), previous_version=_side(decided.traffic[1])
        ),
        other_versions=list(decided.other_versions),
        numbers_kept_days=compared.kept_days,
        incomplete=differing > 0,
        minutes_differing=differing,
        versions_combined=method.versions_combined(rows, spans),
        mixed_environments=await _mixed_environments(repository, release, spans),
        finding=None
        if finding is None
        else FindingWindowView(
            id=finding[0],
            started_at=finding[1],
            reaches=method.reaches(period.started_at, window, finding[1]),
        ),
    )


async def _finding_after(
    session: AsyncSession, *, release: Release, period: ReleasePeriod, finding_id: uuid.UUID
) -> tuple[uuid.UUID, datetime] | None:
    """The finding a comparison was opened from (FR22): this project's, or 404. One that
    started before the period says nothing of a window from it, so it is left out."""
    found = await queries.project_finding(
        session, org_id=release.org_id, project_id=release.project_id, finding_id=finding_id
    )
    if found is None:
        raise NotFoundError(NO_SUCH_FINDING)
    if found.started_at < _minute_of(period.started_at):
        return None
    return found.id, found.started_at


def _window_of(
    window_hours: int | None,
    period: ReleasePeriod,
    finding: tuple[uuid.UUID, datetime] | None,
) -> timedelta:
    """The window asked for; else, opened from a finding, the smallest that reaches it
    (FR22, AC64); else an hour."""
    if window_hours is not None:
        return timedelta(hours=window_hours)
    if finding is not None:
        return method.window_for_finding(period.started_at, finding[1]).window
    return method.DEFAULT_WINDOW


def _hours(window: timedelta) -> int:
    return int(window / timedelta(hours=1))


async def _rows_and_totals(
    repository: TenantScopedRepository, compared: _Compared, start: datetime
) -> tuple[list[method.VersionMinute], list[method.ServiceMinute]]:
    if compared.to <= start:
        return [], []
    keys: dict[str, Any] = {
        "project_id": compared.release.project_id,
        "service_name": compared.release.service_name,
        "start": start,
        "end": compared.to,
    }
    rows = [
        method.version_minute_of(row) for row in await queries.version_minutes(repository, **keys)
    ]
    totals = [
        method.service_minute_of(row) for row in await queries.service_minutes(repository, **keys)
    ]
    return rows, totals


async def _previous_started(
    session: AsyncSession, release: Release, period: ReleasePeriod
) -> datetime | None:
    if not period.previous_version:
        return None
    return await queries.previous_started_at(
        session,
        org_id=release.org_id,
        project_id=release.project_id,
        service_name=release.service_name,
        version=period.previous_version,
        before=period.started_at,
    )


async def _last_week(
    repository: TenantScopedRepository,
    release: Release,
    decided: method.Comparison,
    expired_before: datetime,
) -> list[method.ServiceMinute]:
    """The service's minutes a week before each before-and-after block, while kept."""
    stretches = [
        (block.start - method.LAST_WEEK, block.end - method.LAST_WEEK)
        for block in decided.blocks
        if block.method != method.SIDE_BY_SIDE
    ]
    if not stretches or min(start for start, _ in stretches) < expired_before:
        return []
    rows = await queries.service_minutes(
        repository,
        project_id=release.project_id,
        service_name=release.service_name,
        start=min(start for start, _ in stretches),
        end=max(end for _, end in stretches),
    )
    return [method.service_minute_of(row) for row in rows]


async def _mixed_environments(
    repository: TenantScopedRepository, release: Release, spans: Sequence[method.Span]
) -> bool:
    """FR45, read only where there are numbers to warn about."""
    if not spans:
        return False
    found = await queries.environments(
        repository,
        project_id=release.project_id,
        service_name=release.service_name,
        start=min(start for start, _ in spans),
        end=max(end for _, end in spans),
        clock_slack=RECEIVE_CLOCK_SLACK,
    )
    return len(found) > 1


def window_hours_of(requested: int | None) -> int | None:
    """FR22's windows; None when unset, for the comparison to choose."""
    if requested is None:
        return None
    if requested not in WINDOW_HOURS:
        raise InvalidRequestError(
            f"`window` is one of {', '.join(str(hours) for hours in WINDOW_HOURS)} hours."
        )
    return requested


def _chosen(periods: list[ReleasePeriod], period_id: uuid.UUID | None) -> ReleasePeriod:
    if not periods:
        raise NotFoundError(NO_SUCH_PERIOD)
    if period_id is None:
        return periods[-1]
    for period in periods:
        if period.id == period_id:
            return period
    raise NotFoundError(NO_SUCH_PERIOD)


def _side(side: method.Side) -> SideView:
    return SideView(
        version=side.version, requests=side.requests, errors=side.errors, p95_ms=side.p95_ms
    )


def _reading(reading: method.Reading) -> ReadingView:
    return ReadingView(reading=reading.reading, note=reading.note)


def _block(block: method.Block, last_week: method.Side | None) -> BlockView:
    return BlockView(
        kind=block.kind,
        method=block.method,
        start=block.start,
        end=block.end,
        previous_from=block.previous_start,
        previous_to=block.previous_end,
        minutes_compared=block.minutes_compared,
        this_version=_side(block.this_version),
        previous_version=_side(block.previous_version),
        failure_share=_reading(block.failure_share),
        p95=_reading(block.p95),
        last_week=None if last_week is None else _side(last_week),
    )


# ------------------------------------------------------- errors by version, FR29

# At most this many groups (FR29); the read asks for one more to know there are more.
ERROR_GROUPS_CAP: Final = 100
# ClickHouse's TIMEOUT_EXCEEDED, as `dashboardpanels/shared_reads.py` reads it: a read
# past the account's time limit (`users.d/api-reader.xml`).
_TIMEOUT_EXCEEDED: Final = 159


async def errors_by_version(
    session: AsyncSession,
    repository: TenantScopedRepository,
    *,
    reader: Reader,
    release_id: uuid.UUID,
    period_id: uuid.UUID | None,
    window_hours: int | None,
    now: datetime,
) -> VersionErrors:
    """Error groups new in this version, gone in it, or in both (FR29): this version
    from the period's start over the window, the previous version over the equal
    stretch before. A timeout, aged-out rows and sampling that keeps errors off each
    say so in words, never as an empty list (FR30, FR31, FR51)."""
    release, period = await _readable_period(
        session, reader=reader, release_id=release_id, period_id=period_id
    )
    project = await require_readable_project(session, reader=reader, project_id=release.project_id)
    window = timedelta(hours=window_hours) if window_hours else method.DEFAULT_WINDOW
    previous = period.previous_version or None
    end = max(min(period.started_at + window, now), period.started_at)
    # The previous version over the same duration before the start (FR29).
    start = period.started_at - (end - period.started_at) if previous else period.started_at
    # A release counted in server traffic reads no browser-door row (FR29, AC69).
    server = release.first_seen_at is not None
    answer: dict[str, Any] = {
        "period_id": period.id,
        "version": release.version,
        "previous_version": previous,
        "start": period.started_at,
        "end": end,
        "window_hours": _hours(window),
        "groups": [],
        "truncated": False,
        "timed_out": False,
        "aged_out_on": None,
        "may_be_incomplete": _keeps_errors_off(project),
        "browser_door_read": not server,
    }
    raw_days = await queries.raw_retention_days(
        session, org_id=release.org_id, project_id=release.project_id
    )
    if raw_days is not None and start < now - timedelta(days=raw_days):
        answer["aged_out_on"] = start + timedelta(days=raw_days)
        return VersionErrors(**answer)
    if end <= start:
        return VersionErrors(**answer)
    try:
        rows = await queries.errors_by_version(
            repository,
            project_id=release.project_id,
            this_version=release.version,
            previous_version=previous or release.version,
            service_name=release.service_name,
            split_at=period.started_at,
            start=start,
            end=end,
            clock_slack=RECEIVE_CLOCK_SLACK,
            server_door_only=server,
            limit=ERROR_GROUPS_CAP + 1,
        )
    except Exception as failed:
        if server_error_code(failed) != _TIMEOUT_EXCEEDED:
            raise
        answer["timed_out"] = True
        return VersionErrors(**answer)
    answer["groups"] = [_error_group(row, compared=previous is not None) for row in rows][
        :ERROR_GROUPS_CAP
    ]
    answer["truncated"] = len(rows) > ERROR_GROUPS_CAP
    return VersionErrors(**answer)


def _keeps_errors_off(project: Project) -> bool:
    return (project.sampling_config or {}).get(KEEP_ERRORS) is False


def _error_group(row: dict[str, Any], *, compared: bool) -> VersionErrorGroup:
    mine, theirs = int(row["this_occurrences"]), int(row["previous_occurrences"])
    return VersionErrorGroup(
        group_id=str(row["group_id"]),
        exception_type=str(row["exception_type"]),
        message_template=str(row["message_template"]),
        kind=_error_kind(mine, theirs, compared=compared),
        this_occurrences=mine,
        previous_occurrences=theirs,
        from_browser=bool(row["from_browser"]),
    )


def _error_kind(mine: int, theirs: int, *, compared: bool) -> ErrorKind:
    if not compared:
        return "this_version"
    if mine and theirs:
        return "in_both"
    return "new_here" if mine else "gone_here"


# ---------------------------------------------------------- rollout share, FR26

# The versions drawn by name; the rest are "Other versions" (FR26).
ROLLOUT_NAMED: Final = 4


async def rollout(
    session: AsyncSession,
    repository: TenantScopedRepository,
    *,
    reader: Reader,
    release_id: uuid.UUID,
    period_id: uuid.UUID | None,
    window_hours: int | None,
    now: datetime,
) -> Rollout:
    """Each version's requests per minute around the period, from the hour before its
    start to the settled minute: the four busiest by name, the rest together. No version
    and the worker's fold are labels, never versions (FR26)."""
    release, period = await _readable_period(
        session, reader=reader, release_id=release_id, period_id=period_id
    )
    window = timedelta(hours=window_hours) if window_hours else method.DEFAULT_WINDOW
    compared = await _compared(session, release=release, period=period, window=window, now=now)
    start = method.read_start(period.started_at, window)
    rows, _ = await _rows_and_totals(repository, compared, start)
    series, minutes = _rollout_series(rows)
    return Rollout(
        period_id=period.id,
        series=series,
        minutes=minutes,
        as_of=compared.to if compared.as_of else None,
        compared_to=compared.to,
    )


def _rollout_series(
    rows: Sequence[method.VersionMinute],
) -> tuple[list[RolloutSeries], list[RolloutMinute]]:
    totals: dict[str, int] = defaultdict(int)
    for row in rows:
        totals[row.version] += row.requests
    named = sorted(totals, key=lambda version: (-totals[version], version))[:ROLLOUT_NAMED]
    series = [_series_of(version) for version in named]
    rest = len(totals) > len(named)
    if rest:
        series.append(RolloutSeries(kind="other_versions", version=None))
    by_minute: dict[datetime, list[int]] = defaultdict(lambda: [0] * len(series))
    for row in rows:
        place = named.index(row.version) if row.version in named else len(named)
        by_minute[row.minute][place] += row.requests
    return series, [
        RolloutMinute(minute=minute, requests=counts)
        for minute, counts in sorted(by_minute.items())
    ]


def _series_of(version: str) -> RolloutSeries:
    if version == method.NO_VERSION:
        return RolloutSeries(kind="no_version", version=None)
    if version == method.OTHER_VERSION:
        return RolloutSeries(kind="combined", version=None)
    return RolloutSeries(kind="version", version=version)


# ------------------------------------------------------- sender's details, FR54

# Who may read a sender's free-text `metadata` (D46): members and above. A viewer reads
# the link and the source, which a person on call needs, and not the free text.
READS_SENDER_METADATA: Final = frozenset({"owner", "admin", "member"})


async def sender_details(
    session: AsyncSession, *, identity: Identity, release_id: uuid.UUID
) -> SenderDetails:
    """What each sender wrote beside the release's deploys (FR54): to a signed-in person
    only, never a token or a ticket (the route takes an identity), as plain text."""
    reader = Reader(org_id=identity.org_id, identity=identity)
    release = await _readable_release(session, reader=reader, release_id=release_id)
    may_read_metadata = identity.user.role in READS_SENDER_METADATA
    return SenderDetails(
        deploys=[
            SenderDeploy(
                deploy_id=row.id,
                deployed_at=row.deployed_at,
                url=row.url,
                source=row.source,
                metadata=dict(row.event_metadata) if may_read_metadata else None,
                metadata_withheld=not may_read_metadata,
            )
            for row in await queries.sender_text_of(
                session, org_id=release.org_id, release_id=release.id
            )
        ]
    )


# ------------------------------------------------------------------ markers

# Marks one window can carry (FR35; a guess). Past it, the newest are kept and the
# screen says so.
MARKER_CAP: Final = 500


async def markers_in_window(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    service: str | None,
) -> MarkerList:
    """The marks for a window (design, *Release markers on charts*; D30): each reported
    deploy, and each counted period's start. Postgres only, so charts keep their marks
    through a ClickHouse outage. F20's dashboards call this too, so one install draws
    one set of marks. The caller has proved the project readable."""
    keys: dict[str, Any] = {
        "org_id": org_id,
        "project_id": project_id,
        "start": start,
        "end": end,
        "service": service,
        "limit": MARKER_CAP + 1,
    }
    reported = [
        Marker(
            release_id=row.release_id,
            period_id=None,
            deploy_id=row.id,
            service_name=row.service_name,
            version=row.version,
            at=row.deployed_at,
            basis="reported",
            state=row.state,
        )
        for row in await queries.reported_marks(session, **keys)
    ]
    seen = [
        Marker(
            release_id=row.release_id,
            period_id=row.id,
            deploy_id=None,
            service_name=row.service_name,
            version=row.version,
            at=row.started_at,
            basis="seen",
            state=None,
        )
        for row in await queries.seen_marks(session, **keys)
    ]
    newest = sorted([*reported, *seen], key=lambda mark: mark.at, reverse=True)
    return MarkerList(
        markers=newest[:MARKER_CAP], truncated=len(newest) > MARKER_CAP, cap=MARKER_CAP
    )


async def list_markers(
    session: AsyncSession,
    *,
    reader: Reader,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    service: str | None,
) -> MarkerList:
    project = await require_readable_project(session, reader=reader, project_id=project_id)
    return await markers_in_window(
        session, org_id=reader.org_id, project_id=project.id, start=start, end=end, service=service
    )


# ----------------------------------------------------------------- coverage


async def coverage_at(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    services: Sequence[str] | None,
    at: datetime,
) -> dict[str, method.Coverage]:
    """Each service's coverage as of `at` (FR38), by the shared rule the scheduler uses.
    With `services` None, every service that sent telemetry or has a release."""
    heard = await queries.heard_by_service(
        session, org_id=org_id, project_id=project_id, services=services
    )
    last = await queries.last_reports_before(
        session, org_id=org_id, project_id=project_id, services=services, at=at
    )
    names = (
        set(services)
        if services is not None
        else {
            *heard,
            *await queries.services_sending(session, org_id=org_id, project_id=project_id),
        }
    )
    return {
        name: method.coverage_of(
            *heard.get(name, (None, None)), at=at, last_reported_at=last.get(name)
        )
        for name in sorted(names)
    }


# Strongest first: a group on two services is as well covered as its better-heard one.
_COVERAGE_STRENGTH: Final = (
    method.DEPLOYS_AND_VERSIONS,
    method.DEPLOYS_ONLY,
    method.VERSIONS_ONLY,
    method.COVERAGE_NONE,
)


@dataclass(frozen=True, slots=True)
class CoverageBook:
    """Each service's earliest report and earliest counted version, read once per
    request and asked once per row (FR38), as `DeployHistory` is for the deploy before.
    Only the value: a row's last report is its preceding deploy."""

    heard: Mapping[str, tuple[datetime | None, datetime | None]]

    def value_of(self, services: Sequence[str], *, at: datetime) -> str:
        values = [
            method.coverage_of(
                *self.heard.get(service, (None, None)), at=at, last_reported_at=None
            ).value
            for service in services
        ]
        return min(values, key=_COVERAGE_STRENGTH.index, default=method.COVERAGE_NONE)


async def coverage_book(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> CoverageBook:
    """The caller has proved the project readable."""
    return CoverageBook(
        await queries.heard_by_service(session, org_id=org_id, project_id=project_id, services=None)
    )


async def list_coverage(
    session: AsyncSession,
    *,
    reader: Reader,
    project_id: uuid.UUID,
    service: str | None,
    at: datetime,
) -> CoverageList:
    project = await require_readable_project(session, reader=reader, project_id=project_id)
    found = await coverage_at(
        session,
        org_id=reader.org_id,
        project_id=project.id,
        services=None if service is None else [service],
        at=at,
    )
    return CoverageList(
        at=at,
        services=[
            ServiceCoverage(
                service_name=name,
                coverage=coverage.value,
                last_reported_at=coverage.last_reported_at,
                first_heard_at=coverage.first_heard_at,
            )
            for name, coverage in found.items()
        ],
    )


# ---------------------------------------------------------- filing what is reported
#
# F28 design 3, *From the webhook*, and D37's removal. One rule for a period's start
# (FR42): a report opens a period, `start_basis` reported, only for a release that has
# none; a period once counted is never moved by a report. On a service that sends no
# version in traffic, reports alone decide its periods (FR19): a newer report ends what
# served before it, and an ended version reported again later than every other start
# comes back. A service with versions in traffic is the sync's to end and return.
# Only a deploy that went out counts (founder, 2026-11-09): a `failed` or `rolled_back`
# report is filed and its state shown, but it opens no period, ends none and brings no
# version back, since it did not replace what was running.


@dataclass(frozen=True, slots=True)
class FiledReport:
    """A stored deploy, filed under its release."""

    service_name: str
    release_id: uuid.UUID
    deployed_at: datetime
    state: str


async def make_room(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    keys: Sequence[tuple[str, str]],
) -> int:
    """Evict before creating, so a project over the cap still takes a new release (FR56).
    Answers how many were evicted."""
    existing = await queries.existing_releases(
        session, org_id=org_id, project_id=project_id, keys=keys
    )
    new = len(set(keys) - existing)
    if not new:
        return 0
    held = await queries.release_count(session, org_id=org_id, project_id=project_id)
    excess = held + new - method.RELEASES_PER_PROJECT
    if excess <= 0:
        return 0
    return await queries.evict_releases(
        session, org_id=org_id, project_id=project_id, keep=keys, excess=excess
    )


@dataclass(frozen=True, slots=True)
class OpenedPeriod:
    """A period a report opened, for its summary job (FR55)."""

    id: uuid.UUID
    started_at: datetime


async def place_periods(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, filed: Sequence[FiledReport]
) -> list[OpenedPeriod]:
    """Open or return the periods these reports call for, service by service in name
    order, each service's reports in the order they claim to have happened. A report of
    a deploy that did not go out calls for none. Answers the periods opened."""
    by_service: dict[str, list[FiledReport]] = defaultdict(list)
    for report in filed:
        if report.state in method.OPENING_STATES:
            by_service[report.service_name].append(report)
    opened: list[OpenedPeriod] = []
    for service_name in sorted(by_service):
        await queries.lock_service_periods(
            session, org_id=org_id, project_id=project_id, service_name=service_name
        )
        sends_versions = await queries.service_sends_versions(
            session, org_id=org_id, project_id=project_id, service_name=service_name
        )
        for report in sorted(by_service[service_name], key=lambda one: one.deployed_at):
            period_id = await _place_one(
                session,
                org_id=org_id,
                project_id=project_id,
                report=report,
                sends_versions=sends_versions,
            )
            if period_id is not None:
                opened.append(OpenedPeriod(period_id, report.deployed_at))
    return opened


async def _place_one(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    report: FiledReport,
    sends_versions: bool,
) -> uuid.UUID | None:
    on = queries.ServiceRelease(org_id, project_id, report.service_name, report.release_id)
    latest = await queries.latest_period(session, org_id=org_id, release_id=report.release_id)
    if latest is None:
        is_return = False
    elif latest.ended_at is None or sends_versions:
        return None
    else:
        others_latest = await queries.latest_other_start(session, on)
        if others_latest is None or report.deployed_at <= others_latest:
            return None
        is_return = True
    ended_at = None
    if not sends_versions:
        ended_at = await queries.next_other_start(session, on, after=report.deployed_at)
        await queries.end_open_periods_before(session, on, at=report.deployed_at)
    return await queries.open_reported_period(
        session,
        on,
        started_at=report.deployed_at,
        ended_at=ended_at,
        previous_version=await queries.version_before(session, on, before=report.deployed_at),
        is_return=is_return,
    )


async def queue_summaries(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    opened: Sequence[OpenedPeriod],
    now: datetime,
) -> None:
    """Each opened period's stored reading and summary, due 30 minutes after its start
    (FR55, design section 6), added in the caller's transaction."""
    for period in opened:
        await queries.queue_summary(
            session,
            method.summarize_request(
                org_id,
                project_id,
                period.id,
                countdown=method.summary_countdown(period.started_at, now),
            ),
        )


async def recalculate(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    affected: Sequence[tuple[str, uuid.UUID]],
) -> None:
    """After deploys are removed (D37, AC61): each affected release's reported facts are
    worked out again from the deploys left, a release nothing supports is deleted, and
    the periods that reports decided are rebuilt from those that remain."""
    by_service: dict[str, set[uuid.UUID]] = defaultdict(set)
    for service_name, release_id in affected:
        by_service[service_name].add(release_id)
    for service_name in sorted(by_service):
        await queries.lock_service_periods(
            session, org_id=org_id, project_id=project_id, service_name=service_name
        )
        survivors = [
            release_id
            for release_id in sorted(by_service[service_name])
            if await _restate(session, org_id=org_id, release_id=release_id)
        ]
        if await queries.service_sends_versions(
            session, org_id=org_id, project_id=project_id, service_name=service_name
        ):
            for release_id in survivors:
                await queries.delete_reported_periods(session, org_id=org_id, release_id=release_id)
            refile = [
                row
                for row in await queries.reports_on_service(
                    session, org_id=org_id, project_id=project_id, service_name=service_name
                )
                if row.release_id in survivors
            ]
        else:
            await queries.delete_service_periods(
                session, org_id=org_id, project_id=project_id, service_name=service_name
            )
            refile = list(
                await queries.reports_on_service(
                    session, org_id=org_id, project_id=project_id, service_name=service_name
                )
            )
        await place_periods(
            session,
            org_id=org_id,
            project_id=project_id,
            filed=[
                FiledReport(service_name, row.release_id, row.deployed_at, row.state)
                for row in refile
            ],
        )


async def _restate(session: AsyncSession, *, org_id: uuid.UUID, release_id: uuid.UUID) -> bool:
    """Work a release's reported facts out again; False when it was deleted."""
    reports = await queries.reports_of(session, org_id=org_id, release_id=release_id)
    if not reports and await queries.delete_unseen_release(
        session, org_id=org_id, release_id=release_id
    ):
        return False
    latest = reports[-1] if reports else None
    await queries.set_reported(
        session,
        org_id=org_id,
        release_id=release_id,
        first=reports[0].deployed_at if reports else None,
        last=None if latest is None else latest.deployed_at,
        count=len(reports),
        state=None if latest is None else latest.state,
    )
    return True
