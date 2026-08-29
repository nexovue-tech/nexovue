"""Check reads and writes. Every one takes an org_id — there is no unscoped variant.

The tenant predicate is in the statement rather than checked after the row comes
back. Another organization's check then returns nothing, and "not yours" is
indistinguishable from "does not exist" — a `SELECT` followed by an `if
row.org_id != org_id` would answer the second question out loud.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import Check


async def list_checks(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[Check]:
    """This project's checks, newest last so the list is stable as it grows."""
    statement = (
        select(Check)
        .where(Check.org_id == org_id, Check.project_id == project_id)
        .order_by(Check.created_at, Check.id)
    )
    return (await session.scalars(statement)).all()


async def find_check(
    session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID
) -> Check | None:
    """One check, or None. Another tenant's check is None, not a row."""
    statement = select(Check).where(Check.id == check_id, Check.org_id == org_id)
    check: Check | None = await session.scalar(statement)
    return check


async def count_checks(session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> int:
    """How many checks this project already has, for the per-project ceiling."""
    statement = (
        select(func.count())
        .select_from(Check)
        .where(Check.org_id == org_id, Check.project_id == project_id)
    )
    return int(await session.scalar(statement) or 0)


def add_check(session: AsyncSession, check: Check) -> None:
    """Stage a new check. The caller's transaction commits it."""
    session.add(check)


async def delete_check(session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID) -> bool:
    """Remove one check, reporting whether there was one to remove.

    Scoped by organization in the statement, so a delete aimed at another
    tenant's check removes nothing rather than needing a check afterwards.

    Checks are deleted outright rather than soft-deleted. A withdrawn API key is
    kept because an audit needs to see what was withdrawn; a check holds no
    secret and its measurements live in ClickHouse under their own retention, so
    there is nothing here worth keeping a tombstone for.
    """
    # RETURNING makes this one statement that also answers "was there a row",
    # so there is no separate SELECT for another transaction to race.
    statement = (
        delete(Check).where(Check.id == check_id, Check.org_id == org_id).returning(Check.id)
    )
    return await session.scalar(statement) is not None
