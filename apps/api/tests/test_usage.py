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
from nexovue_api.usage import billing, queries, service
from nexovue_api.usage.queries import MeteringFrontier
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
        return _bound_values(self.statements[-1])


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

    Day filtering follows the days a statement bound rather than the suite's
    range, because the billing period is a different window from the range being
    browsed and both are read in one call.
    """

    def __init__(
        self,
        rows: Sequence[UsageDaily],
        plans: Sequence[Plan],
        services: Sequence[UsageDailyService] = (),
        prices: Sequence[PlanPrice] = (),
        counted_through: datetime | None = None,
        unknown_partitions: int = 0,
    ) -> None:
        self._rows = rows
        self._plans = plans
        self._service_rows = list(services)
        self._prices = prices
        self._counted_through = counted_through
        self._unknown_partitions = unknown_partitions

    async def scalars(self, statement: Any) -> Any:
        return _Rows(self._counters(_Bound(statement)))

    async def scalar(self, statement: Any) -> Any:
        bound = _Bound(statement)
        match _reads(statement):
            case "plan_prices":
                return next((p for p in self._prices if p.plan_slug in bound.values), None)
            case "usage_daily":
                return sum(row.bytes for row in self._counters(bound))
            case "usage_daily_services":
                return len({(row.project_id, row.service_name) for row in self._services(bound)})
            case _:
                return next((plan for plan in self._plans if plan.slug in bound.values), None)

    async def execute(self, statement: Any) -> Any:
        if _reads(statement) == "usage_ingest_offsets":
            return _One((self._counted_through, self._unknown_partitions))

        bound = _Bound(statement)
        selected = set(statement.selected_columns.keys())
        if "recent" in selected:
            return _Rows(self._counts_by_project(bound))
        if selected == {"project_id", "service_name"}:
            return _Rows(self._names_by_project(bound))
        return _Rows(self._counts_by_day(bound))

    def _counts_by_day(self, bound: _Bound) -> list[_ServiceCount]:
        counts: dict[tuple[uuid.UUID, date], int] = {}
        for row in self._services(bound):
            counts[(row.project_id, row.day)] = counts.get((row.project_id, row.day), 0) + 1
        return [_ServiceCount(project, day, n) for (project, day), n in sorted(counts.items())]

    def _counts_by_project(self, bound: _Bound) -> list[_WindowCount]:
        """Two windows, split at the recent boundary the statement bound.

        The boundary is the second of the three days bound, in day order, which
        is what the query itself binds: `previous_from`, `recent_from`, `to`.
        """
        recent_from = bound.days[1]
        windows: dict[uuid.UUID, tuple[set[str], set[str]]] = {}
        for row in self._services(bound):
            recent, previous = windows.setdefault(row.project_id, (set(), set()))
            (recent if row.day >= recent_from else previous).add(row.service_name)
        return [
            _WindowCount(project_id, len(recent), len(previous))
            for project_id, (recent, previous) in sorted(windows.items())
        ]

    def _names_by_project(self, bound: _Bound) -> list[_ServiceName]:
        seen = {(row.project_id, row.service_name) for row in self._services(bound)}
        return [_ServiceName(project_id, name) for project_id, name in sorted(seen)]

    def _counters(self, bound: _Bound) -> list[UsageDaily]:
        return [row for row in self._rows if self._matches(row, bound)]

    def _services(self, bound: _Bound) -> list[UsageDailyService]:
        return [row for row in self._service_rows if self._matches(row, bound)]

    def _matches(self, row: Any, bound: _Bound) -> bool:
        return (
            row.org_id in bound.values
            and (row.project_id in bound.values or not self._narrowed(bound))
            and bound.covers(row.day)
        )

    def _narrowed(self, bound: _Bound) -> bool:
        return any(row.project_id in bound.values for row in self._rows)


class _Bound:
    """The values one statement bound, and the day window among them.

    Reading the window from the statement rather than assuming the suite's is
    what lets one fake answer the browsed range and the billing month in the
    same call — and a query that bound the wrong days answers with the wrong
    rows rather than being quietly corrected here.
    """

    def __init__(self, statement: Any) -> None:
        self.values = _bound_values(statement)
        self.days = sorted(value for value in self.values if isinstance(value, date))

    def covers(self, day: date) -> bool:
        return not self.days or self.days[0] <= day <= self.days[-1]


def _bound_values(statement: Any) -> set[Any]:
    """Every value a statement bound, with an `IN` list flattened into its members."""
    bound: set[Any] = set()
    for value in statement.compile().params.values():
        bound.update(value) if isinstance(value, list) else bound.add(value)
    return bound


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


class _WindowCount:
    """The shape `service_counts_by_project` returns: two windows, one project."""

    def __init__(self, project_id: uuid.UUID, recent: int, previous: int) -> None:
        self.project_id = project_id
        self.recent = recent
        self.previous = previous


class _ServiceName:
    """The shape `service_names` returns: one project, one name."""

    def __init__(self, project_id: uuid.UUID, service_name: str) -> None:
        self.project_id = project_id
        self.service_name = service_name


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
                price_per_service_minor_units=1_000,
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
    assert response.price.price_per_service_minor_units == 1_000
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


# ---------------------------------------------------- what is still being counted


def _frontier(counted_through: datetime | None, unknown: int = 0) -> MeteringFrontier:
    return MeteringFrontier(counted_through=counted_through, unknown_partitions=unknown)


def test_a_day_the_meter_has_not_finished_is_not_settled() -> None:
    """Half of a day counted is a number that will still grow."""
    midday = datetime(2026, 8, 10, 12, tzinfo=UTC)

    assert not service.is_settled(date(2026, 8, 10), _frontier(midday))


def test_a_day_the_meter_has_passed_the_end_of_is_settled() -> None:
    """Settled means counted past the last instant of the day, not "yesterday"."""
    midnight_after = datetime(2026, 8, 11, tzinfo=UTC)

    assert service.is_settled(date(2026, 8, 10), _frontier(midnight_after))
    assert not service.is_settled(date(2026, 8, 11), _frontier(midnight_after))


def test_an_unmetered_partition_leaves_every_day_unsettled() -> None:
    """One partition nobody has read could hold a whole day's traffic, however
    far ahead the others have run."""
    far_ahead = datetime(2027, 1, 1, tzinfo=UTC)

    assert not service.is_settled(date(2026, 8, 10), _frontier(far_ahead, unknown=1))


def test_nothing_metered_at_all_leaves_every_day_unsettled() -> None:
    assert not service.is_settled(date(2026, 8, 10), _frontier(None))


async def _metered(counted_through: datetime | None, unknown: int = 0) -> UsageResponse:
    store = FakeUsageStore(
        _ROWS, [PRO_PLAN], counted_through=counted_through, unknown_partitions=unknown
    )
    return await service.usage_for_organization(
        store,  # type: ignore[arg-type]
        organization=_organization(ORG_ID),
        from_day=FROM_DAY,
        to_day=TO_DAY,
    )


async def test_each_day_carries_whether_it_finished_being_counted() -> None:
    """The rows are 10 and 11 August; the meter stopped inside the 11th."""
    response = await _metered(datetime(2026, 8, 11, 6, tzinfo=UTC))

    assert [(day.day, day.settled) for day in response.days] == [
        (date(2026, 8, 10), True),
        (date(2026, 8, 11), False),
    ]


async def test_the_range_is_unsettled_even_when_every_day_it_lists_is_settled() -> None:
    """The days cannot speak for the days that have no row yet.

    Both rows are settled here and the range still has three weeks the meter has
    not reached — days that could add services to a total a customer would read
    as final. Deriving the range flag from the day list would report the
    opposite, which is why this is the test that would catch it.
    """
    response = await _metered(datetime(2026, 8, 12, tzinfo=UTC))

    assert all(day.settled for day in response.days)
    assert response.settled is False


async def test_the_range_is_settled_once_the_meter_has_passed_its_last_day() -> None:
    response = await _metered(datetime(2026, 9, 1, tzinfo=UTC))

    assert response.settled is True


async def test_an_unmetered_partition_leaves_the_range_unsettled() -> None:
    response = await _metered(datetime(2026, 9, 1, tzinfo=UTC), unknown=1)

    assert response.settled is False


# ------------------------------------------------------- the period, and its cost


def _price(
    *,
    minor_units: int = 1_000,
    ceiling: int | None = None,
    included_bytes: int | None = None,
) -> PlanPrice:
    return PlanPrice(
        plan_slug="pro",
        effective_from=date(2026, 1, 1),
        currency="USD",
        price_per_service_minor_units=minor_units,
        max_billed_services=ceiling,
        included_bytes_per_service=included_bytes,
    )


async def _priced(
    services: list[UsageDailyService],
    *,
    price: PlanPrice | None = None,
    from_day: date = FROM_DAY,
    to_day: date = TO_DAY,
    project_id: uuid.UUID | None = None,
) -> UsageResponse:
    store = FakeUsageStore(_ROWS, [PRO_PLAN], services=services, prices=[price] if price else ())
    return await service.usage_for_organization(
        store,  # type: ignore[arg-type]
        organization=_organization(ORG_ID),
        from_day=from_day,
        to_day=to_day,
        project_id=project_id,
    )


def test_the_billing_period_is_the_calendar_month() -> None:
    """Not the anniversary of signup: every customer's period starts together."""
    assert billing.calendar_month_of(date(2026, 8, 14)) == (date(2026, 8, 1), date(2026, 8, 31))
    assert billing.calendar_month_of(date(2026, 2, 3)) == (date(2026, 2, 1), date(2026, 2, 28))
    # A leap February is the one month a hardcoded length gets wrong.
    assert billing.calendar_month_of(date(2028, 2, 3)) == (date(2028, 2, 1), date(2028, 2, 29))


