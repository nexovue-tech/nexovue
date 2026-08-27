"""The organization's projects, for the top-bar selector."""

from __future__ import annotations

from fastapi import APIRouter

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.deps import DbSession
from nexovue_api.projects import service
from nexovue_api.projects.schemas import ProjectListResponse

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", summary="Every project in the signed-in organization")
async def list_projects(identity: CurrentIdentity, db: DbSession) -> ProjectListResponse:
    return await service.list_for_organization(db, identity.org_id)
