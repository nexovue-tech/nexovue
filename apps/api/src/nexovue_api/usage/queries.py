"""Usage reads. Every one takes an org_id — there is no unscoped variant.

The tenant predicate is in the statement rather than checked after the rows come
back, so another organization's counters return nothing and "not yours" is
indistinguishable from "does not exist".

`usage_daily` is keyed `(org_id, project_id, day)`, which is also its primary
key, so a range over days inside one organization is an index scan.
`usage_daily_services` extends that key with the service name, so counting a
project's services for a day is a scan of one contiguous stretch of it.

The two exceptions carry no org_id and cannot: `plans` and `plan_prices` are the
same for everyone, and `usage_ingest_offsets` records how far the meter has read
each Kafka partition, which is a fact about the meter rather than about a
tenant.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import NamedTuple

from sqlalchemy import Row, distinct, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    Plan,
    PlanPrice,
    UsageDaily,
    UsageDailyService,
    UsageIngestOffset,
)


class MeteringFrontier(NamedTuple):
    """How far the usage meter has counted, in receive time.

    `counted_through` is the earliest point any partition has reached, because a
    day is only finished when every partition has passed it. `unknown_partitions`
    counts the partitions nothing has metered since the receive time was first
    recorded — while one exists, no day can be called finished.
    """

    counted_through: datetime | None
    unknown_partitions: int


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


async def daily_service_counts(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    from_day: date,
    to_day: date,
    project_id: uuid.UUID | None = None,
) -> Sequence[Row[tuple[uuid.UUID, date, int]]]:
    """The billing unit: how many distinct services each project-day carried.

    `count(*)` over the set, not a stored number. There is nothing to keep in
    step because there is nothing denormalized.
    """
    statement = (
        select(
            UsageDailyService.project_id,
            UsageDailyService.day,
            func.count().label("services"),
        )
        .where(
            UsageDailyService.org_id == org_id,
            UsageDailyService.day >= from_day,
            UsageDailyService.day <= to_day,
        )
        .group_by(UsageDailyService.project_id, UsageDailyService.day)
    )
    if project_id is not None:
        statement = statement.where(UsageDailyService.project_id == project_id)
    return (await session.execute(statement)).all()


async def services_in_range(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    from_day: date,
    to_day: date,
    project_id: uuid.UUID | None = None,
) -> int:
    """Distinct services over a whole range — a union, never a sum of the days.

    A service running every day of a month is one service that month, so adding
    the daily counts would bill it thirty times. Distinct by project *and* name,
    because two projects each running a `checkout` are running two of them.
    """
    statement = select(
        func.count(distinct(tuple_(UsageDailyService.project_id, UsageDailyService.service_name)))
    ).where(
        UsageDailyService.org_id == org_id,
        UsageDailyService.day >= from_day,
        UsageDailyService.day <= to_day,
    )
    if project_id is not None:
        statement = statement.where(UsageDailyService.project_id == project_id)
    return await session.scalar(statement) or 0


class IngestedBytes(NamedTuple):
    """What the meter counted over a range, and the browser door's part of it.

    `browser` is a part of `total`, never beside it, so the two must not be
    added together. It is what the browser door's own share is spent against
    (ADR 0097).
    """

    total: int
    browser: int


async def bytes_in_range(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    from_day: date,
    to_day: date,
) -> IngestedBytes:
    """Ingested bytes over a range, for the whole organization.

    The allowance is pooled across the organization, so this is deliberately
    not narrowable to a project: one project's bytes are not one project's
    allowance.

    Both figures come from one statement over one set of rows, so the part can
    never be read from a different range than the whole.
    """
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(UsageDaily.bytes), 0),
                func.coalesce(func.sum(UsageDaily.browser_bytes), 0),
            ).where(
                UsageDaily.org_id == org_id,
                UsageDaily.day >= from_day,
                UsageDaily.day <= to_day,
            )
        )
    ).one()
    return IngestedBytes(total=row[0] or 0, browser=row[1] or 0)


async def service_counts_by_project(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    previous_from: date,
    recent_from: date,
    to_day: date,
) -> Sequence[Row[tuple[uuid.UUID, int, int]]]:
    """Each project's distinct service count in two adjacent windows.

    One scan with two filtered aggregates rather than two round trips, because
    the two windows are contiguous and the rows are read the same way for both.
    Distinct within each window: a service reporting every day is one service,
    here for the same reason it is one on the bill.
    """
    recent = func.count(distinct(UsageDailyService.service_name)).filter(
        UsageDailyService.day >= recent_from
    )
    previous = func.count(distinct(UsageDailyService.service_name)).filter(
        UsageDailyService.day < recent_from
    )
    statement = (
        select(
            UsageDailyService.project_id,
            recent.label("recent"),
            previous.label("previous"),
        )
        .where(
            UsageDailyService.org_id == org_id,
            UsageDailyService.day >= previous_from,
            UsageDailyService.day <= to_day,
        )
        .group_by(UsageDailyService.project_id)
    )
    return (await session.execute(statement)).all()


async def service_names(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_ids: Sequence[uuid.UUID],
    from_day: date,
    to_day: date,
    limit_per_project: int,
) -> Sequence[Row[tuple[uuid.UUID, str]]]:
    """The distinct service names a few projects carried, capped per project.

    The cap is per project rather than over the whole result, so one project in
    the middle of a cardinality explosion cannot crowd every other project's
    names out of the answer — which is exactly the case this is read for.
    """
    if not project_ids:
        return []

    distinct_names = (
        select(UsageDailyService.project_id, UsageDailyService.service_name)
        .where(
            UsageDailyService.org_id == org_id,
            UsageDailyService.project_id.in_(project_ids),
            UsageDailyService.day >= from_day,
            UsageDailyService.day <= to_day,
        )
        .distinct()
        .subquery()
    )
    ranked = select(
        distinct_names.c.project_id,
        distinct_names.c.service_name,
        func.row_number()
        .over(
            partition_by=distinct_names.c.project_id,
            order_by=distinct_names.c.service_name,
        )
        .label("rank"),
    ).subquery()

    statement = select(ranked.c.project_id, ranked.c.service_name).where(
        ranked.c.rank <= limit_per_project
    )
    return (await session.execute(statement)).all()


async def find_plan(session: AsyncSession, *, slug: str) -> Plan | None:
    """What a tier grants. Plans are the same for everyone, so not tenant-scoped."""
    plan: Plan | None = await session.scalar(select(Plan).where(Plan.slug == slug))
    return plan


async def find_effective_price(
    session: AsyncSession, *, plan_slug: str, on_day: date
) -> PlanPrice | None:
    """The price in force on a day: the latest row that had already started.

    Rows are appended and never edited, so this answers the same thing next year
    as it does today — which is the whole reason the price is not a column on
    `plans`. None means no price has been set for the tier, which is the state
    the table ships in.
    """
    price: PlanPrice | None = await session.scalar(
        select(PlanPrice)
        .where(PlanPrice.plan_slug == plan_slug, PlanPrice.effective_from <= on_day)
        .order_by(PlanPrice.effective_from.desc())
        .limit(1)
    )
    return price


async def metering_frontier(session: AsyncSession) -> MeteringFrontier:
    """How far the meter has counted, so a day in progress can be labelled one."""
    row = (
        await session.execute(
            select(
                func.min(UsageIngestOffset.counted_through_received_at),
                func.count().filter(UsageIngestOffset.counted_through_received_at.is_(None)),
            )
        )
    ).one()
    return MeteringFrontier(counted_through=row[0], unknown_partitions=row[1])