async def test_the_period_is_the_month_around_the_last_day_not_the_range() -> None:
    """The range is what is browsed; the month is what is billed."""
    response = await _priced([], from_day=date(2026, 8, 10), to_day=date(2026, 8, 20))

    assert (response.period.first_day, response.period.last_day) == (
        date(2026, 8, 1),
        date(2026, 8, 31),
    )


async def test_the_cost_is_the_months_services_and_not_the_ranges() -> None:
    """The negative control on the multiplication itself.

    `checkout` reported only before the browsed range starts, so the range
    counts one service and the month counts two. Multiplying the number the
    screen happens to be showing gives $10; the month's bill is $20.
    """
    response = await _priced(
        [
            _service(ORG_ID, PROJECT_ID, date(2026, 8, 2), "checkout"),
            _service(ORG_ID, PROJECT_ID, date(2026, 8, 20), "payments"),
        ],
        price=_price(),
        from_day=date(2026, 8, 10),
        to_day=date(2026, 8, 31),
    )

    assert response.totals.services == 1
    assert response.period.services == 2
    assert response.period.service_cost_minor_units == 2_000
    assert response.period.currency == "USD"


async def test_a_service_reporting_all_month_is_billed_once() -> None:
    """The union, in the period as well as in the range."""
    every_day = [
        _service(ORG_ID, PROJECT_ID, date(2026, 8, day), "checkout") for day in range(1, 29)
    ]

    response = await _priced(every_day, price=_price())

    assert response.period.services == 1
    assert response.period.service_cost_minor_units == 1_000


