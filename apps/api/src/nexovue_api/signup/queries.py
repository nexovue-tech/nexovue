"""The reads signup does before it writes.

Each of these backs a unique constraint. The constraint is the guarantee; these
exist so the common case is a clear 409 or a free slug rather than a caught
integrity error.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import Organization, User


async def email_is_taken(session: AsyncSession, email: str) -> bool:
    """Addresses are globally unique: one user belongs to one organization."""
    statement = select(User.id).where(User.email == email).limit(1)
    return await session.scalar(statement) is not None


async def taken_slugs(session: AsyncSession, base: str) -> set[str]:
    """Every slug that could collide with `base` or one of its suffixed forms."""
    statement = select(Organization.slug).where(Organization.slug.startswith(base))
    return set((await session.scalars(statement)).all())
