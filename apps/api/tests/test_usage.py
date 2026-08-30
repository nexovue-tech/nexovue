"""Usage queries carry their tenant in the statement, and cost is not invented.

Every query here is captured before it reaches a database and its compiled
parameters are inspected. A query that lost its `org_id` predicate would still
return rows against a real Postgres if the fixture data happened to belong to
the right tenant — so asserting on the *statement* is what proves the filter is
there, rather than proving the fixture was lucky. The same technique as
`test_integrations.py`.

The service tests use a store that answers *from those bound values*, so a lost
predicate produces an empty answer rather than a correct-looking one.

`test_usage_live.py` runs the same operations against real Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

import pytest

from nexovue_api.data.postgres.models import (
    Organization,
    Plan,
    PlanPrice,
    UsageDaily,
    UsageDailyService,
)
from nexovue_api.errors.exceptions import InternalError, InvalidRequestError
from nexovue_api.usage import queries, service
from nexovue_api.usage.schemas import UsageResponse

ORG_ID: Final = uuid.uuid4()
OTHER_ORG_ID: Final = uuid.uuid4()
PROJECT_ID: Final = uuid.uuid4()
OTHER_PROJECT_ID: Final = uuid.uuid4()

FROM_DAY: Final = date(2026, 8, 1)
TO_DAY: Final = date(2026, 8, 31)

FREE_PLAN: Final = Plan(slug="free", name="Free", raw_retention_days=7, derived_retention_days=30)
PRO_PLAN: Final = Plan(slug="pro", name="Pro", raw_retention_days=30, derived_retention_days=395)


# --------------------------------------------------------- the statements only


class CapturingSession:
    """Records the statement it was handed and answers nothing."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def scalars(self, statement: Any) -> Any:
        self.statements.append(statement)
        return _Empty()

    async def scalar(self, statement: Any) -> Any:
        self.statements.append(statement)
        return None

    @property
    def bound_values(self) -> set[Any]:
        return set(self.statements[-1].compile().params.values())


class _Empty:
    def all(self) -> list[Any]:
        return []


@pytest.fixture
def session() -> CapturingSession:
    return CapturingSession()


async def test_reading_daily_usage_binds_the_organization(session: CapturingSession) -> None:
    """A range scoped only by day would return every tenant's counters."""
    await queries.daily_usage(
        session,  # type: ignore[arg-type]
        org_id=ORG_ID,
        from_day=FROM_DAY,
        to_day=TO_DAY,
    )

    assert {ORG_ID, FROM_DAY, TO_DAY} <= session.bound_values
    assert OTHER_ORG_ID not in session.bound_values


async def test_narrowing_to_a_project_still_binds_the_organization(
    session: CapturingSession,
) -> None:
    """The project id is a value the caller chose; it may not replace the tenant."""
    await queries.daily_usage(
        session,  # type: ignore[arg-type]
        org_id=ORG_ID,
        from_day=FROM_DAY,
        to_day=TO_DAY,
        project_id=PROJECT_ID,
    )

    assert {ORG_ID, PROJECT_ID} <= session.bound_values


# ------------------------------------------------------------ what comes back


class FakeUsageStore:
    """Answers from the values a statement bound, never from its text.

    A query that lost its `org_id` predicate binds no organization, matches no
    row, and reports a tenant with traffic as a tenant with none — which is what
    the isolation tests below then catch.
    """

    def __init__(
        self,
        rows: Sequence[UsageDaily],
        plans: Sequence[Plan],
        services: Sequence[UsageDailyService] = (),
        prices: Sequence[PlanPrice] = (),
        counted_through: datetime | None = None,
    ) -> None:
        self._rows = rows
        self._plans = plans
        self._service_rows = list(services)
        self._prices = prices
        self._counted_through = counted_through

    async def scalars(self, statement: Any) -> Any:
        bound = set(statement.compile().params.values())
        return _Rows(
            [
                row
                for row in self._rows
                if row.org_id in bound
                and (row.project_id in bound or not self._narrowed(bound))
                and FROM_DAY <= row.day <= TO_DAY
            ]
        )

    async def scalar(self, statement: Any) -> Any:
        bound = set(statement.compile().params.values())
        match _reads(statement):
            case "plan_prices":
                return next((p for p in self._prices if p.plan_slug in bound), None)
            case "usage_daily_services":
                return len({(row.project_id, row.service_name) for row in self._services(bound)})
            case _:
                return next((plan for plan in self._plans if plan.slug in bound), None)

    async def execute(self, statement: Any) -> Any:
        bound = set(statement.compile().params.values())
        if _reads(statement) == "usage_ingest_offsets":
            return _One((self._counted_through, 0))
        counts: dict[tuple[uuid.UUID, date], int] = {}
        for row in self._services(bound):
            counts[(row.project_id, row.day)] = counts.get((row.project_id, row.day), 0) + 1
        return _Rows(
            [_ServiceCount(project, day, n) for (project, day), n in sorted(counts.items())]
        )

    def _services(self, bound: set[Any]) -> list[UsageDailyService]:
        return [
            row
            for row in self._service_rows
            if row.org_id in bound
            and (row.project_id in bound or not self._narrowed(bound))
            and FROM_DAY <= row.day <= TO_DAY
        ]

    def _narrowed(self, bound: set[Any]) -> bool:
        return any(row.project_id in bound for row in self._rows)


