"""`/runbooks`: a project's runbooks (F4 FR32; design, *Routes*). HTTP only.

Postgres only, so on the default rate-limit tier. Every signed-in person reads; a support
view sees the list and no bodies; only `MANAGE_KNOWLEDGE` writes (D48). Not on the read
surface: no API token reads runbooks (FR37, D46). Absent where AI is not offered (AC92).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayManageKnowledge
from nexovue_api.deps import AppSettings, DbSession
from nexovue_api.runbooks import service
from nexovue_api.runbooks.schemas import (
    NewRunbook,
    RunbookFields,
    RunbookList,
    RunbookUpload,
    RunbookView,
    UploadResult,
)

router = APIRouter(prefix="/runbooks", tags=["runbooks"])


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("", summary="A project's runbooks: title, service, author and age")
async def list_runbooks(
    identity: CurrentIdentity,
    db: DbSession,
    settings: AppSettings,
    project_id: Annotated[uuid.UUID, Query(description="The project")],
) -> RunbookList:
    service.require_offered(settings.nexovue_deployment)
    return await service.list_runbooks(db, identity=identity, project_id=project_id)


@router.get("/{runbook_id}", summary="One runbook; a support view gets no body")
async def read_runbook(
    identity: CurrentIdentity, db: DbSession, settings: AppSettings, runbook_id: uuid.UUID
) -> RunbookView:
    service.require_offered(settings.nexovue_deployment)
    return await service.read_runbook(db, identity=identity, runbook_id=runbook_id)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Add a runbook")
async def create_runbook(
    identity: MayManageKnowledge, db: DbSession, settings: AppSettings, request: NewRunbook
) -> RunbookView:
    service.require_offered(settings.nexovue_deployment)
    return await service.create_runbook(db, identity=identity, request=request, now=_now())


@router.post("/upload", summary="Add Markdown files as runbooks, to one or more projects")
async def upload_runbooks(
    identity: MayManageKnowledge, db: DbSession, settings: AppSettings, request: RunbookUpload
) -> UploadResult:
    service.require_offered(settings.nexovue_deployment)
    return await service.upload(db, identity=identity, request=request, now=_now())


@router.put("/{runbook_id}", summary="Change a runbook")
async def update_runbook(
    identity: MayManageKnowledge,
    db: DbSession,
    settings: AppSettings,
    runbook_id: uuid.UUID,
    request: RunbookFields,
) -> RunbookView:
    service.require_offered(settings.nexovue_deployment)
    return await service.update_runbook(
        db, identity=identity, runbook_id=runbook_id, request=request, now=_now()
    )


@router.delete("/{runbook_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a runbook")
async def delete_runbook(
    identity: MayManageKnowledge, db: DbSession, settings: AppSettings, runbook_id: uuid.UUID
) -> None:
    service.require_offered(settings.nexovue_deployment)
    await service.delete_runbook(db, identity=identity, runbook_id=runbook_id)
