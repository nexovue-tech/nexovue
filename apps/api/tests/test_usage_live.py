"""Usage counters against real Postgres: one tenant cannot reach another's.

The offline suite proves the queries carry a tenant predicate. This proves the
database agrees — two real organizations provisioned the way signup provisions
them, real `usage_daily` rows, and every cross-tenant read answered as though
the rows did not exist.

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

from nexovue_api.data.postgres.models import Project, UsageDaily, UsageDailyService
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant
from nexovue_api.usage import service
from nexovue_api.usage.schemas import UsageResponse

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

TO_DAY: Final = date(2026, 8, 31)
FROM_DAY: Final = TO_DAY - timedelta(days=6)


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection((parts.hostname or "", parts.port or 5432), timeout=2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="the live usage suite",
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
    tenant = await provision_tenant(
        db,
        organization_name=f"Usage Live {unique}",
        email=f"owner-{unique}@live.test",
        password="correct-horse-battery",
    )
    # `organizations.plan` is a server default, so the inserted object does not
    # carry it until the row is read back.
    await db.refresh(tenant.organization)
    return tenant


async def _record(
    db: AsyncSession, tenant: ProvisionedTenant, *, day: date, spans: int, byte_count: int
) -> None:
    db.add(
        UsageDaily(
            org_id=tenant.organization.id,
            project_id=tenant.project.id,
            day=day,
            spans=spans,
            logs=spans * 2,
            metric_points=spans * 3,
            bytes=byte_count,
        )
    )
    await db.flush()


async def _read(
    db: AsyncSession, tenant: ProvisionedTenant, *, project_id: uuid.UUID | None = None
) -> UsageResponse:
    return await service.usage_for_organization(
        db,
        organization=tenant.organization,
        from_day=FROM_DAY,
        to_day=TO_DAY,
        project_id=project_id,
    )


async def test_an_organizations_own_counters_are_totalled(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    await _record(db, tenant, day=TO_DAY, spans=100, byte_count=1_000)
    await _record(db, tenant, day=TO_DAY - timedelta(days=1), spans=25, byte_count=250)

    answer = await _read(db, tenant)

    assert answer.totals.spans == 125
    assert answer.totals.logs == 250
    assert answer.totals.metric_points == 375
    assert answer.totals.bytes_ingested == 1_250
    # Day order, oldest first.
    assert [day.day for day in answer.days] == [TO_DAY - timedelta(days=1), TO_DAY]


async def test_another_organizations_counters_are_invisible(db: AsyncSession) -> None:
    """Not filtered out afterwards — never selected. The predicate is the boundary."""
    owner, stranger = await _new_tenant(db), await _new_tenant(db)
    await _record(db, owner, day=TO_DAY, spans=100, byte_count=1_000)

    theirs = await _read(db, stranger)

    assert theirs.days == []
    assert theirs.totals.spans == 0


async def test_narrowing_to_another_organizations_project_returns_nothing(
    db: AsyncSession,
) -> None:
    """The project id is a value a caller could guess. Naming one that belongs
    to somebody else must not reach it, even for an authenticated tenant."""
    owner, stranger = await _new_tenant(db), await _new_tenant(db)
    await _record(db, owner, day=TO_DAY, spans=100, byte_count=1_000)

    theirs = await _read(db, stranger, project_id=owner.project.id)

    assert theirs.days == []


async def test_days_outside_the_range_are_not_counted(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    await _record(db, tenant, day=TO_DAY, spans=100, byte_count=1_000)
    await _record(db, tenant, day=FROM_DAY - timedelta(days=1), spans=7, byte_count=70)

    answer = await _read(db, tenant)

    assert answer.totals.spans == 100


async def test_the_plan_a_new_tenant_lands_on_is_read_from_the_plans_table(
    db: AsyncSession,
) -> None:
    """Signup does not set a tier, so the column default applies (ADR 0071)."""
    tenant = await _new_tenant(db)

    answer = await _read(db, tenant)

    assert answer.plan.slug == tenant.organization.plan
    assert answer.plan.raw_retention_days > 0
    assert answer.plan.derived_retention_days > 0


# ------------------------------------------------ the billable count, in real SQL


async def _record_service(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    *,
    day: date,
    name: str,
    project_id: uuid.UUID | None = None,
) -> None:
    db.add(
        UsageDailyService(
            org_id=tenant.organization.id,
            project_id=project_id or tenant.project.id,
            day=day,
            service_name=name,
        )
    )
    await db.flush()


async def _second_project(db: AsyncSession, tenant: ProvisionedTenant) -> Project:
    project = Project(org_id=tenant.organization.id, slug="staging", name="Staging")
    db.add(project)
    await db.flush()
    return project


async def test_a_service_reporting_every_day_is_one_service_for_the_range(
    db: AsyncSession,
) -> None:
    """The union is computed by Postgres, so this is where it is proved.

    The offline suite asserts the statement asks for a distinct count, because a
    fake that dedupes in Python would stay green without one. This asserts the
    database agrees: seven rows, one service, and a bill that would otherwise be
    seven times too large.
    """
    tenant = await _new_tenant(db)
    for offset in range(7):
        await _record_service(db, tenant, day=FROM_DAY + timedelta(days=offset), name="checkout")

    answer = await _read(db, tenant)

    assert answer.totals.services == 1


async def test_the_same_service_name_in_two_projects_is_two_services(db: AsyncSession) -> None:
    """Two teams each running a `checkout` are running two of them."""
    tenant = await _new_tenant(db)
    staging = await _second_project(db, tenant)
    await _record_service(db, tenant, day=TO_DAY, name="checkout")
    await _record_service(db, tenant, day=TO_DAY, name="checkout", project_id=staging.id)

    answer = await _read(db, tenant)

    assert answer.totals.services == 2


async def test_another_organizations_services_are_never_counted(db: AsyncSession) -> None:
    owner, stranger = await _new_tenant(db), await _new_tenant(db)
    await _record_service(db, owner, day=TO_DAY, name="checkout")

    assert (await _read(db, stranger)).totals.services == 0
    assert (await _read(db, owner)).totals.services == 1


async def test_a_tier_with_no_price_row_reports_no_price_at_all(db: AsyncSession) -> None:
    """`plan_prices` ships empty on purpose: a zero would read as free."""
    tenant = await _new_tenant(db)

    assert (await _read(db, tenant)).price is None
