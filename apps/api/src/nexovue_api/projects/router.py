"""The project screen: the selector's list, and P1-76's writes.

Every route takes the project id from the path and the organization from the
session, never the other way round — the service proves the two belong together
before anything is read or changed.

The two configuration documents are `PUT` rather than `PATCH`: the worker parses
the whole document, so a partial write would leave the API and the enforcer with
different ideas of what "the config" is.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from nexovue_api.auth.dependencies import CurrentIdentity, CurrentReader
from nexovue_api.auth.policy import MayConfigureProjects
from nexovue_api.deps import DbSession
from nexovue_api.projects import service
from nexovue_api.projects.schemas import (
    MaskingConfigResponse,
    MaskingConfigWrite,
    ProjectCreateRequest,
    ProjectDeleted,
    ProjectDetail,
    ProjectListResponse,
    ProjectUpdateRequest,
    SamplingConfigResponse,
    SamplingConfigWrite,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", summary="Every project in the signed-in organization")
async def list_projects(reader: CurrentReader, db: DbSession) -> ProjectListResponse:
    """On the read surface (F36, FR17): it is how a token — and an assistant
    reading through one — learns which project ids the rest of the surface takes.
    A scoped token sees only the project it may read (BR11)."""
    listed = await service.list_for_organization(db, reader.org_id)
    if reader.project_scope is None:
        return listed
    return ProjectListResponse(
        projects=[project for project in listed.projects if project.id == reader.project_scope]
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a project")
async def create_project(
    request: ProjectCreateRequest, identity: MayConfigureProjects, db: DbSession
) -> ProjectDetail:
    return await service.create(
        db, org_id=identity.org_id, actor_user_id=identity.user.id, request=request
    )


@router.get("/{project_id}", summary="One project, with its configuration")
async def get_project(
    project_id: uuid.UUID, identity: CurrentIdentity, db: DbSession
) -> ProjectDetail:
    return await service.get(db, org_id=identity.org_id, project_id=project_id)


@router.patch("/{project_id}", summary="Rename a project or change its identifier")
async def update_project(
    project_id: uuid.UUID,
    request: ProjectUpdateRequest,
    identity: MayConfigureProjects,
    db: DbSession,
) -> ProjectDetail:
    return await service.update(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        project_id=project_id,
        request=request,
    )


@router.delete("/{project_id}", summary="Close a project and withdraw its credentials")
async def delete_project(
    project_id: uuid.UUID, identity: MayConfigureProjects, db: DbSession
) -> ProjectDeleted:
    """200 with a body, not 204: what this did to the project's telemetry is the
    thing a customer most needs told, and 204 has nowhere to say it."""
    return await service.delete(
        db, org_id=identity.org_id, actor_user_id=identity.user.id, project_id=project_id
    )


@router.put("/{project_id}/sampling-config", summary="Replace this project's sampling document")
async def replace_sampling_config(
    project_id: uuid.UUID,
    request: SamplingConfigWrite,
    identity: MayConfigureProjects,
    db: DbSession,
) -> SamplingConfigResponse:
    return await service.write_sampling_config(
        db, org_id=identity.org_id, project_id=project_id, document=request.config
    )


@router.put("/{project_id}/masking-config", summary="Replace this project's masking document")
async def replace_masking_config(
    project_id: uuid.UUID,
    request: MaskingConfigWrite,
    identity: MayConfigureProjects,
    db: DbSession,
) -> MaskingConfigResponse:
    return await service.write_masking_config(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        project_id=project_id,
        document=request.config,
        confirm_disabling=request.confirm_disabling,
    )
