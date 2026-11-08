"""F19's Postgres statements for the API. Every one binds `org_id` (NFR5)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from nexovue_platform.alerts.model import service_subject
from sqlalchemy import Text, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    Check,
    Finding,
    Incident,
    ServiceLastSeen,
    SloDailyCount,
    SloDeletedMonth,
    SloStatus,
    SloTarget,
    SloTargetChange,
    User,
)


async def targets_with_status(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[Any]:
    """A project's targets, live and deleted, each with its status if it has one, the
    name of whoever deleted it, and a check target's check name and interval."""
    statement = (
        _with_status_and_check(org_id)
        .where(SloTarget.project_id == project_id)
        .order_by(SloTarget.created_at, SloTarget.id)
    )
    return (await db.execute(statement)).all()


async def target_with_status(
    db: AsyncSession, *, org_id: uuid.UUID, target_id: uuid.UUID
) -> Any | None:
    statement = _with_status_and_check(org_id).where(SloTarget.id == target_id)
    return (await db.execute(statement)).one_or_none()


def _with_status_and_check(org_id: uuid.UUID) -> Any:
    """One organization's rows of (target, status, deleted-by name, check name, interval)."""
    return (
        select(SloTarget, SloStatus, User.name, Check.name, Check.interval_seconds)
        .outerjoin(
            SloStatus, (SloStatus.slo_id == SloTarget.id) & (SloStatus.org_id == SloTarget.org_id)
        )
        .outerjoin(User, (User.id == SloTarget.deleted_by) & (User.org_id == SloTarget.org_id))
        .outerjoin(Check, (Check.id == SloTarget.check_id) & (Check.org_id == SloTarget.org_id))
        .where(SloTarget.org_id == org_id)
    )


async def check_in_project(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, check_id: uuid.UUID
) -> Check | None:
    """The check, only when it is this project's: a target's source is checked against
    its project at write (design, *Tenancy path*)."""
    statement = select(Check).where(
        Check.org_id == org_id, Check.project_id == project_id, Check.id == check_id
    )
    found: Check | None = await db.scalar(statement)
    return found


