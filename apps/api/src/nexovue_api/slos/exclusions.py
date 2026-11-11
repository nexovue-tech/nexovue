"""Time left out of a target, with a reason (F19 FR29, BR6, BR7).

An exclusion is one target's, 10 to 500 characters of reason, at most 7 days long, and
a window may leave out at most 7 of its 28 days, or 2 of its 7. It may start in the
future, or in the past back to the recount limit, and a target holds at most 50 whose
time lies within the figures kept (each limit a guess, in the spec's register).

Creating or removing one leaves `counted_at` empty, which the scheduler's counts read as
"recount the days this touches" (design, *API*: "queues a recount of the days touched");
its notice and its mark on an open burn are the scheduler's too (`slos/notices.py`).
Removing one whose days reach past the recount limit is recorded as not recounted.

Each write records its audit entry with ids only (AC34, AC88). The reason is free text a
person typed: it never enters an incident or a notification (FR33).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final

from nexovue_platform import slo
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity, Reader
from nexovue_api.data.postgres.models import SloExclusion, SloTarget
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.projects.service import require_readable_project
from nexovue_api.slos import queries
from nexovue_api.slos.schemas import SloExclusion as SloExclusionView
from nexovue_api.slos.schemas import SloExclusionCreate, SloExclusions
from nexovue_api.slos.service import NO_SUCH_TARGET, zone_of

NO_SUCH_EXCLUSION: Final = "No such exclusion on this target."
ENDS_BEFORE_IT_STARTS: Final = "An exclusion ends after it starts."
TOO_LONG: Final = (
    f"An exclusion may be at most {slo.EXCLUSION_LONGEST.days} days long. Enter longer work "
    "as more than one."
)
TOO_MUCH: Final = (
    "A window may leave out at most {most} days of its {window}. This would leave out more."
)
BEFORE_THE_LIMIT: Final = (
    "An exclusion may reach back only to {limit}: older days are no longer counted again."
)
TOO_MANY: Final = (
    f"A target holds at most {slo.EXCLUSIONS_MOST} exclusions within its kept figures. "
    "Remove one you no longer need."
)
# `service_stats_1m` holds at most 35 days on any plan (FR27).
SERVICE_ROLLUP_DAYS: Final = 35


async def list_exclusions(
    db: AsyncSession, *, reader: Reader, target_id: uuid.UUID
) -> SloExclusions:
    target = await _readable(db, reader, target_id)
    rows = await queries.exclusions_of(db, org_id=reader.org_id, target_id=target.id)
    return SloExclusions(
        exclusions=[
            SloExclusionView(
                id=row.id,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
                reason=row.reason,
                created_at=row.created_at,
                created_by_name=None if reader.is_token else created_by,
                removed_at=row.removed_at,
                removed_by_name=None if reader.is_token else removed_by,
                removal_not_recounted=row.removal_not_recounted,
            )
            for row, created_by, removed_by in rows
        ]
    )


async def create(
    db: AsyncSession,
    *,
    identity: Identity,
    target_id: uuid.UUID,
    request: SloExclusionCreate,
    now: datetime,
) -> SloExclusionView:
    org_id = identity.organization.id
    target = await _live(db, org_id, target_id)
    held = await queries.live_exclusions(db, org_id=org_id, target_id=target.id)
    await _refuse(db, target, request, held, now=now)
    exclusion = SloExclusion(
        org_id=org_id,
        project_id=target.project_id,
        slo_id=target.id,
        starts_at=request.starts_at,
        ends_at=request.ends_at,
        reason=request.reason,
        created_by=identity.user.id,
        created_at=now,
    )
    db.add(exclusion)
    await db.flush()
    audit.record_slo_excluded(
        db, target=target, exclusion_id=exclusion.id, actor_user_id=identity.user.id
    )
    await db.commit()
    return _view(exclusion, created_by=identity.user.name)


async def remove(
    db: AsyncSession,
    *,
    identity: Identity,
    target_id: uuid.UUID,
    exclusion_id: uuid.UUID,
    now: datetime,
) -> None:
    """Removed by a mark, so it stays visible; its days are counted again where the
    recount limit allows, and it says so where it does not (FR29, AC69)."""
    org_id = identity.organization.id
    target = await _live(db, org_id, target_id)
    exclusion = await queries.exclusion_for_change(
        db, org_id=org_id, target_id=target.id, exclusion_id=exclusion_id
    )
    if exclusion is None or exclusion.removed_at is not None:
        raise NotFoundError(NO_SUCH_EXCLUSION)
    limit = await _recount_limit(db, target, now=now)
    exclusion.removed_at = now
    exclusion.removed_by = identity.user.id
    exclusion.counted_at = None
    exclusion.removal_not_recounted = exclusion.starts_at < limit
    audit.record_slo_exclusion_removed(
        db, target=target, exclusion_id=exclusion.id, actor_user_id=identity.user.id
    )
    await db.commit()


async def _refuse(
    db: AsyncSession,
    target: SloTarget,
    request: SloExclusionCreate,
    held: Sequence[SloExclusion],
    *,
    now: datetime,
) -> None:
    if request.ends_at <= request.starts_at:
        raise InvalidRequestError(ENDS_BEFORE_IT_STARTS, field="ends_at")
    if request.ends_at - request.starts_at > slo.EXCLUSION_LONGEST:
        raise InvalidRequestError(TOO_LONG, field="ends_at")
    limit = await _recount_limit(db, target, now=now)
    if request.starts_at < limit:
        raise InvalidRequestError(
            BEFORE_THE_LIMIT.format(limit=f"{limit:%d %b %Y}"), field="starts_at"
        )
    kept_from = now - slo.FIGURES_KEPT
    if len([each for each in held if each.ends_at > kept_from]) >= slo.EXCLUSIONS_MOST:
        raise ConflictError(TOO_MANY)
    spans = [(each.starts_at, each.ends_at) for each in held]
    spans.append((request.starts_at, request.ends_at))
    window = timedelta(days=target.window_days)
    most = slo.EXCLUDED_MOST_BY_WINDOW[target.window_days]
    if most_in_any_window(spans, window) > most:
        raise InvalidRequestError(
            TOO_MUCH.format(most=most.days, window=target.window_days), field="ends_at"
        )


def most_in_any_window(spans: Sequence[tuple[datetime, datetime]], window: timedelta) -> timedelta:
    """The most time the spans cover inside any stretch `window` long. The busiest
    stretch starts at some span's start or ends at some span's end, so those are tried."""
    starts = [start for start, _ in spans] + [end - window for _, end in spans]
    return max((_covered(spans, start, start + window) for start in starts), default=timedelta(0))


def _covered(
    spans: Sequence[tuple[datetime, datetime]], start: datetime, end: datetime
) -> timedelta:
    clipped = sorted((max(a, start), min(b, end)) for a, b in spans if b > start and a < end)
    total, reach = timedelta(0), start
    for a, b in clipped:
        a = max(a, reach)
        if b > a:
            total += b - a
            reach = b
    return total


async def _recount_limit(db: AsyncSession, target: SloTarget, *, now: datetime) -> datetime:
    """FR28's limit, as the start of its day in the project's zone."""
    project = await queries.project_of(db, org_id=target.org_id, project_id=target.project_id)
    zone = zone_of(project)
    reach = await queries.derived_retention_days(db, org_id=target.org_id) or SERVICE_ROLLUP_DAYS
    if target.kind in (slo.SERVICE_AVAILABILITY, slo.SERVICE_LATENCY):
        reach = min(reach, SERVICE_ROLLUP_DAYS)
    day = slo.recount_limit(now.astimezone(zone).date(), retention_days=reach)
    return slo.day_bounds(day, zone)[0]


async def _readable(db: AsyncSession, reader: Reader, target_id: uuid.UUID) -> SloTarget:
    target = await queries.target_for_read(db, org_id=reader.org_id, target_id=target_id)
    if target is None or target.deleted_at is not None:
        raise NotFoundError(NO_SUCH_TARGET)
    await require_readable_project(db, reader=reader, project_id=target.project_id)
    return target


async def _live(db: AsyncSession, org_id: uuid.UUID, target_id: uuid.UUID) -> SloTarget:
    target = await queries.target_for_change(db, org_id=org_id, target_id=target_id)
    if target is None or target.deleted_at is not None:
        raise NotFoundError(NO_SUCH_TARGET)
    return target


def _view(row: SloExclusion, *, created_by: str | None) -> SloExclusionView:
    return SloExclusionView(
        id=row.id,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        reason=row.reason,
        created_at=row.created_at,
        created_by_name=created_by,
        removed_at=row.removed_at,
        removed_by_name=None,
        removal_not_recounted=row.removal_not_recounted,
    )
