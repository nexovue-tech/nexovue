"""Maintenance window reads and writes. Every statement binds its organization."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import MaintenanceWindow, MaintenanceWindowCheck


async def list_windows(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[MaintenanceWindow]:
    statement = (
        select(MaintenanceWindow)
        .where(MaintenanceWindow.org_id == org_id, MaintenanceWindow.project_id == project_id)
        .order_by(MaintenanceWindow.starts_at, MaintenanceWindow.id)
    )
    return (await session.scalars(statement)).all()


async def find_window(
    session: AsyncSession, *, org_id: uuid.UUID, window_id: uuid.UUID
) -> MaintenanceWindow | None:
    statement = select(MaintenanceWindow).where(
        MaintenanceWindow.id == window_id, MaintenanceWindow.org_id == org_id
    )
    window: MaintenanceWindow | None = await session.scalar(statement)
    return window


async def covered_checks(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[MaintenanceWindowCheck]:
    statement = select(MaintenanceWindowCheck).where(
        MaintenanceWindowCheck.org_id == org_id, MaintenanceWindowCheck.project_id == project_id
    )
    return (await session.scalars(statement)).all()


def add(session: AsyncSession, row: MaintenanceWindow | MaintenanceWindowCheck) -> None:
    session.add(row)


async def replace_covered_checks(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    window_id: uuid.UUID,
    check_ids: Sequence[uuid.UUID],
) -> None:
    await session.execute(
        delete(MaintenanceWindowCheck).where(
            MaintenanceWindowCheck.org_id == org_id, MaintenanceWindowCheck.window_id == window_id
        )
    )
    for check_id in check_ids:
        session.add(
            MaintenanceWindowCheck(
                org_id=org_id, project_id=project_id, window_id=window_id, check_id=check_id
            )
        )


async def delete_window(
    session: AsyncSession, *, org_id: uuid.UUID, window_id: uuid.UUID
) -> uuid.UUID | None:
    statement = (
        delete(MaintenanceWindow)
        .where(MaintenanceWindow.id == window_id, MaintenanceWindow.org_id == org_id)
        .returning(MaintenanceWindow.id)
    )
    deleted: uuid.UUID | None = await session.scalar(statement)
    return deleted