async def test_no_price_recorded_means_no_cost_and_never_a_zero() -> None:
    """A tier nobody priced is not a free tier."""
    response = await _priced([_service(ORG_ID, PROJECT_ID, TO_DAY, "checkout")])

    assert response.period.services == 1
    assert response.period.service_cost_minor_units is None
    assert response.period.currency is None


async def test_the_ceiling_holds_the_billed_quantity_down() -> None:
    """ADR 0056's cap is on the service-count component, and this is it."""
    many = [_service(ORG_ID, PROJECT_ID, TO_DAY, f"service-{index}") for index in range(10)]

    response = await _priced(many, price=_price(ceiling=4))

    assert response.period.services == 10
    assert response.period.billed_services == 4
    assert response.period.service_cost_minor_units == 4_000


async def test_an_uncapped_price_bills_every_service() -> None:
    """Null is uncapped, which is not a cap of zero."""
    many = [_service(ORG_ID, PROJECT_ID, TO_DAY, f"service-{index}") for index in range(3)]

    response = await _priced(many, price=_price(ceiling=None))

    assert response.period.billed_services == 3
    assert response.period.service_cost_minor_units == 3_000


async def test_a_month_the_meter_has_not_finished_is_not_settled() -> None:
    """A month in progress is labelled, never shown as a small bill."""
    store = FakeUsageStore(_ROWS, [PRO_PLAN], counted_through=datetime(2026, 8, 12, tzinfo=UTC))

    response = await service.usage_for_organization(
        store,  # type: ignore[arg-type]
        organization=_organization(ORG_ID),
        from_day=FROM_DAY,
        to_day=TO_DAY,
    )

    assert response.period.settled is False


