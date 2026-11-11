"""The Postgres reads and writes behind dashboards (F20). Every statement binds `org_id`.

Lists read stored columns only, never a definition (AC96). A deleted dashboard is
excluded from every read by one shared predicate, `_live`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import ColumnElement, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    Dashboard,
    DashboardVersion,
    ServiceLastSeen,
    User,
)

# The live list, at most: the cap on live dashboards (FR9).
LIST_LIMIT: Final = 1_000


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


async def list_live(session: AsyncSession, *, org_id: uuid.UUID) -> list[ListedDashboard]:
    """The organization's live dashboards, owners joined, from columns alone."""
    statement = (
        select(Dashboard, User)
        .outerjoin(User, (User.id == Dashboard.created_by) & (User.org_id == Dashboard.org_id))
        .where(Dashboard.org_id == org_id, _live())
        .order_by(func.lower(Dashboard.title), Dashboard.id)
        .limit(LIST_LIMIT)
    )
    rows = (await session.execute(statement)).all()
    return [ListedDashboard(dashboard=row[0], owner=_name_of(row[1])) for row in rows]


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
