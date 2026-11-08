"""`/slos`: reliability targets (F19). Reads are on the read surface (ADR-017); writes are
`CONFIGURE_SLOS`, an owner's, an admin's or a member's (foundation section 5, D20).

Postgres only, and no telemetry repository named here, so these routes stay on the
default rate-limit tier and answer while ClickHouse is down (D59, AC40). `project_id`
is proved to be the caller's, and a token's, before anything is read; `/slos/{id}`
compares the row it loads, as `get_incident` does (AC33).

The month routes (FR27, FR27b, FR30) read stored days alone. `/months` and
`/months.csv` are declared above `/{slo_id}`, whose wildcard would otherwise take them
as an id.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response, status

from nexovue_api.auth.dependencies import CurrentReader
from nexovue_api.auth.policy import MayConfigureSlos
from nexovue_api.deps import DbSession
from nexovue_api.slos import exclusions, months, service
from nexovue_api.slos.schemas import (
    SloChange,
    SloCreate,
    SloExclusion,
    SloExclusionCreate,
    SloExclusions,
    SloHistory,
    SloLastMonth,
    SloList,
    SloMonths,
    SloRestore,
    SloView,
)

router = APIRouter(prefix="/slos", tags=["slos"])

ProjectId = Annotated[uuid.UUID, Query(description="Project whose targets these are")]


@router.get("", summary="A project's reliability targets, those needing attention first")
async def list_slos(reader: CurrentReader, db: DbSession, project_id: ProjectId) -> SloList:
    return await service.list_targets(
        db, reader=reader, project_id=project_id, now=datetime.now(UTC)
    )


# A link to a response that says `attachment`, as `traces/router.py` explains.
CSV_CONTENT_TYPE: Final = "text/csv; charset=utf-8"


@router.get("/months", summary="One month across a project's targets: N of M met")
async def last_month(
    reader: CurrentReader,
    db: DbSession,
    project_id: ProjectId,
    month: Annotated[
        date | None, Query(description="Any day of the month; last month if unset")
    ] = None,
) -> SloLastMonth:
    return await months.last_month(
        db, reader=reader, project_id=project_id, month=month, now=datetime.now(UTC)
    )


@router.get("/months.csv", response_class=Response, summary="Complete months as a file")
async def months_csv(
    reader: CurrentReader,
    db: DbSession,
    project_id: ProjectId,
    months_back: Annotated[
        int, Query(alias="months", ge=1, le=months.CSV_MONTHS_MOST, description="Complete months")
    ] = 1,
    slo_id: Annotated[uuid.UUID | None, Query(description="One target's months only")] = None,
) -> Response:
    sheet, slug = await months.months_csv(
        db,
        reader=reader,
        project_id=project_id,
        months=months_back,
        target_id=slo_id,
        now=datetime.now(UTC),
    )
    return Response(
        content=sheet,
        media_type=CSV_CONTENT_TYPE,
        headers={"content-disposition": f'attachment; filename="{slug}-reliability-months.csv"'},
    )


@router.get("/{slo_id}", summary="One reliability target and where it stands")
async def get_slo(reader: CurrentReader, db: DbSession, slo_id: uuid.UUID) -> SloView:
    return await service.read_target(db, reader=reader, target_id=slo_id, now=datetime.now(UTC))


@router.get("/{slo_id}/months", summary="A target's months, newest first, up to 13")
async def get_slo_months(reader: CurrentReader, db: DbSession, slo_id: uuid.UUID) -> SloMonths:
    return await months.target_months(db, reader=reader, target_id=slo_id, now=datetime.now(UTC))


@router.get("/{slo_id}/history", summary="Every change made to a target")
async def get_slo_history(reader: CurrentReader, db: DbSession, slo_id: uuid.UUID) -> SloHistory:
    return await months.history(db, reader=reader, target_id=slo_id)


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


@router.get("/{slo_id}/exclusions", summary="Time left out of a target, with its reasons")
async def list_slo_exclusions(
    reader: CurrentReader, db: DbSession, slo_id: uuid.UUID
) -> SloExclusions:
    return await exclusions.list_exclusions(db, reader=reader, target_id=slo_id)


@router.post(
    "/{slo_id}/exclusions",
    status_code=status.HTTP_201_CREATED,
    summary="Leave planned work out of a target, with a reason",
)
async def create_slo_exclusion(
    identity: MayConfigureSlos, db: DbSession, slo_id: uuid.UUID, request: SloExclusionCreate
) -> SloExclusion:
    return await exclusions.create(
        db, identity=identity, target_id=slo_id, request=request, now=datetime.now(UTC)
    )


@router.delete(
    "/{slo_id}/exclusions/{exclusion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an exclusion; it stays visible as removed",
)
async def remove_slo_exclusion(
    identity: MayConfigureSlos, db: DbSession, slo_id: uuid.UUID, exclusion_id: uuid.UUID
) -> Response:
    await exclusions.remove(
        db, identity=identity, target_id=slo_id, exclusion_id=exclusion_id, now=datetime.now(UTC)
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
