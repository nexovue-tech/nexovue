"""`/releases` (F28): the list, one release, and its comparison.

Reads are on the read surface (ADR-017): a person or a read token, scoped to its
project. The whole prefix is on the telemetry rate-limit tier (design, *Rate limits*),
though only the comparison reads ClickHouse; the list and the release read Postgres
alone (AC81). Fixed paths under `/releases` are declared before `/{release_id}`, whose
wildcard would otherwise take them as an id.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query

from nexovue_api.auth.dependencies import CurrentReader, TenantRepository
from nexovue_api.deps import DbSession
from nexovue_api.releases import service
from nexovue_api.releases.schemas import (
    ComparisonView,
    CoverageList,
    MarkerList,
    ReleaseList,
    ReleaseView,
)
from nexovue_api.window import clamp_limit, resolve_window

router = APIRouter(prefix="/releases", tags=["releases"])

ProjectId = Annotated[uuid.UUID, Query(description="Project whose releases these are")]


@router.get("", summary="Releases with a period in the window, newest first")
async def list_releases(
    reader: CurrentReader,
    db: DbSession,
    project_id: ProjectId,
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
    service_name: Annotated[str | None, Query(alias="service")] = None,
    version: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query(description="From the previous page")] = None,
    limit: Annotated[int | None, Query(ge=1, le=service.MAX_LIMIT)] = None,
) -> ReleaseList:
    start, end = resolve_window(from_time, to_time)
    return await service.list_releases(
        db,
        reader=reader,
        project_id=project_id,
        start=start,
        end=end,
        service=service_name,
        version=version,
        cursor=cursor,
        limit=clamp_limit(limit, default=service.DEFAULT_LIMIT, maximum=service.MAX_LIMIT),
        now=datetime.now(UTC),
    )


@router.get("/markers", summary="Release marks for a chart's window, newest first")
async def list_markers(
    reader: CurrentReader,
    db: DbSession,
    project_id: ProjectId,
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
    service_name: Annotated[str | None, Query(alias="service")] = None,
) -> MarkerList:
    start, end = resolve_window(from_time, to_time)
    return await service.list_markers(
        db, reader=reader, project_id=project_id, start=start, end=end, service=service_name
    )


@router.get("/coverage", summary="What deploy information each service gives, as of a moment")
async def list_coverage(
    reader: CurrentReader,
    db: DbSession,
    project_id: ProjectId,
    service_name: Annotated[str | None, Query(alias="service")] = None,
    at: Annotated[datetime | None, Query(description="Now if unset")] = None,
) -> CoverageList:
    return await service.list_coverage(
        db,
        reader=reader,
        project_id=project_id,
        service=service_name,
        at=at or datetime.now(UTC),
    )


@router.get("/{release_id}", summary="One release, its periods and what replaced it")
async def get_release(reader: CurrentReader, db: DbSession, release_id: uuid.UUID) -> ReleaseView:
    return await service.read_release(
        db, reader=reader, release_id=release_id, now=datetime.now(UTC)
    )


@router.get(
    "/{release_id}/comparison",
    summary="A period against its previous version, by version where traffic allows",
)
async def get_comparison(
    reader: CurrentReader,
    db: DbSession,
    repository: TenantRepository,
    release_id: uuid.UUID,
    period: Annotated[uuid.UUID | None, Query(description="The latest period if unset")] = None,
    window: Annotated[
        int | None, Query(description="Hours from the period's start: 1, 6, 24")
    ] = None,
) -> ComparisonView:
    return await service.compare(
        db,
        repository,
        reader=reader,
        release_id=release_id,
        period_id=period,
        window_hours=service.window_hours_of(window),
        now=datetime.now(UTC),
    )
