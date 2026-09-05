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

from nexovue_api.data.postgres.models import PlanPrice, Project, UsageDaily, UsageDailyService
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant
from nexovue_api.usage import queries, service
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
    db: AsyncSession,
    tenant: ProvisionedTenant,
    *,
    day: date,
    spans: int,
    byte_count: int,
    browser_byte_count: int = 0,
) -> None:
    # `browser_bytes` is a part of `bytes`, so the default is a day nothing came
    # through the browser door — which is every row before ADR 0097.
    db.add(
        UsageDaily(
            org_id=tenant.organization.id,
            project_id=tenant.project.id,
            day=day,
            spans=spans,
            logs=spans * 2,
            metric_points=spans * 3,
            bytes=byte_count,
            browser_bytes=browser_byte_count,
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


# ------------------------------------------------ the month, its cost, and its allowance


async def _record_price(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    *,
    minor_units: int,
    currency: str = "USD",
    ceiling: int | None = None,
    included_bytes: int | None = None,
    browser_share: int | None = None,
) -> None:
    """A real price row, in the transaction this suite rolls back.

    ADR 0077 shipped `plan_prices` empty and recorded that no rate had ever been
    read from real Postgres. These are the tests that read one.
    """
    db.add(
        PlanPrice(
            plan_slug=tenant.organization.plan,
            effective_from=TO_DAY - timedelta(days=30),
            currency=currency,
            price_per_service_minor_units=minor_units,
            max_billed_services=ceiling,
            included_bytes_per_service=included_bytes,
            browser_share_percent=browser_share,
        )
    )
    await db.flush()


async def test_the_split_is_read_from_a_real_price_row(db: AsyncSession) -> None:
    """Migration 0016's free row, read back through Postgres (ADR 0097).

    The offline suite builds the row in Python, so it cannot prove the column
    exists or that a value survives the round trip. This can.
    """
    gib = 1024**3
    tenant = await _new_tenant(db)
    await _record_price(
        db, tenant, minor_units=0, ceiling=3, included_bytes=2 * gib, browser_share=25
    )

    answer = await _read(db, tenant)

    assert answer.period.included_bytes == 6 * gib
    assert answer.period.browser_included_bytes == 6 * gib // 4
    assert answer.period.server_included_bytes == 6 * gib - 6 * gib // 4
    assert answer.price is not None
    assert answer.price.browser_share_percent == 25


async def test_a_price_row_recording_no_share_reports_no_split(db: AsyncSession) -> None:
    """Every row seeded before 0016, read back: one allowance, both doors."""
    tenant = await _new_tenant(db)
    await _record_price(db, tenant, minor_units=0, ceiling=3, included_bytes=1_000)

    answer = await _read(db, tenant)

    assert answer.period.included_bytes == 3_000
    assert answer.period.browser_included_bytes is None
    assert answer.period.server_included_bytes is None


async def test_the_browser_bytes_are_summed_from_their_own_column(db: AsyncSession) -> None:
    """The part and the whole, summed by Postgres rather than by a fake.

    The offline store adds the rows in Python, so a statement summing `bytes`
    twice would answer correctly there and be wrong here.
    """
    tenant = await _new_tenant(db)
    await _record(db, tenant, day=TO_DAY, spans=100, byte_count=1_000, browser_byte_count=400)
    await _record(db, tenant, day=TO_DAY - timedelta(days=1), spans=25, byte_count=250)

    answer = await _read(db, tenant)

    assert answer.period.bytes_ingested == 1_250
    assert answer.period.browser_bytes_ingested == 400


async def test_the_period_is_the_calendar_month_around_the_last_day(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)

    answer = await _read(db, tenant)

    assert (answer.period.first_day, answer.period.last_day) == (date(2026, 8, 1), TO_DAY)


async def test_the_month_is_priced_from_a_row_real_postgres_returned(db: AsyncSession) -> None:
    """Two services in the month, one of them outside the browsed range.

    The range shows one service and the month bills two, so a cost computed
    from whichever number the screen happens to be showing is wrong by half.
    """
    tenant = await _new_tenant(db)
    await _record_price(db, tenant, minor_units=1_000)
    await _record_service(db, tenant, day=date(2026, 8, 2), name="checkout")
    await _record_service(db, tenant, day=TO_DAY, name="payments")

    answer = await _read(db, tenant)

    assert answer.totals.services == 1
    assert answer.period.services == 2
    assert answer.period.service_cost_minor_units == 2_000
    assert answer.period.currency == "USD"


async def test_a_service_reporting_all_month_is_billed_once_by_postgres(
    db: AsyncSession,
) -> None:
    """The union again, this time behind the multiplication.

    Twenty-eight rows, one service, one service's worth of money. Without the
    distinct count the bill is twenty-eight times too large.
    """
    tenant = await _new_tenant(db)
    await _record_price(db, tenant, minor_units=1_000)
    for offset in range(28):
        await _record_service(
            db, tenant, day=date(2026, 8, 1) + timedelta(days=offset), name="checkout"
        )

    answer = await _read(db, tenant)

    assert answer.period.services == 1
    assert answer.period.service_cost_minor_units == 1_000


async def test_the_ceiling_in_a_real_row_holds_the_bill_down(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    await _record_price(db, tenant, minor_units=1_000, ceiling=2)
    for index in range(5):
        await _record_service(db, tenant, day=TO_DAY, name=f"api-{index}-service")

    answer = await _read(db, tenant)

    assert answer.period.services == 5
    assert answer.period.billed_services == 2
    assert answer.period.service_cost_minor_units == 2_000


async def test_the_allowance_is_read_from_the_row_and_pooled(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    await _record_price(db, tenant, minor_units=1_000, included_bytes=1_000_000)
    await _record_service(db, tenant, day=TO_DAY, name="checkout")
    await _record_service(db, tenant, day=TO_DAY, name="payments")
    await _record(db, tenant, day=TO_DAY, spans=10, byte_count=2_500)

    answer = await _read(db, tenant)

    assert answer.period.included_bytes == 2_000_000
    assert answer.period.bytes_ingested == 2_500


async def test_a_capped_tier_reads_the_ceilings_allowance_from_real_postgres(
    db: AsyncSession,
) -> None:
    """The free tier as migration 0015 seeds it: 2 GiB x 3, whatever is running.

    Enforced at the ingest door (ADR 0093), so the number the screen states has
    to be the number the door applies — and it must not shrink to one service's
    worth because only one service happens to be reporting.
    """
    gib = 1024**3
    tenant = await _new_tenant(db)
    await _record_price(db, tenant, minor_units=0, ceiling=3, included_bytes=2 * gib)
    await _record_service(db, tenant, day=TO_DAY, name="checkout")

    answer = await _read(db, tenant)

    assert answer.period.included_bytes == 6 * gib
    assert answer.period.allowance_enforced is True


async def test_an_uncapped_tier_read_from_real_postgres_enforces_nothing(
    db: AsyncSession,
) -> None:
    """Blocking a paying customer would be far worse than the hole being closed."""
    tenant = await _new_tenant(db)
    await _record_price(db, tenant, minor_units=600, included_bytes=10 * 1024**3)
    await _record_service(db, tenant, day=TO_DAY, name="checkout")

    assert (await _read(db, tenant)).period.allowance_enforced is False


async def test_a_price_row_with_no_allowance_reports_none_rather_than_zero(
    db: AsyncSession,
) -> None:
    """Zero would report every byte ingested as overage."""
    tenant = await _new_tenant(db)
    await _record_price(db, tenant, minor_units=1_000)
    await _record_service(db, tenant, day=TO_DAY, name="checkout")

    assert (await _read(db, tenant)).period.included_bytes is None


async def test_the_months_bytes_stop_at_the_month(db: AsyncSession) -> None:
    """A day in July is not in August's bill."""
    tenant = await _new_tenant(db)
    await _record(db, tenant, day=TO_DAY, spans=10, byte_count=1_000)
    await _record(db, tenant, day=date(2026, 7, 31), spans=10, byte_count=9_999)

    assert (await _read(db, tenant)).period.bytes_ingested == 1_000


async def test_another_organizations_month_is_never_in_this_ones_bill(db: AsyncSession) -> None:
    owner, stranger = await _new_tenant(db), await _new_tenant(db)
    await _record_price(db, owner, minor_units=1_000)
    await _record_service(db, owner, day=TO_DAY, name="checkout")
    await _record(db, owner, day=TO_DAY, spans=10, byte_count=1_000)

    theirs = await _read(db, stranger)

    assert theirs.period.services == 0
    assert theirs.period.bytes_ingested == 0


# ---------------------------------------------------------- cardinality, in real SQL


async def test_the_windowed_counts_are_distinct_in_postgres(db: AsyncSession) -> None:
    """Seven days of one service is one service in the window, not seven.

    The offline suite answers through a Python set, which would dedupe whether
    or not the statement asked it to. This is the layer that actually holds the
    guarantee: without `distinct` here a steady project reads as an exploding
    one and gets warned at for nothing.
    """
    tenant = await _new_tenant(db)
    for offset in range(7):
        await _record_service(db, tenant, day=FROM_DAY + timedelta(days=offset), name="checkout")

    counted = await queries.service_counts_by_project(
        db,
        org_id=tenant.organization.id,
        previous_from=FROM_DAY - timedelta(days=7),
        recent_from=FROM_DAY,
        to_day=TO_DAY,
    )

    assert [(row.recent, row.previous) for row in counted] == [(1, 0)]


async def test_the_name_cap_is_applied_per_project_not_across_them(db: AsyncSession) -> None:
    """One project exploding must not crowd another out of the answer.

    The cap is a window function partitioned by project, and a plain LIMIT
    would return two names from the first project and none from the second.
    """
    tenant = await _new_tenant(db)
    staging = await _second_project(db, tenant)
    for index in range(3):
        await _record_service(db, tenant, day=TO_DAY, name=f"noisy-{index}-service")
    await _record_service(db, tenant, day=TO_DAY, name="quiet", project_id=staging.id)

    names = await queries.service_names(
        db,
        org_id=tenant.organization.id,
        project_ids=[tenant.project.id, staging.id],
        from_day=FROM_DAY,
        to_day=TO_DAY,
        limit_per_project=2,
    )

    by_project: dict[uuid.UUID, list[str]] = {}
    for row in names:
        by_project.setdefault(row.project_id, []).append(row.service_name)
    assert len(by_project[tenant.project.id]) == 2
    assert by_project[staging.id] == ["quiet"]


async def test_pod_names_are_warned_about_end_to_end(db: AsyncSession) -> None:
    """The whole path: real rows, real SQL, the family named in the response."""
    tenant = await _new_tenant(db)
    for suffix in ("x2k9p", "m4rt7", "bn6qw", "kt9zx", "vd4sr", "hj7bk", "cz2mn", "wq8fd"):
        await _record_service(db, tenant, day=TO_DAY, name=f"checkout-7d9f8b6c4d-{suffix}")

    answer = await _read(db, tenant)

    assert len(answer.cardinality) == 1
    warning = answer.cardinality[0]
    assert warning.project_id == tenant.project.id
    assert warning.services_now == 8
    assert [(family.stem, family.kind) for family in warning.families] == [
        ("checkout", "kubernetes-pod")
    ]


async def test_honestly_named_services_are_never_warned_about(db: AsyncSession) -> None:
    """Ten real services, in real SQL, and nothing said about them."""
    tenant = await _new_tenant(db)
    for name in (
        "checkout",
        "payments",
        "shipping",
        "inventory",
        "api-gateway",
        "web-frontend",
        "auth-service",
        "search",
        "recommendation-engine",
        "email-worker",
    ):
        await _record_service(db, tenant, day=TO_DAY, name=name)

    assert (await _read(db, tenant)).cardinality == []
