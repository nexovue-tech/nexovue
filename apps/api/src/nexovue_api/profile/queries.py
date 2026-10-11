"""Postgres reads behind a person's export. Every one names its organization and
the person, both taken from the session."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import ApiToken, AuditLogEntry, SessionRecord, User


async def live_sessions(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID, now: datetime
) -> list[SessionRecord]:
    statement = (
        select(SessionRecord)
        .where(
            SessionRecord.org_id == org_id,
            SessionRecord.user_id == user_id,
            SessionRecord.expires_at > now,
        )
        .order_by(SessionRecord.created_at.desc())
    )
    return list(await session.scalars(statement))


async def activity(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID, limit: int
) -> list[AuditLogEntry]:
    """What this person did, newest first."""
    statement = (
        select(AuditLogEntry)
        .where(AuditLogEntry.org_id == org_id, AuditLogEntry.actor_user_id == user_id)
        .order_by(AuditLogEntry.at.desc(), AuditLogEntry.id.desc())
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def tokens_created_by(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID
) -> list[ApiToken]:
    statement = (
        select(ApiToken)
        .where(ApiToken.org_id == org_id, ApiToken.created_by == user_id)
        .order_by(ApiToken.created_at)
    )
    return list(await session.scalars(statement))


async def invited_by(session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID) -> list[User]:
    """Everyone this person invited, removed or not: the invite was still sent."""
    statement = (
        select(User)
        .where(User.org_id == org_id, User.invited_by == user_id)
        .order_by(User.invited_at)
    )
    return list(await session.scalars(statement))
