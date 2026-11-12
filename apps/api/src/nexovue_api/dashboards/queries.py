"""The Postgres reads and writes behind dashboards (F20). Every statement binds `org_id`.

Lists read stored columns only, never a definition (AC96). A deleted dashboard is
excluded from every read by one shared predicate, `_live`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from nexovue_platform.alerts.model import service_subject
from sqlalchemy import ColumnElement, case, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from nexovue_api.data.postgres.models import (
    Check,
    Dashboard,
    DashboardVersion,
    Incident,
    ServiceLastSeen,
    User,
)

# The live list, at most: the cap on live dashboards (FR9).
LIST_LIMIT: Final = 1_000
# Deleted ones count towards the 2,000 (FR9), so a list of them stops there.
ALL_LIST_LIMIT: Final = 2_000
# The most versions a dashboard keeps whatever their age (FR51), so a history lists them all.
VERSIONS_CEILING: Final = 200
# What an incident about a service is keyed by: a threshold rule's subject, or a burn's
# `about_key` (`nexovue_platform.alerts.model`). `service_subject("")` is the bare prefix.
SERVICE_SUBJECT_PREFIX: Final = service_subject("")


def _live() -> ColumnElement[bool]:
    return Dashboard.deleted_at.is_(None)


@dataclass(frozen=True, slots=True)
class ListedDashboard:
    dashboard: Dashboard
    owner: str | None


def _name_of(user: User | None) -> str | None:
    """A current member's name; nobody once they left, which reads "Former member" (FR38)."""
    if user is None or user.removed_at is not None:
        return None
    return user.name or user.email


def _without_definition() -> Any:
    """A list reads columns alone (AC96): the definition is never loaded, so a draw of a
    row's `definition` here fails loudly instead of quietly reading what a list must not."""
    return defer(Dashboard.definition)


async def list_live(session: AsyncSession, *, org_id: uuid.UUID) -> list[ListedDashboard]:
    """The organization's live dashboards, owners joined, from columns alone."""
    statement = (
        select(Dashboard, User)
        .options(_without_definition())
        .outerjoin(User, (User.id == Dashboard.created_by) & (User.org_id == Dashboard.org_id))
        .where(Dashboard.org_id == org_id, _live())
        .order_by(func.lower(Dashboard.title), Dashboard.id)
        .limit(LIST_LIMIT)
    )
    return _listed(await session.execute(statement))


async def list_deleted(
    session: AsyncSession, *, org_id: uuid.UUID, deleted_since: datetime
) -> list[ListedDashboard]:
    """Dashboards deleted since `deleted_since`, newest deletion first: the ones that can
    still be restored (FR51). Past it, the expiry sweep removes them."""
    statement = (
        select(Dashboard, User)
        .options(_without_definition())
        .outerjoin(User, (User.id == Dashboard.created_by) & (User.org_id == Dashboard.org_id))
        .where(Dashboard.org_id == org_id, Dashboard.deleted_at >= deleted_since)
        .order_by(Dashboard.deleted_at.desc(), Dashboard.id)
        .limit(ALL_LIST_LIMIT)
    )
    return _listed(await session.execute(statement))


async def list_for_service(
    session: AsyncSession, *, org_id: uuid.UUID, service: str, limit: int
) -> list[ListedDashboard]:
    """Live dashboards naming a service, or using the picker that can show it (FR57):
    those naming it first, then by last opened, never opened last. Columns alone."""
    names_it = Dashboard.service_names.contains([service])
    statement = (
        select(Dashboard, User)
        .options(_without_definition())
        .outerjoin(User, (User.id == Dashboard.created_by) & (User.org_id == Dashboard.org_id))
        .where(Dashboard.org_id == org_id, _live(), or_(names_it, Dashboard.uses_picker))
        .order_by(
            case((names_it, 0), else_=1),
            Dashboard.last_viewed_at.desc().nulls_last(),
            func.lower(Dashboard.title),
            Dashboard.id,
        )
        .limit(limit)
    )
    return _listed(await session.execute(statement))


def _listed(result: Any) -> list[ListedDashboard]:
    return [ListedDashboard(dashboard=row[0], owner=_name_of(row[1])) for row in result.all()]


