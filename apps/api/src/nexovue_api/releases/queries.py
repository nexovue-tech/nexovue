"""F28's reads for `/releases`: Postgres for the record, ClickHouse for the comparison.

Every Postgres statement binds `org_id`. Every ClickHouse statement is a module
constant binding the tenant and the project; the repository refuses one that does not.
The list and the release read Postgres alone (AC81: no ClickHouse per row).

**Stored spans answer two questions only** (BR6, AC34): which errors each version had
(`ERRORS_BY_VERSION_SQL`, built in `errorgroups/queries.py` from the errors list's own
occurrence rows) and which environment labels appear (`ENVIRONMENTS_SQL`). Never a
rate or a latency: sampling keeps every error and about one healthy trace in ten.
`test_release_words.py` holds this module to it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform import releases as releases_platform
from sqlalchemy import Select, and_, case, delete, func, literal, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import aggregate_order_by, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import (
    DeployEvent,
    Finding,
    Organization,
    Plan,
    Project,
    Release,
    ReleasePeriod,
    ServiceLastSeen,
)
from nexovue_api.errorgroups.queries import ERRORS_BY_VERSION_SQL, NANOS_PER_SECOND

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


# The same service and span from `service_stats_1m`, per minute (design 4,
# `SERVICE_TOTALS_SQL`): FR28's check that no version row went missing, the time
# method's numbers, and last week's (FR25).
SERVICE_TOTALS_SQL: Final = """
SELECT toUnixTimestamp(minute) AS minute_epoch,
       sum(request_count) AS requests,
       sum(error_count) AS errors,
       sumForEachMerge(latency_buckets) AS buckets
FROM service_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND service_name = {service_name:String}
  AND minute >= {from_minute:DateTime}
  AND minute < {to_minute:DateTime}
GROUP BY minute
ORDER BY minute
"""


async def service_minutes(
    repository: TenantScopedRepository,
    *,
    project_id: uuid.UUID,
    service_name: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    return await repository.query(
        SERVICE_TOTALS_SQL,
        {
            "project_id": str(project_id),
            "service_name": service_name,
            "from_minute": start,
            "to_minute": end,
        },
    )


# Which environment labels one service's stored spans carry in a window (FR45, D39):
# presence only, so sampling does not matter. The current name first, the older
# `deployment.environment` where a span has only that, as the profile writer reads
# them (`profilewriting/rows.go`). Both clocks are bounded, as the errors list does,
# so the read prunes partitions. Never a rate (BR6).
ENVIRONMENTS_SQL: Final = """
SELECT DISTINCT environment
FROM (
    SELECT if(
               ifNull(resource_attrs.deployment.environment.name.:String, '') != '',
               ifNull(resource_attrs.deployment.environment.name.:String, ''),
               ifNull(resource_attrs.deployment.environment.:String, '')
           ) AS environment
    FROM spans
    WHERE tenant_id = {tenant_id:String}
      AND project_id = {project_id:String}
      AND service_name = {service_name:String}
      AND timestamp >= {from_time:DateTime64(9)}
      AND timestamp < {to_time:DateTime64(9)}
      AND received_at >= {received_from:DateTime64(9)}
      AND received_at < {received_to:DateTime64(9)}
)
WHERE environment != ''
LIMIT {limit:UInt32}
"""
# Two labels are enough to warn.
_ENVIRONMENTS_ENOUGH: Final = 2


async def environments(
    repository: TenantScopedRepository,
    *,
    project_id: uuid.UUID,
    service_name: str,
    start: datetime,
    end: datetime,
    clock_slack: timedelta,
) -> list[str]:
    rows = await repository.query(
        ENVIRONMENTS_SQL,
        {
            "project_id": str(project_id),
            "service_name": service_name,
            "from_time": start,
            "to_time": end,
            "received_from": start - clock_slack,
            "received_to": end + clock_slack,
            "limit": _ENVIRONMENTS_ENOUGH,
        },
    )
    return sorted(str(row["environment"]) for row in rows)


async def errors_by_version(
    repository: TenantScopedRepository,
    *,
    project_id: uuid.UUID,
    this_version: str,
    previous_version: str,
    service_name: str,
    split_at: datetime,
    start: datetime,
    end: datetime,
    clock_slack: timedelta,
    server_door_only: bool,
    limit: int,
) -> list[dict[str, Any]]:
    """Error groups of two versions of one service: this version's from `split_at` to
    `end`, the previous version's from `start` to `split_at` (FR29)."""
    return await repository.query(
        ERRORS_BY_VERSION_SQL,
        {
            "project_id": str(project_id),
            "service_name": service_name,
            "this_version": this_version,
            "previous_version": previous_version,
            "split_nanos": int(split_at.timestamp()) * NANOS_PER_SECOND,
            "from_time": start,
            "to_time": end,
            "received_from": start - clock_slack,
            "received_to": end + clock_slack,
            "browser_door_too": 0 if server_door_only else 1,
            "limit": limit,
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


# ------------------------------------------------------- what a sender reports
#
# F28 design 3, *From the webhook*. Every statement binds `org_id`. The webhook writes
# in the sync's order (service, then version), so the two take row locks alike (AC51).


async def lock_service_periods(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, service_name: str
) -> None:
    """Serialize period writes on one service until commit. A report can end another
    release's period, so two batches on one service would otherwise lock period rows in
    whatever order their items fell."""
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"release-periods:{org_id}:{project_id}:{service_name}", 0)
            )
        )
    )


