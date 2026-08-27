"""Postgres reads behind the identity endpoints."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import Organization, User


async def find_active_user_by_email(
    session: AsyncSession, email: str
) -> tuple[User, Organization] | None:
    """The user with this address and their organization, if it is not deleted.

    Both in one statement: login needs the organization for its response, and a
    second round trip would only widen the timing gap between a hit and a miss.
    """
    statement = (
        select(User, Organization)
        .join(Organization, Organization.id == User.org_id)
        .where(User.email == email, Organization.deleted_at.is_(None))
    )
    row = (await session.execute(statement)).first()
    return (row[0], row[1]) if row is not None else None


async def load_identity(
    session: AsyncSession, *, user_id: uuid.UUID, org_id: uuid.UUID
) -> tuple[User, Organization] | None:
    """The user and organization a session names.

    `org_id` is part of the lookup rather than read off the row: a session whose
    two ids have stopped agreeing — a user moved, an organization deleted — must
    resolve to nothing rather than to a tenant it no longer belongs to.
    """
    statement = (
        select(User, Organization)
        .join(Organization, Organization.id == User.org_id)
        .where(
            User.id == user_id,
            User.org_id == org_id,
            Organization.deleted_at.is_(None),
        )
    )
    row = (await session.execute(statement)).first()
    return (row[0], row[1]) if row is not None else None