async def has_checks(db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> bool:
    statement = select(Check.id).where(Check.org_id == org_id, Check.project_id == project_id)
    return await db.scalar(statement.limit(1)) is not None


async def target_for_read(
    db: AsyncSession, *, org_id: uuid.UUID, target_id: uuid.UUID
) -> SloTarget | None:
    """The row, live or deleted, for a read that a deletion does not end (FR30)."""
    statement = select(SloTarget).where(SloTarget.org_id == org_id, SloTarget.id == target_id)
    found: SloTarget | None = await db.scalar(statement)
    return found


async def target_for_change(
    db: AsyncSession, *, org_id: uuid.UUID, target_id: uuid.UUID
) -> SloTarget | None:
    """The row, locked for this transaction's change."""
    statement = (
        select(SloTarget)
        .where(SloTarget.org_id == org_id, SloTarget.id == target_id)
        .with_for_update()
    )
    return (await db.execute(statement)).scalar_one_or_none()


async def days_since(
    db: AsyncSession, *, org_id: uuid.UUID, target_ids: Sequence[uuid.UUID], since: date
) -> Sequence[SloDailyCount]:
    if not target_ids:
        return []
    statement = select(SloDailyCount).where(
        SloDailyCount.org_id == org_id,
        SloDailyCount.slo_id.in_(target_ids),
        SloDailyCount.day >= since,
    )
    return (await db.execute(statement)).scalars().all()


async def live_count(db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> int:
    statement = select(func.count()).where(
        SloTarget.org_id == org_id,
        SloTarget.project_id == project_id,
        SloTarget.deleted_at.is_(None),
    )
    return int(await db.scalar(statement) or 0)


async def live_holding(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    name: str | None = None,
    definition_key: str | None = None,
    other_than: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """The live target already holding a name or a definition, if any."""
    statement = select(SloTarget.id).where(
        SloTarget.org_id == org_id,
        SloTarget.project_id == project_id,
        SloTarget.deleted_at.is_(None),
    )
    if name is not None:
        statement = statement.where(SloTarget.name == name)
    if definition_key is not None:
        statement = statement.where(SloTarget.definition_key == definition_key)
    if other_than is not None:
        statement = statement.where(SloTarget.id != other_than)
    holder: uuid.UUID | None = await db.scalar(statement.limit(1))
    return holder


async def has_reported(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, service_name: str
) -> bool:
    """Whether the project has ever heard from this service, as the usage meter records it."""
    statement = select(ServiceLastSeen.service_name).where(
        ServiceLastSeen.org_id == org_id,
        ServiceLastSeen.project_id == project_id,
        ServiceLastSeen.service_name == service_name,
    )
    return await db.scalar(statement.limit(1)) is not None


async def has_services(db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> bool:
    statement = select(ServiceLastSeen.service_name).where(
        ServiceLastSeen.org_id == org_id, ServiceLastSeen.project_id == project_id
    )
    return await db.scalar(statement.limit(1)) is not None


def record_change(
    db: AsyncSession,
    *,
    target: SloTarget,
    actor_user_id: uuid.UUID,
    what: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    at: datetime,
) -> None:
    """One row of FR30's history, in the caller's transaction."""
    db.add(
        SloTargetChange(
            org_id=target.org_id,
            project_id=target.project_id,
            slo_id=target.id,
            at=at,
            actor_user_id=actor_user_id,
            what=what,
            before=before,
            after=after,
        )
    )


# ------------------------------------------------------------------- months


async def deleted_months_since(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, since: date
) -> Sequence[SloDeletedMonth]:
    """Purged targets' months from `since`, for the CSV."""
    statement = select(SloDeletedMonth).where(
        SloDeletedMonth.org_id == org_id,
        SloDeletedMonth.project_id == project_id,
        SloDeletedMonth.month >= since,
    )
    return (await db.execute(statement)).scalars().all()


# FR27a: the finding signal that means a service lost its traffic, and the alert kind
# that means it fell silent.
TRAFFIC_SIGNAL = [{"signal": "traffic"}]
SILENCE_KIND = "service_silent"


async def traffic_findings(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    services: Sequence[str],
    start: datetime,
    end: datetime,
) -> Sequence[Finding]:
    """Findings of lost traffic on these services that overlap [start, end)."""
    if not services:
        return []
    statement = select(Finding).where(
        Finding.org_id == org_id,
        Finding.project_id == project_id,
        Finding.service_name.in_(services),
        Finding.signals.contains(TRAFFIC_SIGNAL),
        Finding.started_at < end,
        func.coalesce(Finding.closed_at, func.now()) > start,
    )
    return (await db.execute(statement.order_by(Finding.started_at))).scalars().all()


async def silence_incidents(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    services: Sequence[str],
    start: datetime,
    end: datetime,
) -> Sequence[Incident]:
    """Silence incidents on these services that overlap [start, end)."""
    if not services:
        return []
    statement = select(Incident).where(
        Incident.org_id == org_id,
        Incident.project_id == project_id,
        Incident.condition_kind == SILENCE_KIND,
        Incident.subject_key.in_([service_subject(name) for name in services]),
        Incident.opened_at < end,
        func.coalesce(Incident.resolved_at, func.now()) > start,
    )
    return (await db.execute(statement.order_by(Incident.opened_at))).scalars().all()


async def changes_of(db: AsyncSession, *, org_id: uuid.UUID, target_id: uuid.UUID) -> Sequence[Any]:
    """A target's history, newest first, with each actor's name (FR30)."""
    statement = (
        select(SloTargetChange, User.name)
        .outerjoin(
            User,
            (User.id == SloTargetChange.actor_user_id) & (User.org_id == SloTargetChange.org_id),
        )
        .where(SloTargetChange.org_id == org_id, SloTargetChange.slo_id == target_id)
        .order_by(SloTargetChange.at.desc(), SloTargetChange.id.desc())
    )
    return (await db.execute(statement)).all()


async def targets_naming(
    db: AsyncSession, *, org_id: uuid.UUID, needles: Sequence[str]
) -> Sequence[SloTarget]:
    """An organization's targets, live or deleted, whose name or description holds any of
    `needles`, ignoring case (AC88). A prefilter: the caller replaces whole words only."""
    found = [
        func.strpos(func.lower(column), needle.lower()) > 0
        for needle in needles
        for column in (SloTarget.name, SloTarget.description)
    ]
    statement = select(SloTarget).where(SloTarget.org_id == org_id, or_(*found))
    return (await db.execute(statement)).scalars().all()


async def changes_naming(
    db: AsyncSession, *, org_id: uuid.UUID, needles: Sequence[str]
) -> Sequence[SloTargetChange]:
    """An organization's target changes whose `before` or `after` holds any of `needles`
    at any depth, as JSON text writes them, ignoring case (AC88). A prefilter too."""
    found = [
        func.strpos(func.lower(func.cast(column, Text)), needle.lower()) > 0
        for needle in needles
        for column in (SloTargetChange.before, SloTargetChange.after)
    ]
    statement = select(SloTargetChange).where(SloTargetChange.org_id == org_id, or_(*found))
    return (await db.execute(statement)).scalars().all()


async def changes_made_by(
    db: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID, limit: int
) -> Sequence[SloTargetChange]:
    """The target changes one person made, newest first, for their export (AC88)."""
    statement = (
        select(SloTargetChange)
        .where(SloTargetChange.org_id == org_id, SloTargetChange.actor_user_id == user_id)
        .order_by(SloTargetChange.at.desc(), SloTargetChange.id.desc())
        .limit(limit)
    )
    return (await db.execute(statement)).scalars().all()
