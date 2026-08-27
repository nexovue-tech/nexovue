"""Project logic, and the check every telemetry query depends on.

`require_project` is the gate: a `project_id` from a query string is a value the
caller chose, and it becomes a filter on a telemetry query only after it has
been proved to belong to the caller's organization.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import Project
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.projects import queries
from nexovue_api.projects.schemas import ProjectListResponse, ProjectSummary


async def list_for_organization(session: AsyncSession, org_id: uuid.UUID) -> ProjectListResponse:
    projects = await queries.list_projects(session, org_id)
    return ProjectListResponse(
        projects=[
            ProjectSummary(id=project.id, slug=project.slug, name=project.name)
            for project in projects
        ]
    )


async def require_project(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    """The project, or a 404 that does not say whether it exists elsewhere."""
    project = await queries.find_project(session, org_id=org_id, project_id=project_id)
    if project is None:
        raise NotFoundError("No such project.")
    return project
