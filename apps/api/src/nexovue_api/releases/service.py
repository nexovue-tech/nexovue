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
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_platform import releases as method
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.dependencies import Reader
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import Release, ReleasePeriod
from nexovue_api.detection import queries as detection_queries
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
from nexovue_api.findings.queries import derived_retention_days
from nexovue_api.projects.service import require_readable_project
from nexovue_api.releases import queries
from nexovue_api.releases.schemas import (
    Activity,
    Basis,
    BlockView,
    ComparisonView,
    LatestRelease,
    PeriodView,
    ReadingView,
    ReleaseList,
    ReleaseRow,
    ReleaseView,
    SideView,
    TrafficView,
)

NO_SUCH_RELEASE: Final = "No such release"
NO_SUCH_PERIOD: Final = "This release has no such period."
BAD_CURSOR: Final = "This page link is not one we gave out."
DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200
# A release counted this recently is serving (spec, *A release's activity*; a guess).
SERVING_WITHIN: Final = timedelta(minutes=10)
WINDOW_HOURS: Final = tuple(int(window / timedelta(hours=1)) for window in method.WINDOWS)
_CURSOR_SEPARATOR: Final = "|"


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
    )


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
        periods=[_period(period) for period in reversed(periods)],
    )


def _period(period: ReleasePeriod) -> PeriodView:
    return PeriodView(
        id=period.id,
        started_at=period.started_at,
        ended_at=period.ended_at,
        start_basis=period.start_basis,
        previous_version=period.previous_version,
        already_running=period.already_running,
        is_return=period.is_return,
    )


# ---------------------------------------------------------------- comparison


async def compare(
    session: AsyncSession,
    repository: TenantScopedRepository,
    *,
    reader: Reader,
    release_id: uuid.UUID,
    period_id: uuid.UUID | None,
    window_hours: int,
    now: datetime,
) -> ComparisonView:
    """One period against its previous version, by the design's ordered list."""
    release = await _readable_release(session, reader=reader, release_id=release_id)
    periods = await queries.periods_of(session, org_id=reader.org_id, release_id=release.id)
    period = _chosen(periods, period_id)
    window = timedelta(hours=window_hours)
    sweep = await detection_queries.detection_sweep(session)
    to, as_of = method.comparison_end(
        period.started_at + window, sweep.settled_minute if sweep else None, now
    )
    kept_days = min(
        await derived_retention_days(session, org_id=reader.org_id) or method.VERSION_ROLLUP_DAYS,
        method.VERSION_ROLLUP_DAYS,
    )
    previous_started = (
        await queries.previous_started_at(
            session,
            org_id=reader.org_id,
            project_id=release.project_id,
            service_name=release.service_name,
            version=period.previous_version,
            before=period.started_at,
        )
        if period.previous_version
        else None
    )
    start = method.read_start(period.started_at, window)
    rows = (
        await queries.version_minutes(
            repository,
            project_id=release.project_id,
            service_name=release.service_name,
            start=start,
            end=to,
        )
        if to > start
        else []
    )
    decided = method.choose_method(
        method.PeriodToCompare(
            started_at=period.started_at,
            version=release.version,
            previous_version=period.previous_version,
            already_running=period.already_running,
            previous_started_at=previous_started,
            reported_at=release.first_reported_at,
        ),
        [_minute(row) for row in rows],
        to=to,
        window=window,
        expired_before=now - timedelta(days=kept_days),
    )
    return ComparisonView(
        period_id=period.id,
        version=release.version,
        previous_version=period.previous_version,
        reason=decided.reason,
        lead=decided.lead,
        as_of=to if as_of else None,
        compared_to=to,
        window_hours=window_hours,
        blocks=[_block(block) for block in decided.blocks],
        traffic=None
        if decided.traffic is None
        else TrafficView(
            this_version=_side(decided.traffic[0]), previous_version=_side(decided.traffic[1])
        ),
        other_versions=list(decided.other_versions),
        numbers_kept_days=kept_days,
    )


def window_hours_of(requested: int | None) -> int:
    """FR22's windows; one hour by default."""
    if requested is None:
        return WINDOW_HOURS[0]
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


def _minute(row: dict[str, Any]) -> method.VersionMinute:
    return method.VersionMinute(
        minute=datetime.fromtimestamp(int(row["minute_epoch"]), UTC),
        version=str(row["version"]),
        requests=int(row["requests"]),
        errors=int(row["errors"]),
        buckets=tuple(int(count) for count in row["buckets"]),
    )


def _side(side: method.Side) -> SideView:
    return SideView(
        version=side.version, requests=side.requests, errors=side.errors, p95_ms=side.p95_ms
    )


def _reading(reading: method.Reading) -> ReadingView:
    return ReadingView(reading=reading.reading, note=reading.note)


def _block(block: method.Block) -> BlockView:
    return BlockView(
        kind=block.kind,
        method=block.method,
        start=block.start,
        end=block.end,
        minutes_compared=block.minutes_compared,
        this_version=_side(block.this_version),
        previous_version=_side(block.previous_version),
        failure_share=_reading(block.failure_share),
        p95=_reading(block.p95),
    )
