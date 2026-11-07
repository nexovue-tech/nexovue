"""F19's Postgres statements for the API. Every one binds `org_id` (NFR5)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    ServiceLastSeen,
    SloDailyCount,
    SloStatus,
    SloTarget,
    SloTargetChange,
    User,
)


async def targets_with_status(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[Any]:
    """A project's targets, live and deleted, each with its status if it has one."""
    statement = (
        select(SloTarget, SloStatus, User.name)
        .outerjoin(
            SloStatus, (SloStatus.slo_id == SloTarget.id) & (SloStatus.org_id == SloTarget.org_id)
        )
        .outerjoin(User, (User.id == SloTarget.deleted_by) & (User.org_id == SloTarget.org_id))
        .where(SloTarget.org_id == org_id, SloTarget.project_id == project_id)
        .order_by(SloTarget.created_at, SloTarget.id)
    )
    return (await db.execute(statement)).all()


async def target_with_status(
    db: AsyncSession, *, org_id: uuid.UUID, target_id: uuid.UUID
) -> Any | None:
    statement = (
        select(SloTarget, SloStatus, User.name)
        .outerjoin(
            SloStatus, (SloStatus.slo_id == SloTarget.id) & (SloStatus.org_id == SloTarget.org_id)
        )
        .outerjoin(User, (User.id == SloTarget.deleted_by) & (User.org_id == SloTarget.org_id))
        .where(SloTarget.org_id == org_id, SloTarget.id == target_id)
    )
    return (await db.execute(statement)).one_or_none()


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
