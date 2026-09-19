"""The audit log, for the people who answer for the organization (F26, FR36)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from nexovue_api.audit import read
from nexovue_api.audit.schemas import AuditPage, EventKind
from nexovue_api.auth.policy import MayReadAudit
from nexovue_api.deps import DbSession

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", summary="Who did what in this organization, newest first")
async def read_audit_log(
    identity: MayReadAudit,
    db: DbSession,
    actor: uuid.UUID | None = None,
    kind: EventKind | None = None,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=read.MAX_PAGE)] = read.DEFAULT_PAGE,
) -> AuditPage:
    return await read.read_page(
        db,
        org_id=identity.org_id,
        actor_id=actor,
        kind=kind,
        since=since,
        until=until,
        cursor=cursor,
        limit=limit,
    )
