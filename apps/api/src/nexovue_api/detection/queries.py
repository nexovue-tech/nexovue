"""The Postgres reads behind detection's status. Every statement binds `org_id`.

Detection's own tables are written by the scheduler (`detection/project_job.py`);
nothing here reads ClickHouse.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    DetectionProjectRun,
    DetectionState,
    Finding,
    ServiceDetectionSettings,
)

# How many findings one service timeline returns, oldest first.
MARKER_PAGE: Final = 200


async def project_run(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> DetectionProjectRun | None:
    statement = select(DetectionProjectRun).where(
        DetectionProjectRun.org_id == org_id, DetectionProjectRun.project_id == project_id
    )
    run: DetectionProjectRun | None = await session.scalar(statement)
    return run


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


async def service_known(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, service_name: str
) -> bool:
    """Whether detection has seen the service: it has a state row from its first minute."""
    return (
        await service_state(
            session, org_id=org_id, project_id=project_id, service_name=service_name
        )
        is not None
    )


async def quieten(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service_name: str,
    values: dict[str, object],
) -> ServiceDetectionSettings:
    """Set a service's mute or snooze, creating its settings row on first use. An upsert
    on the row's key, `org_id` first, so two people at once cannot collide."""
    statement = (
        insert(ServiceDetectionSettings)
        .values(org_id=org_id, project_id=project_id, service_name=service_name, **values)
        .on_conflict_do_update(index_elements=["org_id", "project_id", "service_name"], set_=values)
        .returning(ServiceDetectionSettings)
        .execution_options(populate_existing=True)
    )
    found: ServiceDetectionSettings = (await session.scalars(statement)).one()
    return found