async def test_a_month_the_meter_has_passed_is_settled() -> None:
    store = FakeUsageStore(_ROWS, [PRO_PLAN], counted_through=datetime(2026, 9, 1, tzinfo=UTC))

    response = await service.usage_for_organization(
        store,  # type: ignore[arg-type]
        organization=_organization(ORG_ID),
        from_day=FROM_DAY,
        to_day=TO_DAY,
    )

    assert response.period.settled is True


async def test_narrowing_to_one_project_does_not_narrow_the_bill() -> None:
    """A per-project cost is a number nobody is ever charged."""
    services = [
        _service(ORG_ID, PROJECT_ID, TO_DAY, "checkout"),
        _service(ORG_ID, OTHER_PROJECT_ID, TO_DAY, "payments"),
    ]

    whole = await _priced(services, price=_price())
    narrowed = await _priced(services, price=_price(), project_id=PROJECT_ID)

    assert narrowed.totals.services == 1
    assert narrowed.period.services == whole.period.services == 2
    assert narrowed.period.service_cost_minor_units == 2_000
    assert narrowed.period.bytes_ingested == whole.period.bytes_ingested


# ------------------------------------------------------------- the allowance


async def test_the_allowance_is_bytes_pooled_across_the_billed_services() -> None:
    """Per service, summed: a quiet service lends its allowance to a busy one."""
    services = [
        _service(ORG_ID, PROJECT_ID, TO_DAY, "checkout"),
        _service(ORG_ID, PROJECT_ID, TO_DAY, "payments"),
    ]

    response = await _priced(services, price=_price(included_bytes=1_000_000))

    assert response.period.included_bytes == 2_000_000
    # The month's rows are the two in `_ROWS`, which is what it is measured against.
    assert response.period.bytes_ingested == 1_500


async def test_no_allowance_recorded_is_not_an_allowance_of_zero() -> None:
    """A null would otherwise report every byte ingested as overage."""
    response = await _priced(
        [_service(ORG_ID, PROJECT_ID, TO_DAY, "checkout")], price=_price(included_bytes=None)
    )

    assert response.period.included_bytes is None
    assert response.period.bytes_ingested == 1_500


async def test_the_allowance_follows_the_ceiling_not_the_raw_count() -> None:
    """Services past the cap are not billed, so they bring no allowance either."""
    many = [_service(ORG_ID, PROJECT_ID, TO_DAY, f"service-{index}") for index in range(10)]

    response = await _priced(many, price=_price(ceiling=4, included_bytes=1_000))

    assert response.period.included_bytes == 4_000


async def test_the_period_bytes_are_asked_for_by_organization_and_month(
    session: CapturingSession,
) -> None:
    """The behaviour test above cannot see the predicate; this can."""
    await queries.bytes_in_range(
        session,  # type: ignore[arg-type]
        org_id=ORG_ID,
        from_day=FROM_DAY,
        to_day=TO_DAY,
    )

    assert {ORG_ID, FROM_DAY, TO_DAY} <= session.bound_values
    assert OTHER_ORG_ID not in session.bound_values


# -------------------------------------------------------------- cardinality


class _CapturingExecute(CapturingSession):
    """A capturing session that also answers `execute`, returning nothing."""

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        return _Empty()