def _reads(statement: Any) -> str:
    """Which table a statement is against, read structurally rather than from text.

    The fake still answers from the values a statement *bound* — this only picks
    which set of rows to filter, so a query that lost its `org_id` predicate
    still matches nothing and the isolation tests still bite.
    """
    return next((table.name for table in statement.get_final_froms()), "")


class _One:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self._row = row

    def one(self) -> tuple[Any, ...]:
        return self._row


class _ServiceCount:
    """The shape `daily_service_counts` returns: a project-day and its count."""

    def __init__(self, project_id: uuid.UUID, day: date, services: int) -> None:
        self.project_id = project_id
        self.day = day
        self.services = services


class _Rows:
    """Whatever the statement asked for — daily counters, or per-day service counts."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


def _usage(
    org_id: uuid.UUID, project_id: uuid.UUID, day: date, *, spans: int, byte_count: int
) -> UsageDaily:
    return UsageDaily(
        org_id=org_id,
        project_id=project_id,
        day=day,
        spans=spans,
        logs=spans * 2,
        metric_points=spans * 3,
        bytes=byte_count,
    )


def _organization(org_id: uuid.UUID, plan: str = "pro") -> Organization:
    return Organization(id=org_id, slug=f"org-{org_id.hex[:8]}", name="Acme", plan=plan)


_ROWS: Final = [
    _usage(ORG_ID, PROJECT_ID, date(2026, 8, 10), spans=100, byte_count=1_000),
    _usage(ORG_ID, OTHER_PROJECT_ID, date(2026, 8, 11), spans=50, byte_count=500),
    _usage(OTHER_ORG_ID, PROJECT_ID, date(2026, 8, 10), spans=999, byte_count=9_999),
]


async def _read(
    org_id: uuid.UUID, *, plan: str = "pro", project_id: uuid.UUID | None = None
) -> UsageResponse:
    store = FakeUsageStore(_ROWS, [FREE_PLAN, PRO_PLAN])
    return await service.usage_for_organization(
        store,  # type: ignore[arg-type]
        organization=_organization(org_id, plan),
        from_day=FROM_DAY,
        to_day=TO_DAY,
        project_id=project_id,
    )


async def test_an_organization_sees_only_its_own_counters() -> None:
    mine = await _read(ORG_ID)
    theirs = await _read(OTHER_ORG_ID)

    assert mine.totals.spans == 150
    assert mine.totals.logs == 300
    assert mine.totals.metric_points == 450
    assert mine.totals.bytes_ingested == 1_500
    assert theirs.totals.spans == 999


async def test_narrowing_to_one_project_narrows_the_totals() -> None:
    narrowed = await _read(ORG_ID, project_id=PROJECT_ID)

    assert [day.project_id for day in narrowed.days] == [PROJECT_ID]
    assert narrowed.totals.spans == 100


async def test_the_days_carry_the_counters_the_row_held() -> None:
    answer = await _read(ORG_ID)
    first = answer.days[0]

    assert first.day == date(2026, 8, 10)
    assert (first.spans, first.logs, first.metric_points, first.bytes_ingested) == (
        100,
        200,
        300,
        1_000,
    )


async def test_the_plan_comes_from_the_database_and_not_from_a_constant() -> None:
    """Retention is what a usage screen shows beside the counters: how long what
    was ingested stays readable. Tiers change without a deploy (ADR 0071)."""
    on_free = await _read(ORG_ID, plan="free")
    on_pro = await _read(ORG_ID, plan="pro")

    assert (on_free.plan.slug, on_free.plan.raw_retention_days) == ("free", 7)
    assert (on_pro.plan.slug, on_pro.plan.derived_retention_days) == ("pro", 395)


async def test_a_tier_with_no_row_is_reported_rather_than_defaulted() -> None:
    """`organizations.plan` is a foreign key, so this is a broken database. A
    fallback tier here would misstate the customer's retention silently."""
    with pytest.raises(InternalError):
        await _read(ORG_ID, plan="a-tier-that-was-deleted")


async def test_a_day_nobody_recorded_is_absent_rather_than_zero_filled() -> None:
    """Nothing writes a zero row, so a gap means "not recorded", which is a
    different claim from "nothing was ingested"."""
    answer = await _read(ORG_ID)

    assert len(answer.days) == 2
    assert {day.day for day in answer.days} == {date(2026, 8, 10), date(2026, 8, 11)}


# ------------------------------------------------------------------ the window


def test_the_default_range_is_the_last_thirty_days() -> None:
    start, end = service.resolve_day_range(None, None)

    assert end == datetime.now(UTC).date()
    assert (end - start).days == service.DEFAULT_DAYS - 1


