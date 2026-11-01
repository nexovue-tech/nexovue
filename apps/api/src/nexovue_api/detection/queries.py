"""The Postgres reads behind detection's status. Every statement binds `org_id`,
except `detection_sweep`'s, whose one row describes nexovue's pipeline and names no
tenant.

Detection's own tables are written by the scheduler (`detection/project_job.py`,
`detection/coordinator.py`); nothing here reads ClickHouse.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import (
    AuditLogEntry,
    DetectionProjectRun,
    DetectionState,
    DetectionSweep,
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


async def detection_sweep(session: AsyncSession) -> DetectionSweep | None:
    """When the install's detection sweep last ran, and the newest minute it settled."""
    sweep: DetectionSweep | None = await session.scalar(select(DetectionSweep))
    return sweep


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


@dataclass(frozen=True, slots=True)
class SnoozeRecord:
    """One snooze set or ended, as the audit log keeps it: when, until when (None when
    it ended one), and whether it counts toward the members' allowance."""

    at: datetime
    until: datetime | None
    member: bool


async def snooze_records(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service_name: str,
    since: datetime,
) -> list[SnoozeRecord]:
    """Every snooze of one service set or ended since `since`, oldest first."""
    statement = (
        select(AuditLogEntry.at, AuditLogEntry.detail)
        .where(
            AuditLogEntry.org_id == org_id,
            AuditLogEntry.action == audit.SERVICE_SNOOZED,
            AuditLogEntry.target == audit.project_target(project_id),
            AuditLogEntry.detail["service"].astext == service_name,
            AuditLogEntry.at >= since,
        )
        .order_by(AuditLogEntry.at, AuditLogEntry.id)
    )
    return [
        SnoozeRecord(
            at=at,
            until=None if detail.get("until") is None else datetime.fromisoformat(detail["until"]),
            member=bool(detail.get(audit.MEMBER_SNOOZE_KEY)),
        )
        for at, detail in (await session.execute(statement)).all()
    ]
