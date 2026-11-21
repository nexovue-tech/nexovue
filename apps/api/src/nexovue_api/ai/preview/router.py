"""`/ai/preview`: what would be sent, read before an owner agrees (F3 FR25, F4 FR24). HTTP only.

Its own router, on the telemetry tier, because chat's preview reads the project's newest log
line from ClickHouse (F4 T2.12); the rest of `/ai` reads Postgres only and keeps the default
tier, so the settings screen answers while ClickHouse is down.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from nexovue_api.ai import service
from nexovue_api.ai.schemas import Preview
from nexovue_api.assistant.preview import chat_preview
from nexovue_api.assistant.schemas import ChatPreview
from nexovue_api.auth.dependencies import TenantRepository
from nexovue_api.auth.policy import MayManageAI
from nexovue_api.deps import DbSession
from nexovue_api.errors.exceptions import InvalidRequestError

router = APIRouter(prefix="/ai/preview", tags=["ai"])


@router.get(
    "",
    summary="What would be sent: the newest finding's exact request, or chat's kinds and samples",
)
async def read_preview(
    identity: MayManageAI,
    db: DbSession,
    repository: TenantRepository,
    purpose: Annotated[
        Literal["explain", "chat"], Query(description="Whose preview: wording's or chat's")
    ] = "explain",
    project_id: Annotated[
        uuid.UUID | None, Query(description="Chat's preview: the project its samples come from")
    ] = None,
) -> Preview | ChatPreview:
    if purpose == "explain":
        return await service.preview(db, org_id=identity.org_id)
    if project_id is None:
        raise InvalidRequestError("Chat's preview needs a project_id.")
    # F4 (FR24, AC2): generated from chat's catalog, with samples as they would be sent.
    return await chat_preview(
        db, repository, org_id=identity.org_id, project_id=project_id, now=datetime.now(UTC)
    )
