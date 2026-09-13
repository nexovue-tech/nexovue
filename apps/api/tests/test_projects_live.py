"""The project list against real Postgres, and the one fact it now carries.

The offline suite proves the statements bind a tenant. This proves the database
agrees about the thing they are read for: whether anything has ever arrived for
a project.

That answer comes from the ingest meter rather than from the telemetry store,
and the reason is the case below — a project metered long ago and silent since
must still read as one that reported. The old screens guessed it from "the
widest time range came back empty", which called a fortnight of quiet a project
that was never instrumented.

Everything runs inside a transaction that is rolled back, so the suite leaves
nothing behind.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.data.postgres.models import Project, UsageDaily
from nexovue_api.projects import service
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

TODAY: Final = date(2026, 8, 31)
LONG_AGO: Final = TODAY - timedelta(days=400)


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection((parts.hostname or "", parts.port or 5432), timeout=2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="the live projects suite",
    remedy="Postgres is not running — start it with `just dev`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A session in a transaction that is always rolled back."""
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _new_tenant(db: AsyncSession) -> ProvisionedTenant:
    unique = uuid.uuid4().hex[:12]
    return await provision_tenant(
        db,
        organization_name=f"Projects Live {unique}",
        email=f"owner-{unique}@live.test",
        password="correct-horse-battery",
    )


async def _second_project(db: AsyncSession, tenant: ProvisionedTenant) -> Project:
    project = Project(org_id=tenant.organization.id, slug="staging", name="Staging")
    db.add(project)
    await db.flush()
    return project


async def _meter(db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, day: date) -> None:
    """One day the ingest meter counted something, as the worker writes it."""
    db.add(UsageDaily(org_id=org_id, project_id=project_id, day=day, spans=1, bytes=100))
    await db.flush()


async def _first_reported(
    db: AsyncSession, tenant: ProvisionedTenant
) -> dict[uuid.UUID, date | None]:
    listing = await service.list_for_organization(db, tenant.organization.id)
    return {project.id: project.first_reported_on for project in listing.projects}


async def test_a_freshly_provisioned_project_has_never_reported(db: AsyncSession) -> None:
    """Signup writes a project and nothing else. Null is the honest answer, and
    the one condition under which a screen may offer setup."""
    tenant = await _new_tenant(db)

    assert await _first_reported(db, tenant) == {tenant.project.id: None}


async def test_postgres_returns_the_earliest_metered_day_and_not_the_latest(
    db: AsyncSession,
) -> None:
    tenant = await _new_tenant(db)
    for day in (TODAY, TODAY - timedelta(days=5), TODAY - timedelta(days=2)):
        await _meter(db, org_id=tenant.organization.id, project_id=tenant.project.id, day=day)

    assert (await _first_reported(db, tenant))[tenant.project.id] == TODAY - timedelta(days=5)


async def test_a_project_metered_over_a_year_ago_still_reads_as_one_that_reported(
    db: AsyncSession,
) -> None:
    """The case this field exists for. No time range a customer can select
    reaches this day, and the project is still not one to send back to setup."""
    tenant = await _new_tenant(db)
    await _meter(db, org_id=tenant.organization.id, project_id=tenant.project.id, day=LONG_AGO)

    assert (await _first_reported(db, tenant))[tenant.project.id] == LONG_AGO


async def test_one_project_reporting_says_nothing_about_the_next(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    staging = await _second_project(db, tenant)
    await _meter(db, org_id=tenant.organization.id, project_id=tenant.project.id, day=TODAY)

    reported = await _first_reported(db, tenant)
    assert reported[tenant.project.id] == TODAY
    assert reported[staging.id] is None


async def test_another_organizations_arrivals_are_invisible(db: AsyncSession) -> None:
    """A read scoped by project alone would find these rows: the meter is keyed
    `(org_id, project_id, day)` and a project id is a value a caller can hold."""
    quiet = await _new_tenant(db)
    busy = await _new_tenant(db)
    await _meter(db, org_id=busy.organization.id, project_id=busy.project.id, day=TODAY)

    assert await _first_reported(db, quiet) == {quiet.project.id: None}
    assert (await _first_reported(db, busy))[busy.project.id] == TODAY
