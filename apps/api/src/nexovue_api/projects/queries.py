"""Project reads and writes. Every one takes an org_id — there is no unscoped variant.

The statements that close a project's doors name other domains' tables —
`api_keys`, `site_keys`, `project_webhook_secrets` — and are here rather than in
those packages because what they express is one fact about a project, not four
facts about credentials. `usage_daily` is named for the same reason: read here it
answers "has this project ever reported", which is a fact about the project
rather than about the bill. Each still filters `org_id` as well as `project_id`,
so the tenancy is in the statement and not in a check somebody remembered.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    ApiKey,
    Check,
    Integration,
    Project,
    ProjectWebhookSecret,
    SiteKey,
    UsageDaily,
)


async def list_projects(session: AsyncSession, org_id: uuid.UUID) -> Sequence[Project]:
    """This organization's live projects, oldest first so `production` leads."""
    statement = (
        select(Project)
        .where(Project.org_id == org_id, Project.deleted_at.is_(None))
        .order_by(Project.created_at, Project.slug)
    )
    return (await session.scalars(statement)).all()


async def first_reported_days(session: AsyncSession, org_id: uuid.UUID) -> dict[uuid.UUID, date]:
    """The first day each of this organization's projects sent us anything.

    Read from the ingest meter, not from ClickHouse. The meter counts what
    *arrived*, its rows outlive every retention window, and one row exists for
    any signal — so it still answers for a project whose telemetry has since
    expired. A `min(timestamp)` over the telemetry store would call that project
    one that never reported, which is the mistake this read exists to end.

    A project reporting for the first time right now is absent until the meter
    flushes. That is the harmless direction: it is new, and reads as new.
    """
    statement = (
        select(UsageDaily.project_id, func.min(UsageDaily.day))
        .where(UsageDaily.org_id == org_id)
        .group_by(UsageDaily.project_id)
    )
    first_days: dict[uuid.UUID, date] = {}
    for project_id, first_day in (await session.execute(statement)).all():
        first_days[project_id] = first_day
    return first_days


async def find_project(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Project | None:
    """One project, or None. Another tenant's project is None, not a row."""
    statement = select(Project).where(
        Project.id == project_id,
        Project.org_id == org_id,
        Project.deleted_at.is_(None),
    )
    project: Project | None = await session.scalar(statement)
    return project


async def lock_project(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Project | None:
    """The project row, held until this transaction ends.

    Locked for the same reason `lock_organization` is: two deletions arriving
    together would otherwise both read a live project and both write a
    `deleted_at`, and the second would move the moment the doors closed forward.
    """
    statement = (
        select(Project)
        .where(
            Project.id == project_id,
            Project.org_id == org_id,
            Project.deleted_at.is_(None),
        )
        .with_for_update()
    )
    project: Project | None = await session.scalar(statement)
    return project


async def slugs_beginning_with(session: AsyncSession, *, org_id: uuid.UUID, base: str) -> set[str]:
    """Every slug in this organization that could collide with `base` or a
    suffixed form of it. Deleted projects are included: the unique constraint is
    on `(org_id, slug)` and does not care that a row is tombstoned."""
    statement = select(Project.slug).where(Project.org_id == org_id, Project.slug.startswith(base))
    return set((await session.scalars(statement)).all())


async def slug_is_taken(session: AsyncSession, *, org_id: uuid.UUID, slug: str) -> bool:
    """Whether this exact identifier is already used in this organization.

    Deleted projects count. `(org_id, slug)` is unique whether or not a row is
    tombstoned, so a slug a deletion left behind would fail on insert rather than
    be reported.
    """
    statement = select(exists().where(Project.org_id == org_id, Project.slug == slug))
    return bool(await session.scalar(statement))


async def live_project_count(session: AsyncSession, org_id: uuid.UUID) -> int:
    """How many projects this organization still has."""
    statement = (
        select(func.count())
        .select_from(Project)
        .where(Project.org_id == org_id, Project.deleted_at.is_(None))
    )
    return int(await session.scalar(statement) or 0)


async def enabled_check_count(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> int:
    """Synthetic checks the scheduler would still run for this project.

    The scheduler selects on `enabled` alone and knows nothing about
    `projects.deleted_at`, so a deleted project with an enabled check would go on
    fetching somebody's URL from our infrastructure for ever.
    """
    statement = (
        select(func.count())
        .select_from(Check)
        .where(
            Check.org_id == org_id,
            Check.project_id == project_id,
            Check.enabled.is_(True),
        )
    )
    return int(await session.scalar(statement) or 0)


async def integration_count(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> int:
    """Connectors the poller would still assume a role for, same reasoning."""
    statement = (
        select(func.count())
        .select_from(Integration)
        .where(Integration.org_id == org_id, Integration.project_id == project_id)
    )
    return int(await session.scalar(statement) or 0)


async def revoke_api_keys(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, at: datetime
) -> int:
    """Withdraw every live secret ingest key this project holds.

    The receiver resolves a key on `revoked_at IS NULL` and never joins
    `projects`, so this — not `projects.deleted_at` — is what actually stops a
    deleted project ingesting.
    """
    statement = (
        update(ApiKey)
        .where(
            ApiKey.org_id == org_id,
            ApiKey.project_id == project_id,
            ApiKey.revoked_at.is_(None),
        )
        .values(revoked_at=at)
    )
    return await _rows_changed(session, statement)


async def revoke_site_keys(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, at: datetime
) -> int:
    """The browser door's keys, closed on the same column for the same reason."""
    statement = (
        update(SiteKey)
        .where(
            SiteKey.org_id == org_id,
            SiteKey.project_id == project_id,
            SiteKey.revoked_at.is_(None),
        )
        .values(revoked_at=at)
    )
    return await _rows_changed(session, statement)


async def revoke_webhook_secrets(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, at: datetime
) -> int:
    """The deploy door: the one write in the control plane the internet can reach.

    `live_webhook_secrets` is deliberately unscoped by organization — the caller
    has proved nothing but the signature — and it does not look at the project
    either, so an open secret on a deleted project stays open.
    """
    statement = (
        update(ProjectWebhookSecret)
        .where(
            ProjectWebhookSecret.org_id == org_id,
            ProjectWebhookSecret.project_id == project_id,
            ProjectWebhookSecret.revoked_at.is_(None),
        )
        .values(revoked_at=at)
    )
    return await _rows_changed(session, statement)


async def _rows_changed(session: AsyncSession, statement: Any) -> int:
    """How many rows a DML statement touched.

    `AsyncSession.execute` is typed as returning the abstract `Result`, which has
    no row count; what it actually returns for DML is a `CursorResult`, which does.
    """
    result = await session.execute(statement)
    return int(cast(CursorResult[Any], result).rowcount)
