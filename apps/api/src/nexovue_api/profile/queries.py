"""Postgres reads behind a person's export. Every one names its organization and
the person, both taken from the session."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    ApiToken,
    AssistantMessage,
    AssistantStep,
    AuditLogEntry,
    FindingNote,
    Runbook,
    SessionRecord,
    User,
)


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


async def notes_written(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID, limit: int
) -> list[FindingNote]:
    """The case notes this person wrote, newest first (F22 AC25), served by
    `ix_finding_notes_org_id_author_id`."""
    statement = (
        select(FindingNote)
        .where(FindingNote.org_id == org_id, FindingNote.author_id == user_id)
        .order_by(FindingNote.created_at.desc(), FindingNote.id.desc())
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def chat_questions(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID, limit: int
) -> list[AssistantMessage]:
    """The questions this person asked chat, newest first (F4 AC34)."""
    statement = (
        select(AssistantMessage)
        .where(AssistantMessage.org_id == org_id, AssistantMessage.user_id == user_id)
        .order_by(AssistantMessage.created_at.desc(), AssistantMessage.id.desc())
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def runbooks_written(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID, limit: int
) -> list[Runbook]:
    """The runbooks this person wrote, newest first (F4 AC34), served by
    `ix_runbooks_org_id_created_by`."""
    statement = (
        select(Runbook)
        .where(Runbook.org_id == org_id, Runbook.created_by == user_id)
        .order_by(Runbook.created_at.desc(), Runbook.id.desc())
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def chat_steps(
    session: AsyncSession, *, org_id: uuid.UUID, message_ids: list[uuid.UUID]
) -> list[AssistantStep]:
    """The steps of those questions, in order (F4 AC34)."""
    if not message_ids:
        return []
    statement = (
        select(AssistantStep)
        .where(AssistantStep.org_id == org_id, AssistantStep.message_id.in_(message_ids))
        .order_by(AssistantStep.message_id, AssistantStep.position)
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
