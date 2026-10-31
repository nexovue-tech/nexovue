"""Detection's state (F6, FR25) and the user's controls over it (FR18, FR22, FR24).

The reads are on the read surface (ADR-017). `project_id` is a value the caller
chose, so it is proved to belong to the caller's organization, and to a token's
scope, before anything is read. All of them read Postgres only.

Who may change what (FR23, D11, D25): a snooze is every working role's, the 3 a.m.
lever; a mute, the sensitivity and the time zone decide whether detection is heard
at all, and are an admin's.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query

from nexovue_api.auth.dependencies import CurrentReader
from nexovue_api.auth.policy import MayConfigureDetection, MayOperateAlerts
from nexovue_api.data.postgres.models import SERVICE_NAME_LENGTH
from nexovue_api.deps import DbSession
from nexovue_api.detection import service
from nexovue_api.detection.schemas import (
    DetectionSettings,
    DetectionSettingsWrite,
    ProjectDetectionStatus,
    QuietServiceRequest,
    ServiceControls,
    ServiceDetection,
)
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


@router.post(
    "/services/{service_name}/snooze",
    summary="Hear nothing from a service for up to 4 hours, or end its snooze",
)
async def snooze_service(
    identity: MayOperateAlerts,
    db: DbSession,
    service_name: ServiceName,
    request: QuietServiceRequest,
) -> ServiceControls:
    return await service.snooze_service(
        db,
        actor=identity.user,
        project_id=request.project_id,
        service_name=service_name,
        until=request.until,
        now=datetime.now(UTC),
    )


@router.put(
    "/services/{service_name}/mute", summary="Mute a service for up to 30 days, or unmute it"
)
async def mute_service(
    identity: MayConfigureDetection,
    db: DbSession,
    service_name: ServiceName,
    request: QuietServiceRequest,
) -> ServiceControls:
    return await service.mute_service(
        db,
        actor=identity.user,
        project_id=request.project_id,
        service_name=service_name,
        until=request.until,
        now=datetime.now(UTC),
    )


@router.get("/projects/{project_id}/detection", summary="A project's sensitivity and time zone")
async def detection_settings(
    reader: CurrentReader, db: DbSession, project_id: uuid.UUID
) -> DetectionSettings:
    project = await require_readable_project(db, reader=reader, project_id=project_id)
    return service.project_settings(project)


@router.put(
    "/projects/{project_id}/detection", summary="Change a project's sensitivity and time zone"
)
async def change_detection_settings(
    identity: MayConfigureDetection,
    db: DbSession,
    project_id: uuid.UUID,
    request: DetectionSettingsWrite,
) -> DetectionSettings:
    return await service.change_project_settings(
        db, actor=identity.user, project_id=project_id, request=request
    )
