"""`/slos`: reliability targets (F19). Reads are on the read surface (ADR-017); writes are
`CONFIGURE_SLOS`, an owner's, an admin's or a member's (foundation section 5, D20).

Postgres only, and no telemetry repository named here, so these routes stay on the
default rate-limit tier and answer while ClickHouse is down (D59, AC40). `project_id`
is proved to be the caller's, and a token's, before anything is read; `/slos/{id}`
compares the row it loads, as `get_incident` does (AC33).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from nexovue_api.auth.dependencies import CurrentReader
from nexovue_api.auth.policy import MayConfigureSlos
from nexovue_api.deps import DbSession
from nexovue_api.slos import service
from nexovue_api.slos.schemas import SloChange, SloCreate, SloList, SloRestore, SloView

router = APIRouter(prefix="/slos", tags=["slos"])

ProjectId = Annotated[uuid.UUID, Query(description="Project whose targets these are")]


@router.get("", summary="A project's reliability targets, those needing attention first")
async def list_slos(reader: CurrentReader, db: DbSession, project_id: ProjectId) -> SloList:
    return await service.list_targets(
        db, reader=reader, project_id=project_id, now=datetime.now(UTC)
    )


@router.get("/{slo_id}", summary="One reliability target and where it stands")
async def get_slo(reader: CurrentReader, db: DbSession, slo_id: uuid.UUID) -> SloView:
    return await service.read_target(db, reader=reader, target_id=slo_id, now=datetime.now(UTC))


@router.post("", status_code=status.HTTP_201_CREATED, summary="Set a reliability target")
async def create_slo(
    identity: MayConfigureSlos, db: DbSession, project_id: ProjectId, request: SloCreate
) -> SloView:
    return await service.create(
        db, identity=identity, project_id=project_id, request=request, now=datetime.now(UTC)
    )


@router.patch("/{slo_id}", summary="Change a reliability target")
async def change_slo(
    identity: MayConfigureSlos, db: DbSession, slo_id: uuid.UUID, request: SloChange
) -> SloView:
    return await service.change(
        db, identity=identity, target_id=slo_id, request=request, now=datetime.now(UTC)
    )


@router.delete(
    "/{slo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a reliability target; it can be restored for 30 days",
)
async def delete_slo(identity: MayConfigureSlos, db: DbSession, slo_id: uuid.UUID) -> Response:
    await service.delete(db, identity=identity, target_id=slo_id, now=datetime.now(UTC))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{slo_id}/restore", summary="Restore a deleted reliability target")
async def restore_slo(
    identity: MayConfigureSlos, db: DbSession, slo_id: uuid.UUID, request: SloRestore
) -> SloView:
    return await service.restore(
        db, identity=identity, target_id=slo_id, request=request, now=datetime.now(UTC)
    )