async def find_live(
    session: AsyncSession, *, org_id: uuid.UUID, dashboard_id: uuid.UUID, for_update: bool = False
) -> Dashboard | None:
    """One live dashboard, or None. Another organization's is None, not a row."""
    statement = select(Dashboard).where(
        Dashboard.org_id == org_id, Dashboard.id == dashboard_id, _live()
    )
    if for_update:
        statement = statement.with_for_update()
    found: Dashboard | None = await session.scalar(statement)
    return found


async def find_any(
    session: AsyncSession, *, org_id: uuid.UUID, dashboard_id: uuid.UUID, for_update: bool = False
) -> Dashboard | None:
    """One dashboard, live or deleted, for restore and purge. Another organization's is None."""
    statement = select(Dashboard).where(Dashboard.org_id == org_id, Dashboard.id == dashboard_id)
    if for_update:
        statement = statement.with_for_update()
    found: Dashboard | None = await session.scalar(statement)
    return found


@dataclass(frozen=True, slots=True)
class KeptVersion:
    version: DashboardVersion
    saved_by: str | None


async def list_versions(
    session: AsyncSession, *, org_id: uuid.UUID, dashboard_id: uuid.UUID
) -> list[KeptVersion]:
    """The kept versions, newest first, with who saved each. The definition is not read."""
    statement = (
        select(DashboardVersion, User)
        .options(defer(DashboardVersion.definition))
        .outerjoin(User, (User.id == DashboardVersion.saved_by) & (User.org_id == org_id))
        .where(DashboardVersion.org_id == org_id, DashboardVersion.dashboard_id == dashboard_id)
        .order_by(DashboardVersion.version.desc())
        .limit(VERSIONS_CEILING)
    )
    return [
        KeptVersion(version=row[0], saved_by=_name_of(row[1]))
        for row in (await session.execute(statement)).all()
    ]


async def find_version(
    session: AsyncSession, *, org_id: uuid.UUID, dashboard_id: uuid.UUID, version: int
) -> KeptVersion | None:
    statement = (
        select(DashboardVersion, User)
        .outerjoin(User, (User.id == DashboardVersion.saved_by) & (User.org_id == org_id))
        .where(
            DashboardVersion.org_id == org_id,
            DashboardVersion.dashboard_id == dashboard_id,
            DashboardVersion.version == version,
        )
    )
    row = (await session.execute(statement)).one_or_none()
    return None if row is None else KeptVersion(version=row[0], saved_by=_name_of(row[1]))


async def delete_version(
    session: AsyncSession, *, org_id: uuid.UUID, dashboard_id: uuid.UUID, version: int
) -> None:
    await session.execute(
        delete(DashboardVersion).where(
            DashboardVersion.org_id == org_id,
            DashboardVersion.dashboard_id == dashboard_id,
            DashboardVersion.version == version,
        )
    )


async def delete_dashboard_row(
    session: AsyncSession, *, org_id: uuid.UUID, dashboard_id: uuid.UUID
) -> None:
    """The row, and its versions with it (the foreign key cascades)."""
    await session.execute(
        delete(Dashboard).where(Dashboard.org_id == org_id, Dashboard.id == dashboard_id)
    )


async def record_panels_not_reading(
    session: AsyncSession, *, org_id: uuid.UUID, dashboard_id: uuid.UUID, count: int
) -> None:
    """The count the list shows beside a dashboard: panels the last stamped read found
    unable to read (design, *The list*). One short write, never a version, never a save."""
    await session.execute(
        update(Dashboard)
        .where(Dashboard.org_id == org_id, Dashboard.id == dashboard_id)
        .values(panels_not_reading=count)
    )


async def person_name(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID | None
) -> str | None:
    if user_id is None:
        return None
    user = await session.scalar(select(User).where(User.org_id == org_id, User.id == user_id))
    return _name_of(user)


async def live_title_holder(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    title: str,
    other_than: uuid.UUID | None = None,
) -> Dashboard | None:
    """The live dashboard holding a title, ignoring case (BR12)."""
    statement = select(Dashboard).where(
        Dashboard.org_id == org_id, _live(), func.lower(Dashboard.title) == title.lower()
    )
    if other_than is not None:
        statement = statement.where(Dashboard.id != other_than)
    found: Dashboard | None = await session.scalar(statement.limit(1))
    return found


