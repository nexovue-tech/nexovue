"""Uploading, listing and using a project's source maps.

`project_id` arrives as a query parameter, which makes it a value the caller
chose. `require_project` proves it belongs to the caller's organization, and
every statement behind these routes binds `org_id` as well, so the proof is not
the only thing standing between two tenants.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, UploadFile, status

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayUploadSourceMaps
from nexovue_api.deps import DbSession, ObjectStoreDep
from nexovue_api.projects.service import require_project
from nexovue_api.sourcemaps import service
from nexovue_api.sourcemaps.schemas import (
    SourceMapListResponse,
    SourceMapSummary,
    SymbolicateRequest,
    SymbolicateResponse,
)
from nexovue_api.window import clamp_limit

router = APIRouter(prefix="/source-maps", tags=["source-maps"])

ProjectId = Annotated[uuid.UUID, Query(description="Project the maps were uploaded for")]

# The build a map belongs to. `release` is required even when a debug id is
# present: the debug id makes the lookup exact, and the release is what a person
# reads in a list of uploads.
ReleaseForm = Annotated[str, Form(min_length=1, max_length=200)]
MinifiedPathForm = Annotated[str, Form(min_length=1, max_length=1024)]
DebugIdForm = Annotated[str, Form(max_length=64)]


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


@router.post("", status_code=status.HTTP_201_CREATED, summary="Upload one source map")
async def upload_source_map(
    identity: MayUploadSourceMaps,
    db: DbSession,
    store: ObjectStoreDep,
    project_id: ProjectId,
    release: ReleaseForm,
    minified_path: MinifiedPathForm,
    source_map: Annotated[UploadFile, File(description="The .map file the build produced")],
    debug_id: DebugIdForm = "",
) -> SourceMapSummary:
    """Store one build artifact's map.

    The whole file is read into memory, which is what bounds it: the service
    refuses anything past its limit, and Starlette has already spilled a large
    upload to a temporary file by this point rather than holding it all.
    """
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    body = await source_map.read()
    return await service.store_source_map(
        db,
        store,
        org_id=identity.org_id,
        project_id=project.id,
        release=release,
        minified_path=minified_path,
        debug_id=debug_id,
        body=body,
    )


@router.post("/symbolicate", summary="Turn minified stack frames back into source")
async def symbolicate(
    identity: CurrentIdentity,
    db: DbSession,
    store: ObjectStoreDep,
    project_id: ProjectId,
    request: SymbolicateRequest,
) -> SymbolicateResponse:
    project = await require_project(db, org_id=identity.org_id, project_id=project_id)
    return await service.symbolicate(
        db,
        store,
        org_id=identity.org_id,
        project_id=project.id,
        frames=request.frames,
        debug_id=request.debug_id,
        release=request.release,
    )