async def release_count(session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> int:
    found = await session.scalar(
        select(func.count())
        .select_from(Release)
        .where(Release.org_id == org_id, Release.project_id == project_id)
    )
    return int(found or 0)


async def existing_releases(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    keys: Sequence[tuple[str, str]],
) -> set[tuple[str, str]]:
    """Which of these (service, version) pairs already have a release."""
    if not keys:
        return set()
    rows = await session.execute(
        select(Release.service_name, Release.version).where(
            Release.org_id == org_id,
            Release.project_id == project_id,
            tuple_(Release.service_name, Release.version).in_(list(keys)),
        )
    )
    return {(service_name, version) for service_name, version in rows}


async def evict_releases(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    keep: Sequence[tuple[str, str]],
    excess: int,
) -> int:
    """FR56's order, the sync's (`releases/sync.py`): never-seen releases of services the
    project has never heard from, then the least recently active; never one with a
    period in service, nor one this request writes."""
    serving = (
        select(ReleasePeriod.id)
        .where(
            ReleasePeriod.org_id == org_id,
            ReleasePeriod.release_id == Release.id,
            ReleasePeriod.ended_at.is_(None),
        )
        .exists()
    )
    heard = (
        select(ServiceLastSeen.service_name)
        .where(
            ServiceLastSeen.org_id == org_id,
            ServiceLastSeen.project_id == project_id,
            ServiceLastSeen.service_name == Release.service_name,
        )
        .exists()
    )
    candidates = (
        select(Release.id)
        .where(
            Release.org_id == org_id,
            Release.project_id == project_id,
            tuple_(Release.service_name, Release.version).not_in(list(keep)),
            ~serving,
        )
        .order_by(
            and_(Release.first_seen_at.is_(None), ~heard).desc(),
            Release.last_activity_at,
            Release.id,
        )
        .limit(excess)
    )
    result = await session.execute(
        delete(Release)
        .where(Release.org_id == org_id, Release.id.in_(candidates.scalar_subquery()))
        .returning(Release.id)
    )
    return len(result.all())


async def save_reported_release(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service_name: str,
    version: str,
    deployed_at: datetime,
    state: str,
) -> uuid.UUID:
    """File one new report under its release (design 3, step 5). `LEAST` and `GREATEST`
    touch only the reported times, never a period's start (FR42); the last state is the
    latest report's by its claimed time; activity is arrival, never the claim (D20)."""
    statement = insert(Release).values(
        org_id=org_id,
        project_id=project_id,
        service_name=service_name,
        version=version,
        first_reported_at=deployed_at,
        last_reported_at=deployed_at,
        report_count=1,
        last_state=state,
        last_activity_at=func.now(),
    )
    excluded = statement.excluded
    upsert = statement.on_conflict_do_update(
        constraint="uq_releases_project_service_version",
        set_={
            "first_reported_at": func.least(Release.first_reported_at, excluded.first_reported_at),
            "last_reported_at": func.greatest(Release.last_reported_at, excluded.last_reported_at),
            "report_count": Release.report_count + 1,
            "last_state": case(
                (
                    or_(
                        Release.last_reported_at.is_(None),
                        excluded.last_reported_at >= Release.last_reported_at,
                    ),
                    excluded.last_state,
                ),
                else_=Release.last_state,
            ),
            "last_activity_at": func.greatest(Release.last_activity_at, excluded.last_activity_at),
        },
        where=Release.org_id == org_id,
    ).returning(Release.id)
    found = await session.scalar(upsert)
    if not isinstance(found, uuid.UUID):
        raise LookupError(f"no release {service_name!r} {version!r} to file under")
    return found


async def service_sends_versions(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, service_name: str
) -> bool:
    """Whether any version of this service has been counted in traffic: then the sync
    decides its periods, and a report only opens one for a version that has none."""
    found = await session.scalar(
        select(Release.id)
        .where(
            Release.org_id == org_id,
            Release.project_id == project_id,
            Release.service_name == service_name,
            Release.first_seen_at.is_not(None),
        )
        .limit(1)
    )
    return found is not None


async def latest_period(
    session: AsyncSession, *, org_id: uuid.UUID, release_id: uuid.UUID
) -> ReleasePeriod | None:
    found: ReleasePeriod | None = await session.scalar(
        select(ReleasePeriod)
        .where(ReleasePeriod.org_id == org_id, ReleasePeriod.release_id == release_id)
        .order_by(ReleasePeriod.started_at.desc(), ReleasePeriod.id.desc())
        .limit(1)
    )
    return found


@dataclass(frozen=True, slots=True)
class ServiceRelease:
    """One release, and the service whose other periods a report reads and ends."""

    org_id: uuid.UUID
    project_id: uuid.UUID
    service_name: str
    release_id: uuid.UUID


def _other_periods(on: ServiceRelease) -> Any:
    """The service's periods of other releases; each statement names `org_id` itself, so
    the tenancy structure test reads it in the statement."""
    return (
        ReleasePeriod.project_id == on.project_id,
        ReleasePeriod.service_name == on.service_name,
        ReleasePeriod.release_id != on.release_id,
    )


async def latest_other_start(
    session: AsyncSession,
    on: ServiceRelease,
) -> datetime | None:
    found: datetime | None = await session.scalar(
        select(func.max(ReleasePeriod.started_at)).where(
            ReleasePeriod.org_id == on.org_id, *_other_periods(on)
        )
    )
    return found


async def next_other_start(
    session: AsyncSession,
    on: ServiceRelease,
    *,
    after: datetime,
) -> datetime | None:
    """The first other period on the service starting after a time: where a period
    opened from a late-arriving report ends."""
    found: datetime | None = await session.scalar(
        select(func.min(ReleasePeriod.started_at)).where(
            ReleasePeriod.org_id == on.org_id,
            *_other_periods(on),
            ReleasePeriod.started_at > after,
        )
    )
    return found


async def version_before(
    session: AsyncSession,
    on: ServiceRelease,
    *,
    before: datetime,
) -> str | None:
    """The version of the latest other period on the service started before a time:
    what a new period replaced (FR20)."""
    found: str | None = await session.scalar(
        select(Release.version)
        .join(
            ReleasePeriod,
            and_(ReleasePeriod.org_id == Release.org_id, ReleasePeriod.release_id == Release.id),
        )
        .where(
            Release.org_id == on.org_id,
            ReleasePeriod.org_id == on.org_id,
            *_other_periods(on),
            ReleasePeriod.started_at < before,
        )
        .order_by(ReleasePeriod.started_at.desc(), ReleasePeriod.id.desc())
        .limit(1)
    )
    return found


async def end_open_periods_before(
    session: AsyncSession,
    on: ServiceRelease,
    *,
    at: datetime,
) -> None:
    """A newer report on a service sending no version replaces what served before it."""
    await session.execute(
        update(ReleasePeriod)
        .where(
            ReleasePeriod.org_id == on.org_id,
            *_other_periods(on),
            ReleasePeriod.ended_at.is_(None),
            ReleasePeriod.started_at < at,
        )
        .values(ended_at=at)
    )


async def open_reported_period(
    session: AsyncSession,
    on: ServiceRelease,
    *,
    started_at: datetime,
    ended_at: datetime | None,
    previous_version: str | None,
    is_return: bool,
) -> None:
    """A period whose start a sender reported (FR42). A retry, or a second period at one
    start, writes nothing twice."""
    await session.execute(
        insert(ReleasePeriod)
        .values(
            org_id=on.org_id,
            release_id=on.release_id,
            project_id=on.project_id,
            service_name=on.service_name,
            started_at=started_at,
            start_basis=releases_platform.REPORTED,
            ended_at=ended_at,
            previous_version=previous_version,
            already_running=False,
            is_return=is_return,
        )
        .on_conflict_do_nothing()
    )


# ------------------------------------------------------ recalculating, D37, AC61


async def reports_of(
    session: AsyncSession, *, org_id: uuid.UUID, release_id: uuid.UUID
) -> Sequence[Any]:
    """A release's remaining deploys, oldest claim first."""
    return (
        await session.execute(
            select(DeployEvent.deployed_at, DeployEvent.state)
            .where(DeployEvent.org_id == org_id, DeployEvent.release_id == release_id)
            .order_by(DeployEvent.deployed_at, DeployEvent.created_at, DeployEvent.id)
        )
    ).all()


async def reports_on_service(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, service_name: str
) -> Sequence[Any]:
    """A service's remaining filed deploys, oldest claim first, with their release and
    state."""
    return (
        await session.execute(
            select(DeployEvent.release_id, DeployEvent.deployed_at, DeployEvent.state)
            .where(
                DeployEvent.org_id == org_id,
                DeployEvent.project_id == project_id,
                DeployEvent.service_name == service_name,
                DeployEvent.release_id.is_not(None),
            )
            .order_by(DeployEvent.deployed_at, DeployEvent.created_at, DeployEvent.id)
        )
    ).all()


async def set_reported(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    release_id: uuid.UUID,
    first: datetime | None,
    last: datetime | None,
    count: int,
    state: str | None,
) -> None:
    await session.execute(
        update(Release)
        .where(Release.org_id == org_id, Release.id == release_id)
        .values(
            first_reported_at=first, last_reported_at=last, report_count=count, last_state=state
        )
    )


async def delete_unseen_release(
    session: AsyncSession, *, org_id: uuid.UUID, release_id: uuid.UUID
) -> bool:
    """Remove a release nothing supports any more: never seen, and no report left.
    Its periods go with it."""
    result = await session.execute(
        delete(Release)
        .where(
            Release.org_id == org_id,
            Release.id == release_id,
            Release.first_seen_at.is_(None),
        )
        .returning(Release.id)
    )
    return result.first() is not None


async def delete_reported_periods(
    session: AsyncSession, *, org_id: uuid.UUID, release_id: uuid.UUID
) -> None:
    await session.execute(
        delete(ReleasePeriod).where(
            ReleasePeriod.org_id == org_id,
            ReleasePeriod.release_id == release_id,
            ReleasePeriod.start_basis == releases_platform.REPORTED,
        )
    )


async def delete_service_periods(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, service_name: str
) -> None:
    """Every period of a service sending no version: all of them came from reports, and
    are rebuilt from those that remain."""
    await session.execute(
        delete(ReleasePeriod).where(
            ReleasePeriod.org_id == org_id,
            ReleasePeriod.project_id == project_id,
            ReleasePeriod.service_name == service_name,
        )
    )


# -------------------------------------------------------- markers, FR35, D30


async def reported_marks(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    service: str | None,
    limit: int,
) -> Sequence[Any]:
    """Every deploy a sender reported in [start, end], newest first; same-version
    redeploys and failed ones included. No sender text."""
    statement = select(
        DeployEvent.id,
        DeployEvent.release_id,
        DeployEvent.service_name,
        DeployEvent.version,
        DeployEvent.deployed_at,
        DeployEvent.state,
    ).where(
        DeployEvent.org_id == org_id,
        DeployEvent.project_id == project_id,
        DeployEvent.deployed_at >= start,
        DeployEvent.deployed_at <= end,
    )
    if service is not None:
        statement = statement.where(DeployEvent.service_name == service)
    statement = statement.order_by(DeployEvent.deployed_at.desc(), DeployEvent.id.desc())
    return (await session.execute(statement.limit(limit))).all()


async def seen_marks(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    service: str | None,
    limit: int,
) -> Sequence[Any]:
    """Every period counted in traffic that started in [start, end], newest first. A
    reported period's start is its deploy's, already marked; one already running when
    counting began is not a release (FR43)."""
    statement = (
        select(
            ReleasePeriod.id,
            ReleasePeriod.release_id,
            ReleasePeriod.service_name,
            Release.version,
            ReleasePeriod.started_at,
        )
        .join(
            Release,
            and_(Release.org_id == ReleasePeriod.org_id, Release.id == ReleasePeriod.release_id),
        )
        .where(
            ReleasePeriod.org_id == org_id,
            ReleasePeriod.project_id == project_id,
            Release.org_id == org_id,
            ReleasePeriod.started_at >= start,
            ReleasePeriod.started_at <= end,
            ReleasePeriod.start_basis == releases_platform.SEEN,
            ReleasePeriod.already_running.is_(False),
        )
    )
    if service is not None:
        statement = statement.where(ReleasePeriod.service_name == service)
    statement = statement.order_by(ReleasePeriod.started_at.desc(), ReleasePeriod.id.desc())
    return (await session.execute(statement.limit(limit))).all()


# ------------------------------------------------------------ coverage, FR38


async def heard_by_service(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    services: Sequence[str] | None,
) -> dict[str, tuple[datetime | None, datetime | None]]:
    """Each service's earliest report and earliest counted version, from the record."""
    statement = (
        select(
            Release.service_name,
            func.min(Release.first_reported_at),
            func.min(Release.first_seen_at),
        )
        .where(Release.org_id == org_id, Release.project_id == project_id)
        .group_by(Release.service_name)
    )
    if services is not None:
        statement = statement.where(Release.service_name.in_(list(services)))
    return {row[0]: (row[1], row[2]) for row in (await session.execute(statement)).all()}


async def last_reports_before(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    services: Sequence[str] | None,
    at: datetime,
) -> dict[str, datetime]:
    """Each service's last report at or before a moment, by the time it claims."""
    statement = (
        select(DeployEvent.service_name, func.max(DeployEvent.deployed_at))
        .where(
            DeployEvent.org_id == org_id,
            DeployEvent.project_id == project_id,
            DeployEvent.deployed_at <= at,
        )
        .group_by(DeployEvent.service_name)
    )
    if services is not None:
        statement = statement.where(DeployEvent.service_name.in_(list(services)))
    return {row[0]: row[1] for row in (await session.execute(statement)).all()}


async def services_sending(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> list[str]:
    """Every service that has sent telemetry, so one we hear no deploys for is listed."""
    found = await session.scalars(
        select(ServiceLastSeen.service_name).where(
            ServiceLastSeen.org_id == org_id, ServiceLastSeen.project_id == project_id
        )
    )
    return list(found)


# ------------------------------------------- what came before a finding, FR32, FR52

# The services one service called in a window, from the edges rollup (FR52): marking a
# release elsewhere "this service calls it". Presence only.
CALLED_SERVICES_SQL: Final = """
SELECT DISTINCT server_service
FROM service_edges_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND client_service = {service_name:String}
  AND minute >= {from_minute:DateTime}
  AND minute < {to_minute:DateTime}
"""


async def called_services(
    repository: TenantScopedRepository,
    *,
    project_id: uuid.UUID,
    service_name: str,
    start: datetime,
    end: datetime,
) -> set[str]:
    rows = await repository.query(
        CALLED_SERVICES_SQL,
        {
            "project_id": str(project_id),
            "service_name": service_name,
            "from_minute": start,
            "to_minute": end,
        },
    )
    return {str(row["server_service"]) for row in rows}


def _period_facts(org_id: uuid.UUID, project_id: uuid.UUID) -> Select[Any]:
    """A period with what the finding page states of its release."""
    return (
        select(
            ReleasePeriod.id.label("period_id"),
            ReleasePeriod.release_id,
            ReleasePeriod.service_name,
            ReleasePeriod.started_at,
            ReleasePeriod.start_basis,
            ReleasePeriod.is_return,
            ReleasePeriod.already_running,
            Release.version,
            Release.first_seen_at,
            Release.first_reported_at,
            Release.last_state,
        )
        .join(
            Release,
            and_(Release.org_id == ReleasePeriod.org_id, Release.id == ReleasePeriod.release_id),
        )
        .where(
            ReleasePeriod.org_id == org_id,
            Release.org_id == org_id,
            ReleasePeriod.project_id == project_id,
        )
    )


async def period_before(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service_name: str,
    at: datetime,
) -> Any | None:
    """The service's last period starting at or before `at`, at any age (FR32). A
    period starting in the finding's own minute came before it (AC41)."""
    return (
        await session.execute(
            _period_facts(org_id, project_id)
            .where(ReleasePeriod.service_name == service_name, ReleasePeriod.started_at <= at)
            .order_by(ReleasePeriod.started_at.desc(), ReleasePeriod.id.desc())
            .limit(1)
        )
    ).first()


async def period_preceding(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service_name: str,
    release_id: uuid.UUID,
    started_at: datetime,
    period_id: uuid.UUID,
) -> Any | None:
    """The period of another version that ran on the service before this one started
    (F5 FR1: "the version that ran before it"), in the order `period_before` reads."""
    return (
        await session.execute(
            _period_facts(org_id, project_id)
            .where(
                ReleasePeriod.service_name == service_name,
                ReleasePeriod.release_id != release_id,
                tuple_(ReleasePeriod.started_at, ReleasePeriod.id)
                < tuple_(literal(started_at), literal(period_id)),
            )
            .order_by(ReleasePeriod.started_at.desc(), ReleasePeriod.id.desc())
            .limit(1)
        )
    ).first()


async def periods_between(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    services: Sequence[str] | None,
    limit: int,
) -> Sequence[Any]:
    """Releases that started in [start, end], newest first. A version already running
    when counting began is not a release (FR43)."""
    statement = _period_facts(org_id, project_id).where(
        ReleasePeriod.started_at >= start,
        ReleasePeriod.started_at <= end,
        ReleasePeriod.already_running.is_(False),
    )
    if services is not None:
        statement = statement.where(ReleasePeriod.service_name.in_(list(services)))
    statement = statement.order_by(ReleasePeriod.started_at.desc(), ReleasePeriod.id.desc())
    return (await session.execute(statement.limit(limit))).all()


async def latest_period_per_service(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    services: Sequence[str],
    start: datetime,
    end: datetime,
) -> Sequence[Any]:
    """Each named service's latest release starting in [start, end] (FR32's project
    finding). One row per service at most, so the read is bounded by the services."""
    statement = (
        _period_facts(org_id, project_id)
        .where(
            ReleasePeriod.service_name.in_(list(services)),
            ReleasePeriod.started_at >= start,
            ReleasePeriod.started_at <= end,
            ReleasePeriod.already_running.is_(False),
        )
        .distinct(ReleasePeriod.service_name)
        .order_by(
            ReleasePeriod.service_name, ReleasePeriod.started_at.desc(), ReleasePeriod.id.desc()
        )
    )
    return (await session.execute(statement)).all()


# ---------------------------------------------------- the release page (T4.1, T4.4)


async def raw_retention_days(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> int | None:
    """How long the project keeps raw spans and logs, as `project_retention` reads it
    (migration 0005): the project's own limit within its plan's."""
    found: int | None = await session.scalar(
        select(func.least(Project.retention_days, Plan.raw_retention_days))
        .join(Organization, Organization.id == Project.org_id)
        .join(Plan, Plan.slug == Organization.plan)
        .where(
            Project.org_id == org_id,
            Project.id == project_id,
            Organization.deleted_at.is_(None),
        )
    )
    return found


async def project_finding(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, finding_id: uuid.UUID
) -> Finding | None:
    """A finding of this project, for the window a comparison opened from it takes."""
    found: Finding | None = await session.scalar(
        select(Finding).where(
            Finding.org_id == org_id, Finding.project_id == project_id, Finding.id == finding_id
        )
    )
    return found


async def deploys_of(
    session: AsyncSession, *, org_id: uuid.UUID, release_id: uuid.UUID
) -> Sequence[Any]:
    """A release's reported deploys, oldest claim first: facts only, no sender text."""
    return (
        await session.execute(
            select(
                DeployEvent.id,
                DeployEvent.deployed_at,
                DeployEvent.created_at,
                DeployEvent.state,
                DeployEvent.commit_sha,
            )
            .where(DeployEvent.org_id == org_id, DeployEvent.release_id == release_id)
            .order_by(DeployEvent.deployed_at, DeployEvent.created_at, DeployEvent.id)
        )
    ).all()


async def sender_text_of(
    session: AsyncSession, *, org_id: uuid.UUID, release_id: uuid.UUID
) -> Sequence[Any]:
    """What each sender wrote beside a release's deploys (FR54): served to a person
    alone, by `sender-details`."""
    return (
        await session.execute(
            select(
                DeployEvent.id,
                DeployEvent.deployed_at,
                DeployEvent.url,
                DeployEvent.source,
                DeployEvent.event_metadata,
            )
            .where(DeployEvent.org_id == org_id, DeployEvent.release_id == release_id)
            .order_by(DeployEvent.deployed_at, DeployEvent.created_at, DeployEvent.id)
        )
    ).all()


async def findings_from(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service_name: str,
    start: datetime,
    end: datetime | None,
    limit: int,
) -> Sequence[Any]:
    """Findings on one service that started in [start, end), oldest first (FR34). The
    caller passes the start's own minute, so a finding in that minute is after it."""
    statement = select(
        Finding.id,
        Finding.title,
        Finding.severity,
        Finding.state,
        Finding.started_at,
    ).where(
        Finding.org_id == org_id,
        Finding.project_id == project_id,
        Finding.service_name == service_name,
        Finding.started_at >= start,
    )
    if end is not None:
        statement = statement.where(Finding.started_at < end)
    statement = statement.order_by(Finding.started_at, Finding.id).limit(limit)
    return (await session.execute(statement)).all()


# FR46: two versions told and never seen beside one seen and never told (a guess).
NAMING_REPORTED_ONLY: Final = 2
NAMING_SEEN_ONLY: Final = 1


async def naming_differs(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    since: datetime,
) -> Sequence[Any]:
    """FR46: each service whose releases since `since` hold at least two versions the
    CI reported and traffic never carried, beside at least one traffic carried and the
    CI never reported, with the newest of each. Grouped in the statement, so the read
    is one row per such service."""
    reported_only = and_(Release.first_reported_at >= since, Release.first_seen_at.is_(None))
    seen_only = and_(Release.first_seen_at >= since, Release.first_reported_at.is_(None))
    newest_reported = func.array_agg(
        aggregate_order_by(Release.version, Release.first_reported_at.desc())
    ).filter(reported_only)
    newest_seen = func.array_agg(
        aggregate_order_by(Release.version, Release.first_seen_at.desc())
    ).filter(seen_only)
    return (
        await session.execute(
            select(
                Release.service_name,
                newest_reported[1].label("reported_version"),
                newest_seen[1].label("seen_version"),
            )
            .where(
                Release.org_id == org_id,
                Release.project_id == project_id,
                or_(reported_only, seen_only),
            )
            .group_by(Release.service_name)
            .having(
                and_(
                    func.count().filter(reported_only) >= NAMING_REPORTED_ONLY,
                    func.count().filter(seen_only) >= NAMING_SEEN_ONLY,
                )
            )
            .order_by(Release.service_name)
        )
    ).all()
