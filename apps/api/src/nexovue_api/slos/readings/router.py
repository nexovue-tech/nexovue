"""`/slo-readings`: F19's ClickHouse reads, on the telemetry tier (D59).

`suggestion` is a person's, of any role, a viewer included (AC32), and never a token's
(AC33): `CurrentIdentity` refuses one with 403. It is the form's only ClickHouse read,
so when it fails the form says so and still saves (AC106).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from nexovue_platform import slo

from nexovue_api.auth.dependencies import CurrentIdentity, TenantRepository
from nexovue_api.deps import AppSettings, DbSession
from nexovue_api.slos.readings import service
from nexovue_api.slos.readings.schemas import SloSuggestion
from nexovue_api.slos.schemas import CreatableKind

router = APIRouter(prefix="/slo-readings", tags=["slos"])


@router.get("/suggestion", summary="A target suggested from four weeks, with its arithmetic")
async def read_suggestion(
    identity: CurrentIdentity,
    db: DbSession,
    settings: AppSettings,
    repository: TenantRepository,
    project_id: Annotated[uuid.UUID, Query(description="Project the source is in")],
    kind: Annotated[
        CreatableKind, Query(description="The kind of target")
    ] = slo.SERVICE_AVAILABILITY,
    service_name: Annotated[str | None, Query(alias="service", max_length=255)] = None,
    endpoint_name: Annotated[str | None, Query(alias="endpoint", max_length=255)] = None,
    check_id: Annotated[uuid.UUID | None, Query()] = None,
) -> SloSuggestion:
    return await service.suggestion(
        db,
        repository,
        identity=identity,
        project_id=project_id,
        kind=kind,
        service_name=service_name,
        endpoint_name=endpoint_name,
        check_id=check_id,
        # A check naming no location runs from every one the deployment has.
        probe_locations=len(settings.probe_locations),
        now=datetime.now(UTC),
    )