def test_a_single_day_is_a_valid_range() -> None:
    """Ends are inclusive, so `from == to` asks for that one day."""
    assert service.resolve_day_range(FROM_DAY, FROM_DAY) == (FROM_DAY, FROM_DAY)


def test_a_backwards_range_is_refused() -> None:
    with pytest.raises(InvalidRequestError):
        service.resolve_day_range(TO_DAY, FROM_DAY)


def test_a_range_longer_than_any_retention_is_refused() -> None:
    """Beyond 395 days no tier keeps anything, so the extra span is only scan."""
    end = date(2026, 8, 31)
    with pytest.raises(InvalidRequestError):
        service.resolve_day_range(end - timedelta(days=service.MAX_DAYS), end)


# --------------------------------------------------------------------- pricing


def test_money_is_not_a_column_on_the_tier() -> None:
    """A price has a date; a tier does not.

    Holding a rate on `plans` would mean changing it rewrites what every past
    invoice was based on. `plan_prices` carries `effective_from`, so a new price
    is a new row and last month still prices at last month's rate.
    """
    priced = {
        column.name
        for column in Plan.__table__.columns
        if any(word in column.name for word in ("price", "cost", "rate", "amount", "currency"))
    }

    assert priced == set()
    assert "effective_from" in PlanPrice.__table__.columns


async def test_the_price_reported_is_the_one_in_force_on_the_last_day() -> None:
    """A rate that changed mid-range must not reprice the days before it."""
    store = FakeUsageStore(
        _ROWS,
        [PRO_PLAN],
        prices=[
            PlanPrice(
                plan_slug="pro",
                effective_from=date(2026, 1, 1),
                currency="USD",
                price_per_service_cents=1_000,
            )
        ],
    )

    response = await service.usage_for_organization(
        store,  # type: ignore[arg-type]
        organization=_organization(ORG_ID),
        from_day=FROM_DAY,
        to_day=TO_DAY,
    )

    assert response.price is not None
    assert response.price.price_per_service_cents == 1_000
    assert response.price.currency == "USD"


async def test_a_tier_with_no_price_reports_none_rather_than_zero() -> None:
    """Zero is a number a customer reads as free. Absent is the truth."""
    response = await _read(ORG_ID)

    assert response.price is None


# ---------------------------------------------------------- the billable count


def _service(org_id: uuid.UUID, project_id: uuid.UUID, day: date, name: str) -> UsageDailyService:
    return UsageDailyService(org_id=org_id, project_id=project_id, day=day, service_name=name)


async def _billed(org_id: uuid.UUID, services: list[UsageDailyService]) -> UsageResponse:
    store = FakeUsageStore(_ROWS, [PRO_PLAN], services=services)
    return await service.usage_for_organization(
        store,  # type: ignore[arg-type]
        organization=_organization(org_id),
        from_day=FROM_DAY,
        to_day=TO_DAY,
    )


async def test_the_range_total_asks_the_database_for_a_distinct_count() -> None:
    """The behaviour tests below cannot see this, and that is why it is here.

    They answer through a fake that dedupes in Python, so they stay green even
    if the statement stops asking for `distinct` — which is the whole bug. The
    statement is the only place the guarantee actually lives, so it is what gets
    asserted. Same technique as the tenant predicates above.
    """
    captured = CapturingSession()

    await queries.services_in_range(
        captured,  # type: ignore[arg-type]
        org_id=ORG_ID,
        from_day=FROM_DAY,
        to_day=TO_DAY,
    )

    statement = str(captured.statements[-1].compile())
    assert "DISTINCT" in statement.upper(), statement
    assert "service_name" in statement
    assert "project_id" in statement


async def test_a_service_reporting_every_day_is_billed_once_for_the_range() -> None:
    """The unit is distinct services, so summing the days would bill it daily."""
    every_day = [
        _service(ORG_ID, PROJECT_ID, date(2026, 8, day), "checkout") for day in range(10, 20)
    ]

    response = await _billed(ORG_ID, every_day)

    assert response.totals.services == 1


async def test_the_same_name_in_two_projects_is_two_services() -> None:
    """Two teams each running a `checkout` are running two of them."""
    response = await _billed(
        ORG_ID,
        [
            _service(ORG_ID, PROJECT_ID, date(2026, 8, 10), "checkout"),
            _service(ORG_ID, OTHER_PROJECT_ID, date(2026, 8, 10), "checkout"),
        ],
    )

    assert response.totals.services == 2


async def test_another_organizations_services_are_never_counted() -> None:
    response = await _billed(
        ORG_ID,
        [
            _service(ORG_ID, PROJECT_ID, date(2026, 8, 10), "checkout"),
            _service(OTHER_ORG_ID, PROJECT_ID, date(2026, 8, 10), "payments"),
            _service(OTHER_ORG_ID, PROJECT_ID, date(2026, 8, 10), "shipping"),
        ],
    )

    assert response.totals.services == 1
