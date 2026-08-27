"""Project reads. Every one takes an org_id — there is no unscoped variant."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import Project


async def list_projects(session: AsyncSession, org_id: uuid.UUID) -> Sequence[Project]:
    """This organization's live projects, oldest first so `production` leads."""
    statement = (
        select(Project)
        .where(Project.org_id == org_id, Project.deleted_at.is_(None))
        .order_by(Project.created_at, Project.slug)
    )
    return (await session.scalars(statement)).all()


async def find_project(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Project | None:
    """One project, or None. Another tenant's project is None, not a row."""
    statement = select(Project).where(
        Project.id == project_id,
        Project.org_id == org_id,
        Project.deleted_at.is_(None),
    )
    project: Project | None = await session.scalar(statement)
    return project
