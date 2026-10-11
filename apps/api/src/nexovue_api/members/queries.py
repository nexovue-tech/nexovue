"""Postgres reads behind the members API. Every one names its organization."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Text, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import AuditLogEntry, SessionRecord, User


async def members_of(session: AsyncSession, *, org_id: uuid.UUID) -> list[User]:
    """Everyone in the organization who has not been removed, oldest first."""
    statement = (
        select(User)
        .where(User.org_id == org_id, User.removed_at.is_(None))
        .order_by(User.created_at, User.id)
    )
    return list(await session.scalars(statement))


async def find_member(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID, lock: bool = False
) -> User | None:
    """One person in this organization, not removed. `lock` holds the row until commit."""
    statement = select(User).where(
        User.org_id == org_id, User.id == user_id, User.removed_at.is_(None)
    )
    if lock:
        statement = statement.with_for_update()
    found: User | None = await session.scalar(statement)
    return found


async def owners_locked(session: AsyncSession, *, org_id: uuid.UUID) -> list[User]:
    """Every owner, locked until commit.

    The lock is what stops two owners demoting each other at the same moment from
    both succeeding and leaving the organization with none (BR2).
    """
    statement = (
        select(User)
        .where(User.org_id == org_id, User.role == "owner", User.removed_at.is_(None))
        .order_by(User.id)
        .with_for_update()
    )
    return list(await session.scalars(statement))


async def find_by_email(session: AsyncSession, email: str) -> User | None:
    """The person with this address in any organization.

    Cross-tenant by nature, like signup's `email_is_taken`: an address is globally
    unique, and an invite has to say so when it belongs to someone elsewhere (D1).
    """
    found: User | None = await session.scalar(select(User).where(User.email == email))
    return found


async def last_sign_ins(
    session: AsyncSession, *, org_id: uuid.UUID, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    if not user_ids:
        return {}
    statement = (
        select(AuditLogEntry.actor_user_id, func.max(AuditLogEntry.at))
        .where(
            AuditLogEntry.org_id == org_id,
            AuditLogEntry.action == audit.SESSION_CREATED,
            AuditLogEntry.actor_user_id.in_(user_ids),
        )
        .group_by(AuditLogEntry.actor_user_id)
    )
    rows = (await session.execute(statement)).tuples().all()
    return {user_id: at for user_id, at in rows if user_id is not None}


async def owners(session: AsyncSession, *, org_id: uuid.UUID) -> list[User]:
    """Every owner not removed, unlocked: for saying whether one may leave."""
    statement = select(User).where(
        User.org_id == org_id, User.role == "owner", User.removed_at.is_(None)
    )
    return list(await session.scalars(statement))


async def entries_about(
    session: AsyncSession, *, org_id: uuid.UUID, target: str, address: str
) -> list[AuditLogEntry]:
    """Every entry in this organization about one person, or quoting their address.

    The address match is a substring over the detail's text and only narrows the
    rows; the caller replaces exact values. `autoescape` because an address may
    hold `_`, which LIKE would read as a wildcard.
    """
    statement = select(AuditLogEntry).where(
        AuditLogEntry.org_id == org_id,
        AuditLogEntry.detail.is_not(None),
        or_(
            AuditLogEntry.target == target,
            cast(AuditLogEntry.detail, Text).contains(address, autoescape=True),
        ),
    )
    return list(await session.scalars(statement))


async def end_sessions_of(session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Delete every session row of one person: each holds where they signed in from."""
    await session.execute(
        delete(SessionRecord).where(
            SessionRecord.org_id == org_id, SessionRecord.user_id == user_id
        )
    )
