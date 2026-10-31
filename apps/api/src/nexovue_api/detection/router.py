"""Detection's state (F6, FR25). Both routes are on the read surface (ADR-017).

`project_id` is a value the caller chose, so it is proved to belong to the
caller's organization, and to a token's scope, before anything is read. Both
read Postgres only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query

from nexovue_api.auth.dependencies import CurrentReader
from nexovue_api.data.postgres.models import SERVICE_NAME_LENGTH
from nexovue_api.deps import DbSession
from nexovue_api.detection import service
from nexovue_api.detection.schemas import ProjectDetectionStatus, ServiceDetection
from nexovue_api.projects.service import require_readable_project
from nexovue_api.window import resolve_window

router = APIRouter(tags=["detection"])

ServiceName = Annotated[str, Path(min_length=1, max_length=SERVICE_NAME_LENGTH)]
ProjectId = Annotated[uuid.UUID, Query(description="Project the service belongs to")]
FromTime = Annotated[datetime | None, Query(alias="from")]
ToTime = Annotated[datetime | None, Query(alias="to")]


@router.get(
    "/projects/{project_id}/detection/status",
    summary="Whether detection runs, and how many services it has learned and judges",
)
async def detection_status(
    reader: CurrentReader, db: DbSession, project_id: uuid.UUID
) -> ProjectDetectionStatus:
    project = await require_readable_project(db, reader=reader, project_id=project_id)
    return await service.project_status(
        db, org_id=reader.org_id, project_id=project.id, now=datetime.now(UTC)
    )


@router.get(
    "/services/{service_name}/detection",
    summary="One service's detection state, and its findings over a range; at most 200",
)
async def service_detection(
    reader: CurrentReader,
    db: DbSession,
    service_name: ServiceName,
    project_id: ProjectId,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> ServiceDetection:
    project = await require_readable_project(db, reader=reader, project_id=project_id)
    start, end = resolve_window(from_time, to_time)
    return await service.service_detection(
        db,
        org_id=reader.org_id,
        project_id=project.id,
        service_name=service_name,
        start=start,
        end=end,
        now=datetime.now(UTC),
    )
