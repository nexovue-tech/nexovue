"""`/dashboard-panels`: a dashboard's panels, saved or a draft, in one charged batch (F20).

On the telemetry tier by its own prefix (`TELEMETRY_PATHS`), and kept out of
`dashboards/router.py` because the coverage test judges a router file as a whole
(design section 2, D41). It names `TenantRepository`, so with no ClickHouse client yet
it answers 503 before the handler runs; the page's Postgres half, `/dashboards/{id}/
context`, still answers (D24). `CurrentIdentity`: an API token reads no dashboard (D9).
A draft's read is `MayEditDashboards`, since only an editor has a draft, and a support
view is refused it as every write is (AC8).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Query, Request
from starlette.requests import HTTPConnection

from nexovue_api.auth.dependencies import CurrentIdentity, TenantRepository
from nexovue_api.auth.policy import MayEditDashboards
from nexovue_api.dashboardpanels import service
from nexovue_api.dashboardpanels.schemas import PanelReadResponse, PreviewBody
from nexovue_api.dashboardpanels.service import BatchRequest, DraftRequest, PanelReads
from nexovue_api.dashboards.definition import MAX_PANELS, NAME_MAX_LENGTH
from nexovue_api.dashboards.ranges import RangeId
from nexovue_api.deps import DbSession
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.ratelimit.charge import arrival_of, scan_charge_for

router = APIRouter(prefix="/dashboard-panels", tags=["dashboards"])

PANEL_IDS_SEPARATOR: Final = ","


def get_panel_reads(connection: HTTPConnection) -> PanelReads:
    """The process's caps and kept answers, built with the app (`main.create_app`)."""
    reads: PanelReads = connection.app.state.dashboard_reads
    return reads


PanelReadsDep = Annotated[PanelReads, Depends(get_panel_reads)]


def _panel_ids(panels: str | None) -> frozenset[str] | None:
    if panels is None:
        return None
    ids = frozenset(part for part in panels.split(PANEL_IDS_SEPARATOR) if part)
    if len(ids) > MAX_PANELS:
        raise InvalidRequestError(f"Name at most {MAX_PANELS} panels.")
    return ids


@router.get("/read", summary="A saved dashboard's panels, each with its own state")
async def read_panels(
    request: Request,
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    reads: PanelReadsDep,
    dashboard_id: Annotated[uuid.UUID, Query(description="The dashboard")],
    project_id: Annotated[uuid.UUID, Query(description="The project it is shown against")],
    range_id: Annotated[
        RangeId | None, Query(alias="range", description="A live window, ending now")
    ] = None,
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
    service_name: Annotated[
        str | None,
        Query(alias="service", min_length=1, max_length=NAME_MAX_LENGTH, description="Picked"),
    ] = None,
    panels: Annotated[
        str | None, Query(description="Only these panel ids, comma-separated")
    ] = None,
) -> PanelReadResponse:
    return await service.read_panels(
        db,
        identity=identity,
        repository=repository,
        reads=reads,
        charge=scan_charge_for(request.scope, getattr(request.app.state, "rate_limiter", None)),
        asked=BatchRequest(
            dashboard_id=dashboard_id,
            project_id=project_id,
            range_id=range_id,
            from_time=from_time,
            to_time=to_time,
            service=service_name,
            panel_ids=_panel_ids(panels),
        ),
        arrived_at=arrival_of(request.scope),
        disconnected=request.is_disconnected,
        correlation_id=request.scope.get("state", {}).get("correlation_id"),
        now=datetime.now(UTC),
    )


@router.post("/preview", summary="A draft's panels, read and charged as a saved read is")
async def preview_panels(
    request: Request,
    identity: MayEditDashboards,
    db: DbSession,
    repository: TenantRepository,
    reads: PanelReadsDep,
    body: PreviewBody,
) -> PanelReadResponse:
    return await service.preview_panels(
        db,
        identity=identity,
        repository=repository,
        reads=reads,
        charge=scan_charge_for(request.scope, getattr(request.app.state, "rate_limiter", None)),
        asked=DraftRequest(
            project_id=body.project_id,
            range_id=body.range_id,
            from_time=body.from_time,
            to_time=body.to_time,
            service=body.service,
            panel_ids=None if body.panels is None else frozenset(body.panels),
            definition=body.definition,
        ),
        arrived_at=arrival_of(request.scope),
        disconnected=request.is_disconnected,
        correlation_id=request.scope.get("state", {}).get("correlation_id"),
        now=datetime.now(UTC),
    )