async def hold_the_organizations_dashboards(session: AsyncSession, *, org_id: uuid.UUID) -> None:
    """Serialize creates within one organization, until the transaction ends, so two at
    the cap cannot both pass its count. A lock of its own, not the organization's row."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('dashboards:' || CAST(:org_id AS text)))"),
        {"org_id": str(org_id)},
    )


@dataclass(frozen=True, slots=True)
class Counts:
    live: int
    all: int


async def counts(session: AsyncSession, *, org_id: uuid.UUID) -> Counts:
    """Live dashboards, and every row, deleted ones too (FR9)."""
    statement = select(
        func.count().filter(_live()),
        func.count(),
    ).where(Dashboard.org_id == org_id)
    live, every = (await session.execute(statement)).one()
    return Counts(live=int(live), all=int(every))


async def trim_versions(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    keep_newest: int,
    older_than: datetime,
    ceiling: int,
) -> None:
    """Drop versions past the newest `keep_newest` *and* saved before `older_than`, and
    anything past `ceiling` whatever its age (FR51)."""
    ranked = (
        select(
            DashboardVersion.version,
            DashboardVersion.saved_at,
            func.row_number().over(order_by=DashboardVersion.version.desc()).label("place"),
        )
        .where(DashboardVersion.org_id == org_id, DashboardVersion.dashboard_id == dashboard_id)
        .subquery()
    )
    doomed = select(ranked.c.version).where(
        ((ranked.c.place > keep_newest) & (ranked.c.saved_at < older_than))
        | (ranked.c.place > ceiling)
    )
    await session.execute(
        delete(DashboardVersion).where(
            DashboardVersion.org_id == org_id,
            DashboardVersion.dashboard_id == dashboard_id,
            DashboardVersion.version.in_(doomed),
        )
    )


async def last_seen_of(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    services: Sequence[str],
) -> dict[str, datetime]:
    """When each named service last reported to the project, by the usage meter.

    A service with no row has no record in this project, which a panel says as
    `not_in_project`, identical whether it reports elsewhere or nowhere (FR16).
    """
    if not services:
        return {}
    statement = select(ServiceLastSeen.service_name, ServiceLastSeen.last_seen_at).where(
        ServiceLastSeen.org_id == org_id,
        ServiceLastSeen.project_id == project_id,
        ServiceLastSeen.service_name.in_(list(services)),
    )
    return dict((await session.execute(statement)).tuples().all())


async def project_last_seen(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> datetime | None:
    """When anything last reported to the project, or None if nothing ever has (FR48)."""
    statement = select(func.max(ServiceLastSeen.last_seen_at)).where(
        ServiceLastSeen.org_id == org_id, ServiceLastSeen.project_id == project_id
    )
    newest: datetime | None = await session.scalar(statement)
    return newest


async def checks_named(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    names: Sequence[str],
) -> dict[str, list[str]]:
    """Each named check's ids in the project: none is `not_in_project`, two `ambiguous`,
    since check names are not unique (F20, FR16, AC22). Another project's checks of that
    name are never read."""
    if not names:
        return {}
    statement = (
        select(Check.name, Check.id)
        .where(Check.org_id == org_id, Check.project_id == project_id, Check.name.in_(list(names)))
        .order_by(Check.name, Check.id)
    )
    found: dict[str, list[str]] = {}
    for name, check_id in (await session.execute(statement)).tuples().all():
        found.setdefault(name, []).append(str(check_id))
    return found


@dataclass(frozen=True, slots=True)
class ServiceIncident:
    id: uuid.UUID
    service_name: str
    rule_name: str
    severity: str
    opened_at: datetime
    resolved_at: datetime | None


async def service_incidents_in_window(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[ServiceIncident]:
    """Alert incidents open at any moment of the window whose rule names a service, newest
    first (F20, FR67). A service-error, latency or silence rule keys its incident by the
    service; a burn names the service it is about, when it is about one."""
    about = func.coalesce(Incident.about_key, Incident.subject_key)
    statement = (
        select(Incident, about)
        .where(
            Incident.org_id == org_id,
            Incident.project_id == project_id,
            Incident.opened_at < end,
            or_(Incident.resolved_at.is_(None), Incident.resolved_at >= start),
            about.startswith(SERVICE_SUBJECT_PREFIX, autoescape=True),
        )
        .order_by(Incident.opened_at.desc(), Incident.id)
        .limit(limit)
    )
    return [
        ServiceIncident(
            id=incident.id,
            service_name=key.removeprefix(SERVICE_SUBJECT_PREFIX),
            rule_name=incident.rule_name,
            severity=incident.severity,
            opened_at=incident.opened_at,
            resolved_at=incident.resolved_at,
        )
        for incident, key in (await session.execute(statement)).tuples().all()
    ]
