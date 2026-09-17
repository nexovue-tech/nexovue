"""Secret and variable reads and writes. Every one binds its organization."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import CheckSecret, CheckVariable


async def list_secrets(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[CheckSecret]:
    statement = (
        select(CheckSecret)
        .where(CheckSecret.org_id == org_id, CheckSecret.project_id == project_id)
        .order_by(CheckSecret.name)
    )
    return (await session.scalars(statement)).all()


async def find_secret(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, name: str
) -> CheckSecret | None:
    statement = select(CheckSecret).where(
        CheckSecret.org_id == org_id,
        CheckSecret.project_id == project_id,
        CheckSecret.name == name,
    )
    secret: CheckSecret | None = await session.scalar(statement)
    return secret


async def delete_secret(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, name: str
) -> uuid.UUID | None:
    statement = (
        delete(CheckSecret)
        .where(
            CheckSecret.org_id == org_id,
            CheckSecret.project_id == project_id,
            CheckSecret.name == name,
        )
        .returning(CheckSecret.id)
    )
    deleted: uuid.UUID | None = await session.scalar(statement)
    return deleted


async def list_variables(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[CheckVariable]:
    statement = (
        select(CheckVariable)
        .where(CheckVariable.org_id == org_id, CheckVariable.project_id == project_id)
        .order_by(CheckVariable.name)
    )
    return (await session.scalars(statement)).all()


async def find_variable(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, name: str
) -> CheckVariable | None:
    statement = select(CheckVariable).where(
        CheckVariable.org_id == org_id,
        CheckVariable.project_id == project_id,
        CheckVariable.name == name,
    )
    variable: CheckVariable | None = await session.scalar(statement)
    return variable


async def delete_variable(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, name: str
) -> bool:
    statement = (
        delete(CheckVariable)
        .where(
            CheckVariable.org_id == org_id,
            CheckVariable.project_id == project_id,
            CheckVariable.name == name,
        )
        .returning(CheckVariable.id)
    )
    return await session.scalar(statement) is not None


def add(session: AsyncSession, row: CheckSecret | CheckVariable) -> None:
    session.add(row)
