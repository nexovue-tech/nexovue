"""Check file rows. Every statement binds its organization."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import CheckFile


def add_file(session: AsyncSession, row: CheckFile) -> None:
    session.add(row)


async def files_in_project(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    file_ids: Sequence[uuid.UUID],
) -> Sequence[CheckFile]:
    if not file_ids:
        return []
    statement = select(CheckFile).where(
        CheckFile.org_id == org_id,
        CheckFile.project_id == project_id,
        CheckFile.id.in_(file_ids),
    )
    return (await session.scalars(statement)).all()


async def files_of_check(
    session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID
) -> Sequence[CheckFile]:
    statement = select(CheckFile).where(CheckFile.org_id == org_id, CheckFile.check_id == check_id)
    return (await session.scalars(statement)).all()


async def count_files_of_check(
    session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID
) -> int:
    statement = (
        select(func.count())
        .select_from(CheckFile)
        .where(CheckFile.org_id == org_id, CheckFile.check_id == check_id)
    )
    return int(await session.scalar(statement) or 0)


async def attach(
    session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID, file_ids: Sequence[uuid.UUID]
) -> None:
    """The listed files belong to the check; any it held before and no longer
    lists are detached, and the sweep takes them an hour on."""
    await session.execute(
        update(CheckFile)
        .where(
            CheckFile.org_id == org_id,
            CheckFile.check_id == check_id,
            CheckFile.id.not_in(file_ids) if file_ids else CheckFile.id.is_not(None),
        )
        .values(check_id=None, detached_at=func.now())
    )
    if file_ids:
        await session.execute(
            update(CheckFile)
            .where(CheckFile.org_id == org_id, CheckFile.id.in_(file_ids))
            .values(check_id=check_id, detached_at=None)
        )


async def detach_files_of_check(
    session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID
) -> None:
    await session.execute(
        update(CheckFile)
        .where(CheckFile.org_id == org_id, CheckFile.check_id == check_id)
        .values(check_id=None, detached_at=func.now())
    )
