"""HTTP for the LLM-calls screen. No SQL and no arithmetic."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from nexovue_api.auth.dependencies import CurrentReader, TenantRepository
from nexovue_api.deps import DbSession
from nexovue_api.llm_observability import service
from nexovue_api.llm_observability.schemas import LLMCallListResponse
from nexovue_api.projects.service import require_readable_project
from nexovue_api.window import resolve_window

router = APIRouter(prefix="/llm", tags=["llm-observability"])

ProjectId = Annotated[uuid.UUID, Query(description="Project whose model calls to list")]
FromTime = Annotated[datetime | None, Query(alias="from")]
ToTime = Annotated[datetime | None, Query(alias="to")]
ServiceMatch = Annotated[
    str | None, Query(alias="service", max_length=255, description="Only this service's calls")
]
ModelMatch = Annotated[
    str | None,
    Query(alias="model", max_length=255, description="Only groups naming this model"),
]


@router.get("/calls", summary="Every model call this project reported, busiest first")
async def list_llm_calls(
    reader: CurrentReader,
    db: DbSession,
    repository: TenantRepository,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
    service_match: ServiceMatch = None,
    model_match: ModelMatch = None,
) -> LLMCallListResponse:
    project = await require_readable_project(db, reader=reader, project_id=project_id)
    window_start, window_end = resolve_window(from_time, to_time)
    return await service.list_llm_calls(
        repository,
        project_id=str(project.id),
        from_time=window_start,
        to_time=window_end,
        service_match=service_match,
        model_match=model_match,
    )