async def test_the_windowed_counts_ask_the_database_for_a_distinct_count() -> None:
    """The fake below dedupes with a Python set, so only the statement shows this.

    Without `distinct` a service reporting on seven days counts as seven, and a
    project running steadily would look like a project exploding.
    """
    captured = _CapturingExecute()

    await queries.service_counts_by_project(
        captured,  # type: ignore[arg-type]
        org_id=ORG_ID,
        previous_from=date(2026, 8, 17),
        recent_from=date(2026, 8, 24),
        to_day=TO_DAY,
    )

    statement = str(captured.statements[-1].compile())
    assert "DISTINCT" in statement.upper(), statement
    assert ORG_ID in captured.bound_values


async def test_reading_service_names_binds_the_organization(session: CapturingSession) -> None:
    """Project ids narrow; they never replace the tenant predicate."""
    captured = _CapturingExecute()

    await queries.service_names(
        captured,  # type: ignore[arg-type]
        org_id=ORG_ID,
        project_ids=[PROJECT_ID],
        from_day=FROM_DAY,
        to_day=TO_DAY,
        limit_per_project=10,
    )

    bound = captured.bound_values
    assert ORG_ID in bound
    assert OTHER_ORG_ID not in bound


def _pods(count: int, day: date = TO_DAY) -> list[UsageDailyService]:
    """One deployment, one service name per pod — the bill this warns about."""
    # Every suffix is spellable in the generator's own alphabet, which has no
    # vowels and no 0, 1 or 3 in it.
    suffixes = ["x2k9p", "m4rt7", "bn6qw", "kt9zx", "vd4sr", "hj7bk", "cz2mn", "wq8fd"]
    return [
        _service(ORG_ID, PROJECT_ID, day, f"checkout-7d9f8b6c4d-{suffix}")
        for suffix in suffixes[:count]
    ]


async def test_a_project_whose_names_carry_pod_ids_is_named_with_the_reason() -> None:
    recent = TO_DAY - timedelta(days=2)
    response = await _priced(_pods(8, day=recent))

    assert len(response.cardinality) == 1
    warning = response.cardinality[0]
    assert warning.project_id == PROJECT_ID
    assert warning.services_now == 8
    assert warning.window_days == 7
    assert [family.kind for family in warning.families] == ["kubernetes-pod"]
    assert warning.families[0].stem == "checkout"
    assert warning.families[0].members == 8
    assert len(warning.families[0].examples) == 3


async def test_the_change_is_reported_against_the_week_before() -> None:
    """'Twelve services, up from three' — the sentence needs both numbers."""
    last_week = TO_DAY - timedelta(days=9)
    recent = TO_DAY - timedelta(days=1)
    services = [
        _service(ORG_ID, PROJECT_ID, last_week, "checkout"),
        _service(ORG_ID, PROJECT_ID, last_week, "payments"),
        *_pods(8, day=recent),
    ]

    response = await _priced(services)

    assert (response.cardinality[0].services_now, response.cardinality[0].services_before) == (8, 2)


async def test_a_project_running_ordinary_services_is_never_warned_about() -> None:
    """Ten honestly named services are ten services, and silence is the answer."""
    names = [
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
    ]
    services = [_service(ORG_ID, PROJECT_ID, TO_DAY, name) for name in names]

    response = await _priced(services)

    assert response.cardinality == []


async def test_a_small_project_is_left_alone_however_it_moved() -> None:
    """Three services where there was one is a customer, not an explosion."""
    services = [
        _service(ORG_ID, PROJECT_ID, TO_DAY, name) for name in ("checkout", "payments", "search")
    ]

    response = await _priced(services)

    assert response.cardinality == []


async def test_another_organizations_names_are_never_examined() -> None:
    response = await _priced(
        [
            _service(OTHER_ORG_ID, PROJECT_ID, TO_DAY, f"checkout-7d9f8b6c4d-{suffix}")
            for suffix in ("x2k9p", "m4rt7", "bn6qw", "kt9zx", "vd4sr", "hj7bk", "cz2mn", "wq8fd")
        ]
    )

    assert response.cardinality == []
