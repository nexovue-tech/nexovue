"""Postgres reads behind support access. Every one names its organization."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import AuditLogEntry, SupportAccessGrant


async def active_grant(
    session: AsyncSession, *, org_id: uuid.UUID, at: datetime
) -> SupportAccessGrant | None:
    """The owner's consent in force at `at`, if there is one."""
    statement = (
        select(SupportAccessGrant)
        .where(
            SupportAccessGrant.org_id == org_id,
            SupportAccessGrant.granted_at <= at,
            SupportAccessGrant.expires_at > at,
            SupportAccessGrant.withdrawn_at.is_(None),
        )
        .order_by(SupportAccessGrant.granted_at.desc())
        .limit(1)
    )
    found: SupportAccessGrant | None = await session.scalar(statement)
    return found


async def grant_is_live(
    session: AsyncSession, *, org_id: uuid.UUID, grant_id: uuid.UUID, at: datetime
) -> bool:
    """Whether the grant a support session was started under still stands."""
    statement = select(SupportAccessGrant.id).where(
        SupportAccessGrant.org_id == org_id,
        SupportAccessGrant.id == grant_id,
        SupportAccessGrant.expires_at > at,
        SupportAccessGrant.withdrawn_at.is_(None),
    )
    return await session.scalar(statement) is not None


async def recent_views(
    session: AsyncSession, *, org_id: uuid.UUID, limit: int = 20
) -> list[AuditLogEntry]:
    statement = (
        select(AuditLogEntry)
        .where(
            AuditLogEntry.org_id == org_id,
            AuditLogEntry.action == audit.SUPPORT_VIEW_STARTED,
        )
        .order_by(AuditLogEntry.at.desc())
        .limit(limit)
    )
    return list(await session.scalars(statement))
