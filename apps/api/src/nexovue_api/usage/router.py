"""What this organization has ingested, per day.

Read from `usage_daily` in Postgres, not from ClickHouse: the counters are the
control plane's own record of what was accepted, and re-counting telemetry to
answer a billing question would report what survived sampling and retention
rather than what arrived.

The organization comes from the session. `project_id` is optional and, being a
value the caller chose, is proved to belong to that organization before it
narrows anything.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.deps import DbSession
from nexovue_api.projects.service import require_project
from nexovue_api.usage import service
from nexovue_api.usage.schemas import UsageResponse

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", summary="Ingestion counters for this organization, by day")
async def read_usage(
    identity: CurrentIdentity,
    db: DbSession,
    project_id: Annotated[uuid.UUID | None, Query(description="Narrow to one project")] = None,
    from_day: Annotated[date | None, Query(alias="from")] = None,
    to_day: Annotated[date | None, Query(alias="to")] = None,
) -> UsageResponse:
    if project_id is not None:
        await require_project(db, org_id=identity.org_id, project_id=project_id)
    start, end = service.resolve_day_range(from_day, to_day)
    return await service.usage_for_organization(
        db,
        organization=identity.organization,
        from_day=start,
        to_day=end,
        project_id=project_id,
    )
