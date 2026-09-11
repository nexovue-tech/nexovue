"""Connector reads and writes. Every one takes an org_id — there is no unscoped variant.

The tenant predicate is in the statement rather than checked after the row comes
back, so another organization's connector returns nothing and "not yours" is
indistinguishable from "does not exist".
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import Integration


async def list_integrations(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[Integration]:
    statement = (
        select(Integration)
        .where(Integration.org_id == org_id, Integration.project_id == project_id)
        .order_by(Integration.created_at, Integration.id)
    )
    return (await session.scalars(statement)).all()


def add_integration(session: AsyncSession, integration: Integration) -> None:
    """Stage a new connector. The caller's transaction commits it."""
    session.add(integration)


async def delete_integration(
    session: AsyncSession, *, org_id: uuid.UUID, integration_id: uuid.UUID
) -> bool:
    """Remove one connector, reporting whether there was one to remove."""
    statement = (
        delete(Integration)
        .where(Integration.id == integration_id, Integration.org_id == org_id)
        .returning(Integration.id)
    )
    return await session.scalar(statement) is not None
