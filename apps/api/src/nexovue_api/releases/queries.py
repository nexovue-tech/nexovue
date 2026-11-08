"""F28's reads for `/releases`: Postgres for the record, ClickHouse for the comparison.

Every Postgres statement binds `org_id`. The one ClickHouse statement is a module
constant binding the tenant and the project; the repository refuses one that does not.
The list and the release read Postgres alone (AC81: no ClickHouse per row).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import Release, ReleasePeriod

# One service's requests, failures and latency buckets per minute and version, in
# [from, to): what the comparison splits (design 4, `VERSION_MINUTES_SQL`). No version
# and the worker's fold are read too, so the method can see a service with no version.
VERSION_MINUTES_SQL: Final = """
SELECT toUnixTimestamp(minute) AS minute_epoch,
       version,
       sum(request_count) AS requests,
       sum(error_count) AS errors,
       sumForEachMerge(latency_buckets) AS buckets
FROM service_versions_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND service_name = {service_name:String}
  AND minute >= {from_minute:DateTime}
  AND minute < {to_minute:DateTime}
GROUP BY minute, version
ORDER BY minute, version
"""


async def version_minutes(
    repository: TenantScopedRepository,
    *,
    project_id: uuid.UUID,
    service_name: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    return await repository.query(
        VERSION_MINUTES_SQL,
        {
            "project_id": str(project_id),
            "service_name": service_name,
            "from_minute": start,
            "to_minute": end,
        },
    )


# ---------------------------------------------------------------- the record


def _latest_period(org_id: uuid.UUID) -> Any:
    """Each release's newest period, as a subquery keyed by release."""
    ranked = (
        select(
            ReleasePeriod.release_id,
            ReleasePeriod.ended_at,
            func.row_number()
            .over(
                partition_by=ReleasePeriod.release_id,
                order_by=(ReleasePeriod.started_at.desc(), ReleasePeriod.id.desc()),
            )
            .label("rank"),
        )
        .where(ReleasePeriod.org_id == org_id)
        .subquery()
    )
    return select(ranked.c.release_id, ranked.c.ended_at).where(ranked.c.rank == 1).subquery()


def _in_window(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    start: datetime,
    end: datetime,
    service: str | None,
    version: str | None,
) -> Select[Any]:
    """Each release with a period overlapping the window, by its newest such period
    (design, *What the window means*): one serving since yesterday is in today's."""
    overlapping = (
        select(
            ReleasePeriod.id.label("period_id"),
            ReleasePeriod.release_id,
            ReleasePeriod.started_at,
            ReleasePeriod.ended_at,
            ReleasePeriod.start_basis,
            ReleasePeriod.previous_version,
            ReleasePeriod.already_running,
            ReleasePeriod.is_return,
            func.row_number()
            .over(
                partition_by=ReleasePeriod.release_id,
                order_by=(ReleasePeriod.started_at.desc(), ReleasePeriod.id.desc()),
            )
            .label("rank"),
        )
        .where(
            ReleasePeriod.org_id == org_id,
            ReleasePeriod.project_id == project_id,
            ReleasePeriod.started_at < end,
            or_(ReleasePeriod.ended_at.is_(None), ReleasePeriod.ended_at > start),
        )
        .subquery()
    )
    latest = _latest_period(org_id)
    statement = (
        select(
            Release,
            overlapping.c.period_id,
            overlapping.c.started_at,
            overlapping.c.ended_at,
            overlapping.c.start_basis,
            overlapping.c.previous_version,
            overlapping.c.already_running,
            overlapping.c.is_return,
            latest.c.ended_at.label("latest_ended_at"),
        )
        .join(overlapping, and_(overlapping.c.release_id == Release.id, overlapping.c.rank == 1))
        .join(latest, latest.c.release_id == Release.id)
        .where(Release.org_id == org_id, Release.project_id == project_id)
    )
    if service is not None:
        statement = statement.where(Release.service_name == service)
    if version is not None:
        statement = statement.where(Release.version == version)
    return statement


