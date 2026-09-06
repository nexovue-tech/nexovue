"""Reading a project's uploaded source maps.

`project_id` arrives as a query parameter, which makes it a value the caller
chose. `require_project` proves it belongs to the caller's organization, and
every statement behind this route binds `org_id` as well, so the proof is not
the only thing standing between two tenants.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.deps import DbSession
from nexovue_api.projects.service import require_project
from nexovue_api.sourcemaps import service
from nexovue_api.sourcemaps.schemas import SourceMapListResponse
from nexovue_api.window import clamp_limit

router = APIRouter(prefix="/source-maps", tags=["source-maps"])

ProjectId = Annotated[uuid.UUID, Query(description="Project the maps were uploaded for")]


@router.get("", summary="Source maps uploaded for a project, newest first")
async def list_source_maps(
    identity: CurrentIdentity,
    db: DbSession,
    project_id: ProjectId,
    limit: Annotated[int | None, Query(ge=1, le=service.MAX_LIMIT)] = None,
) -> SourceMapListResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    return await service.list_source_maps(
        db,
        org_id=identity.org_id,
        project_id=project.id,
        limit=clamp_limit(limit, default=service.DEFAULT_LIMIT, maximum=service.MAX_LIMIT),
    )
