"""The Postgres reads behind detection's status. Every statement binds `org_id`.

Detection's own tables are written by the scheduler (`detection/project_job.py`);
nothing here reads ClickHouse.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    DetectionProjectRun,
    DetectionState,
    Finding,
    ServiceDetectionSettings,
    ServiceLastSeen,
)

# How many findings one service timeline returns, oldest first.
MARKER_PAGE: Final = 200


async def last_completed_run(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> datetime | None:
    statement = select(DetectionProjectRun.last_completed_at).where(
        DetectionProjectRun.org_id == org_id, DetectionProjectRun.project_id == project_id
    )
    completed: datetime | None = await session.scalar(statement)
    return completed


async def is_judged_by_scheduler(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, seen_since: datetime
) -> bool:
    """Whether the coordinator queues this project's job each minute: it had traffic
    since `seen_since`, or has a finding open (scheduler `detection/projects.py`)."""
    traffic = exists().where(
        ServiceLastSeen.org_id == org_id,
        ServiceLastSeen.project_id == project_id,
        ServiceLastSeen.last_seen_at >= seen_since,
    )
    open_finding = exists().where(
        Finding.org_id == org_id, Finding.project_id == project_id, Finding.state == "open"
    )
    judged: bool = await session.scalar(select(or_(traffic, open_finding))) or False
    return judged


async def service_states(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> list[DetectionState]:
    statement = select(DetectionState).where(
        DetectionState.org_id == org_id, DetectionState.project_id == project_id
    )
    return list(await session.scalars(statement))


async def service_state(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, service_name: str
) -> DetectionState | None:
    statement = select(DetectionState).where(
        DetectionState.org_id == org_id,
        DetectionState.project_id == project_id,
        DetectionState.service_name == service_name,
    )
    found: DetectionState | None = await session.scalar(statement)
    return found


async def muted_service_count(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, now: datetime
) -> int:
    statement = select(func.count()).where(
        ServiceDetectionSettings.org_id == org_id,
        ServiceDetectionSettings.project_id == project_id,
        ServiceDetectionSettings.muted_until > now,
    )
    counted: int = await session.scalar(statement) or 0
    return counted


async def service_settings(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, service_name: str
) -> ServiceDetectionSettings | None:
    statement = select(ServiceDetectionSettings).where(
        ServiceDetectionSettings.org_id == org_id,
        ServiceDetectionSettings.project_id == project_id,
        ServiceDetectionSettings.service_name == service_name,
    )
    found: ServiceDetectionSettings | None = await session.scalar(statement)
    return found


async def open_finding_count(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> int:
    statement = select(func.count()).where(
        Finding.org_id == org_id, Finding.project_id == project_id, Finding.state == "open"
    )
    counted: int = await session.scalar(statement) or 0
    return counted


async def service_findings(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service_name: str,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[Finding]:
    """The service's findings that overlap `[start, end]`, oldest first."""
    statement = (
        select(Finding)
        .where(
            Finding.org_id == org_id,
            Finding.project_id == project_id,
            Finding.scope == "service",
            Finding.service_name == service_name,
            Finding.started_at <= end,
            or_(Finding.closed_at.is_(None), Finding.closed_at >= start),
        )
        .order_by(Finding.started_at, Finding.id)
        .limit(limit)
    )
    return list(await session.scalars(statement))
