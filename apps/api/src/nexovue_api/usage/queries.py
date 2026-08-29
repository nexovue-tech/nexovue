"""Usage reads. Every one takes an org_id — there is no unscoped variant.

The tenant predicate is in the statement rather than checked after the rows come
back, so another organization's counters return nothing and "not yours" is
indistinguishable from "does not exist".

`usage_daily` is keyed `(org_id, project_id, day)`, which is also its primary
key, so a range over days inside one organization is an index scan.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import Plan, UsageDaily


async def daily_usage(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    from_day: date,
    to_day: date,
    project_id: uuid.UUID | None = None,
) -> Sequence[UsageDaily]:
    """Counters for one organization over an inclusive range of days."""
    statement = (
        select(UsageDaily)
        .where(
            UsageDaily.org_id == org_id,
            UsageDaily.day >= from_day,
            UsageDaily.day <= to_day,
        )
        .order_by(UsageDaily.day, UsageDaily.project_id)
    )
    if project_id is not None:
        statement = statement.where(UsageDaily.project_id == project_id)
    return (await session.scalars(statement)).all()


async def find_plan(session: AsyncSession, *, slug: str) -> Plan | None:
    """What a tier grants. Plans are the same for everyone, so not tenant-scoped."""
    plan: Plan | None = await session.scalar(select(Plan).where(Plan.slug == slug))
    return plan
