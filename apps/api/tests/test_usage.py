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

from nexovue_api.data.postgres.models import Organization, Plan, UsageDaily
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

    def __init__(self, rows: Sequence[UsageDaily], plans: Sequence[Plan]) -> None:
        self._rows = rows
        self._plans = plans

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
        return next((plan for plan in self._plans if plan.slug in bound), None)

    def _narrowed(self, bound: set[Any]) -> bool:
        return any(row.project_id in bound for row in self._rows)


class _Rows:
    def __init__(self, rows: list[UsageDaily]) -> None:
        self._rows = rows

    def all(self) -> list[UsageDaily]:
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


def test_the_response_carries_no_cost_because_no_price_is_stored() -> None:
    """`plans` holds retention and nothing about money — no rate, no included
    volume, no currency. Any figure here would be one this service invented, and
    a customer reads it as a bill. When a price becomes a column, this test is
    what fails and says to add the projection."""
    priced = {
        column.name
        for column in Plan.__table__.columns
        if any(word in column.name for word in ("price", "cost", "rate", "amount", "currency"))
    }

    assert priced == set()
    assert not any(
        field.startswith(("cost", "projected", "price")) for field in UsageResponse.model_fields
    )
