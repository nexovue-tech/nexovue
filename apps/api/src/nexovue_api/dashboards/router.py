"""`/dashboards`: saved dashboards and the Postgres half of a dashboard page (F20).

Reads take `CurrentIdentity` and writes `MayEditDashboards`: an API token reaches no
dashboard route, because a one-project token reading organization-wide definitions
would learn other projects' service names (D9). Postgres only, and no telemetry
repository is named here, so `/dashboards` stays on the default rate tier and the
context answers while ClickHouse is down (design section 2, D24).

`/templates` is declared above `/{dashboard_id}`, whose wildcard would otherwise take
it as an id (AC24).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from nexovue_platform.deployment import SELF_HOSTED
from starlette.requests import HTTPConnection

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayEditDashboards, MayPurgeDashboards
from nexovue_api.dashboards import service
from nexovue_api.dashboards.definition import NAME_MAX_LENGTH, DashboardDocument
from nexovue_api.dashboards.limits import DashboardLimits
from nexovue_api.dashboards.ranges import RangeId
from nexovue_api.dashboards.schemas import (
    DashboardContext,
    DashboardListResponse,
    DashboardResponse,
    DashboardTemplatesResponse,
    PurgeDashboard,
    ReplaceDashboard,
    RestoreDashboard,
    VersionResponse,
    VersionsResponse,
)
from nexovue_api.deps import AppSettings, DbSession

router = APIRouter(prefix="/dashboards", tags=["dashboards"])

ProjectId = Annotated[uuid.UUID, Query(description="The project the dashboard is shown against")]
Range = Annotated[RangeId | None, Query(alias="range", description="A live window, ending now")]
FromTime = Annotated[datetime | None, Query(alias="from")]
ToTime = Annotated[datetime | None, Query(alias="to")]


def get_dashboard_limits(connection: HTTPConnection) -> DashboardLimits:
    """The install's limits, checked once when the app was built (`main.create_app`)."""
    limits: DashboardLimits = connection.app.state.dashboard_limits
    return limits


Limits = Annotated[DashboardLimits, Depends(get_dashboard_limits)]


@router.get("", summary="The organization's dashboards, from stored columns only")
async def list_dashboards(
    identity: CurrentIdentity,
    db: DbSession,
    service_name: Annotated[
        str | None,
        Query(
            alias="service",
            min_length=1,
            max_length=NAME_MAX_LENGTH,
            description="Only dashboards that can show this service, naming ones first",
        ),
    ] = None,
    deleted: Annotated[
        bool, Query(description="The deleted dashboards that can still be restored")
    ] = False,
) -> DashboardListResponse:
    return await service.list_dashboards(
        db, org_id=identity.org_id, service=service_name, deleted=deleted, now=datetime.now(UTC)
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="Save a new dashboard")
async def create_dashboard(
    identity: MayEditDashboards, db: DbSession, limits: Limits, document: DashboardDocument
) -> DashboardResponse:
    return await service.create_dashboard(
        db, identity=identity, document=document, now=datetime.now(UTC), limits=limits
    )


@router.get("/templates", summary="The starting points a dashboard opens from, as drafts")
async def list_templates(identity: CurrentIdentity) -> DashboardTemplatesResponse:
    return service.templates()


@router.get("/{dashboard_id}", summary="One dashboard, with its definition and version")
async def get_dashboard(
    identity: CurrentIdentity, db: DbSession, dashboard_id: uuid.UUID
) -> DashboardResponse:
    return await service.get_dashboard(db, org_id=identity.org_id, dashboard_id=dashboard_id)


@router.put("/{dashboard_id}", summary="Save over a dashboard, from the version it was read at")
async def replace_dashboard(
    identity: MayEditDashboards,
    db: DbSession,
    limits: Limits,
    dashboard_id: uuid.UUID,
    body: ReplaceDashboard,
) -> DashboardResponse:
    return await service.replace_dashboard(
        db,
        identity=identity,
        dashboard_id=dashboard_id,
        body=body,
        now=datetime.now(UTC),
        limits=limits,
    )


@router.delete(
    "/{dashboard_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a dashboard, restorable for 30 days",
)
async def delete_dashboard(
    identity: MayEditDashboards, db: DbSession, dashboard_id: uuid.UUID
) -> None:
    await service.delete_dashboard(
        db, identity=identity, dashboard_id=dashboard_id, now=datetime.now(UTC)
    )


@router.post(
    "/{dashboard_id}/restore", summary="Undo a delete within 30 days, under a new title if asked"
)
async def restore_dashboard(
    identity: MayEditDashboards,
    db: DbSession,
    dashboard_id: uuid.UUID,
    body: RestoreDashboard | None = None,
) -> DashboardResponse:
    return await service.restore_dashboard(
        db,
        identity=identity,
        dashboard_id=dashboard_id,
        new_title=body.title if body else None,
        now=datetime.now(UTC),
    )


@router.post(
    "/{dashboard_id}/purge",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a dashboard, or one version of it, at once",
)
async def purge_dashboard(
    identity: MayPurgeDashboards,
    db: DbSession,
    dashboard_id: uuid.UUID,
    body: PurgeDashboard | None = None,
) -> None:
    await service.purge_dashboard(
        db,
        identity=identity,
        dashboard_id=dashboard_id,
        version=body.version if body else None,
    )


@router.get("/{dashboard_id}/versions", summary="The versions a dashboard keeps, newest first")
async def list_versions(
    identity: CurrentIdentity, db: DbSession, dashboard_id: uuid.UUID
) -> VersionsResponse:
    return await service.list_versions(db, org_id=identity.org_id, dashboard_id=dashboard_id)


@router.get("/{dashboard_id}/versions/{version}", summary="One kept version, in full")
async def get_version(
    identity: CurrentIdentity,
    db: DbSession,
    dashboard_id: uuid.UUID,
    version: Annotated[int, Path(ge=1)],
) -> VersionResponse:
    return await service.get_version(
        db, org_id=identity.org_id, dashboard_id=dashboard_id, version=version
    )


@router.get("/{dashboard_id}/context", summary="The Postgres half of a dashboard page")
async def dashboard_context(
    identity: CurrentIdentity,
    db: DbSession,
    settings: AppSettings,
    dashboard_id: uuid.UUID,
    project_id: ProjectId,
    range_id: Range = None,
    from_time: FromTime = None,
    to_time: ToTime = None,
) -> DashboardContext:
    return await service.context(
        db,
        identity=identity,
        dashboard_id=dashboard_id,
        project_id=project_id,
        range_id=range_id,
        from_time=from_time,
        to_time=to_time,
        self_hosted=settings.nexovue_deployment == SELF_HOSTED,
        now=datetime.now(UTC),
    )