async def releases_in_window(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    service: str | None,
    version: str | None,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> Sequence[Any]:
    """A page, newest period first, keyed by (start, period id)."""
    statement = _in_window(
        org_id, project_id, start=start, end=end, service=service, version=version
    ).subquery()
    page = select(statement)
    if after is not None:
        started, period_id = after
        page = page.where(
            or_(
                statement.c.started_at < started,
                and_(statement.c.started_at == started, statement.c.period_id < period_id),
            )
        )
    page = page.order_by(statement.c.started_at.desc(), statement.c.period_id.desc()).limit(limit)
    return (await session.execute(page)).all()


async def window_totals(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    service: str | None,
    version: str | None,
) -> tuple[int, int]:
    """How many releases, and on how many services, the window holds."""
    statement = _in_window(
        org_id, project_id, start=start, end=end, service=service, version=version
    ).subquery()
    row = (
        await session.execute(
            select(func.count(), func.count(func.distinct(statement.c.service_name)))
        )
    ).one()
    return int(row[0]), int(row[1])


async def latest_release(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service: str | None,
    version: str | None,
) -> Any | None:
    """The newest period in the project, of the filtered service and version if named,
    for the "nothing in this window" sentence; None when the project has none."""
    statement = (
        select(Release, ReleasePeriod.started_at)
        .join(
            ReleasePeriod,
            and_(ReleasePeriod.org_id == Release.org_id, ReleasePeriod.release_id == Release.id),
        )
        .where(Release.org_id == org_id, Release.project_id == project_id)
    )
    if service is not None:
        statement = statement.where(Release.service_name == service)
    if version is not None:
        statement = statement.where(Release.version == version)
    statement = statement.order_by(ReleasePeriod.started_at.desc(), ReleasePeriod.id.desc()).limit(
        1
    )
    return (await session.execute(statement)).one_or_none()


async def has_releases(session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> bool:
    found = await session.scalar(
        select(Release.id)
        .where(Release.org_id == org_id, Release.project_id == project_id)
        .limit(1)
    )
    return found is not None


async def find_release(
    session: AsyncSession, *, org_id: uuid.UUID, release_id: uuid.UUID
) -> Release | None:
    found: Release | None = await session.scalar(
        select(Release).where(Release.org_id == org_id, Release.id == release_id)
    )
    return found


async def periods_of(
    session: AsyncSession, *, org_id: uuid.UUID, release_id: uuid.UUID
) -> list[ReleasePeriod]:
    return list(
        await session.scalars(
            select(ReleasePeriod)
            .where(ReleasePeriod.org_id == org_id, ReleasePeriod.release_id == release_id)
            .order_by(ReleasePeriod.started_at, ReleasePeriod.id)
        )
    )


async def replaced_by(
    session: AsyncSession, *, org_id: uuid.UUID, release: Release, ended_at: datetime
) -> str | None:
    """The version that held the service when this period ended: the newest other
    period on the service started by then."""
    other = aliased(Release)
    found: str | None = await session.scalar(
        select(other.version)
        .join(
            ReleasePeriod,
            and_(ReleasePeriod.org_id == other.org_id, ReleasePeriod.release_id == other.id),
        )
        .where(
            other.org_id == org_id,
            other.project_id == release.project_id,
            other.service_name == release.service_name,
            other.id != release.id,
            ReleasePeriod.started_at <= ended_at,
        )
        .order_by(ReleasePeriod.started_at.desc(), ReleasePeriod.id.desc())
        .limit(1)
    )
    return found


async def previous_started_at(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service_name: str,
    version: str,
    before: datetime,
) -> datetime | None:
    """When the previous version's latest period before `before` started, for its warm-up."""
    found: datetime | None = await session.scalar(
        select(ReleasePeriod.started_at)
        .join(
            Release,
            and_(Release.org_id == ReleasePeriod.org_id, Release.id == ReleasePeriod.release_id),
        )
        .where(
            Release.org_id == org_id,
            Release.project_id == project_id,
            Release.service_name == service_name,
            Release.version == version,
            ReleasePeriod.started_at <= before,
        )
        .order_by(ReleasePeriod.started_at.desc())
        .limit(1)
    )
    return found
