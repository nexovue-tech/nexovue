"""Ingestion counters, the billable quantity, and what they cost this month.

No SQL text and no HTTP.

**The billable number.** Pricing is per service monitored; `usage_daily_services`
makes that countable — one row per service a project sent something for on a
day. The count here is `count(*)` over that set, and the range total is a union
rather than a sum, because a service running all month is one service that
month.

**The cost.** The period is the calendar month, the quantity is the union of
services over it, and the rate is the row in force — so the cost is one
multiplication, and `billing` is where it happens. Four refusals survive it:

- **no price recorded, no cost shown.** Never a zero: an unpriced tier is not a
  free one, and the absence is carried to the browser as an absence.
- **a month still being metered is labelled**, not quietly shown as a small
  number. The frontier says which, exactly as it does per day.
- **the cost is the service-count component only.** Volume above the allowance
  is not in it, because neither the allowance amount nor what happens above it
  is decided — the pricing model promises "block and say so" in one paragraph
  and "stays live, so growth still pays" in another.
- **it is the organization's**, even when the day table is narrowed to one
  project. A narrowed cost is a number nobody is ever charged.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import Organization, PlanPrice, UsageDaily
from nexovue_api.errors.exceptions import InternalError, InvalidRequestError
from nexovue_api.usage import billing, cardinality, queries
from nexovue_api.usage.cardinality import ProjectServiceCounts
from nexovue_api.usage.queries import MeteringFrontier
from nexovue_api.usage.schemas import (
    BillingPeriodUsage,
    CardinalityWarning,
    PlanPriceSummary,
    PlanSummary,
    ProjectDayUsage,
    ServiceNameFamily,
    UsageResponse,
    UsageTotals,
)

DEFAULT_DAYS: Final = 30

# Just past the longest retention any tier grants (395 days), so every
# window that could still hold data is askable and nothing beyond it is.
MAX_DAYS: Final = 400

RANGE_BACKWARDS: Final = "`from` must not be later than `to`."
RANGE_TOO_LONG: Final = f"A usage range may span at most {MAX_DAYS} days."

# One project-day, as the two reads key it.
_ProjectDay = tuple[uuid.UUID, date]


async def usage_for_organization(
    session: AsyncSession,
    *,
    organization: Organization,
    from_day: date,
    to_day: date,
    project_id: uuid.UUID | None = None,
) -> UsageResponse:
    """Daily counters and billable services, for one organization or one project."""
    rows = await queries.daily_usage(
        session,
        org_id=organization.id,
        from_day=from_day,
        to_day=to_day,
        project_id=project_id,
    )
    counted = await queries.daily_service_counts(
        session,
        org_id=organization.id,
        from_day=from_day,
        to_day=to_day,
        project_id=project_id,
    )
    services_by_day: dict[_ProjectDay, int] = {
        (row.project_id, row.day): row.services for row in counted
    }
    frontier = await queries.metering_frontier(session)
    # The rate in force on the day being read, not on the month's last day: a
    # repricing that starts later this month must not price a month that has
    # not reached it.
    # The currency this organization is billed in, or the default before it has
    # ever paid. The screen states figures in one currency and has to pick it.
    currency = (
        await queries.billing_currency(session, org_id=organization.id)
        or queries.DEFAULT_BILLING_CURRENCY
    )
    price = await queries.find_effective_price(
        session, plan_slug=organization.plan, currency=currency, on_day=to_day
    )

    return UsageResponse(
        plan=await _plan_of(session, organization),
        price=_price_summary(price),
        period=await _billing_period(
            session, org_id=organization.id, to_day=to_day, price=price, frontier=frontier
        ),
        cardinality=await _cardinality_warnings(session, org_id=organization.id, to_day=to_day),
        from_day=from_day,
        to_day=to_day,
        # The frontier only moves forwards, so the whole range is finished
        # exactly when its last day is — and while it is not, a day the meter
        # has not reached has no row to carry the caveat itself.
        settled=is_settled(to_day, frontier),
        totals=_totals(
            rows,
            services=await queries.services_in_range(
                session,
                org_id=organization.id,
                from_day=from_day,
                to_day=to_day,
                project_id=project_id,
            ),
        ),
        days=[_to_day_usage(row, services_by_day, frontier) for row in rows],
    )


def resolve_day_range(from_day: date | None, to_day: date | None) -> tuple[date, date]:
    """The requested range of days, defaulting to the last thirty, ends included."""
    end = to_day or datetime.now(UTC).date()
    start = from_day or end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise InvalidRequestError(RANGE_BACKWARDS)
    if (end - start).days >= MAX_DAYS:
        raise InvalidRequestError(RANGE_TOO_LONG)
    return start, end


def is_settled(day: date, frontier: MeteringFrontier) -> bool:
    """Whether a day has finished being counted.

    Not "is it yesterday". The meter reads Kafka at its own pace, so a day is
    finished only once every partition has been counted past its last instant —
    a meter that spent the night catching up is still filling in yesterday. A
    partition nothing has metered leaves the answer unknown, and unknown is
    reported as not settled: a number that may still grow must never be
    presented as one a bill could be raised on.
    """
    if frontier.unknown_partitions > 0 or frontier.counted_through is None:
        return False
    ends = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    return frontier.counted_through >= ends


async def _plan_of(session: AsyncSession, organization: Organization) -> PlanSummary:
    """The tier this organization is on.

    `organizations.plan` is a non-null foreign key onto `plans.slug` with
    `ON DELETE RESTRICT`, so a missing row is a broken database rather than a
    caller's mistake — reported as one, not papered over with a default tier
    that would misstate the customer's retention.
    """
    plan = await queries.find_plan(session, slug=organization.plan)
    if plan is None:
        raise InternalError()
    return PlanSummary(
        slug=plan.slug,
        name=plan.name,
        raw_retention_days=plan.raw_retention_days,
        derived_retention_days=plan.derived_retention_days,
    )


def _price_summary(price: PlanPrice | None) -> PlanPriceSummary | None:
    """The rate, or nothing at all. A missing price is reported as missing."""
    if price is None:
        return None
    return PlanPriceSummary(
        effective_from=price.effective_from,
        currency=price.currency,
        price_per_service_minor_units=price.price_per_service_minor_units,
        max_billed_services=price.max_billed_services,
        included_bytes_per_service=price.included_bytes_per_service,
        browser_share_percent=price.browser_share_percent,
    )


async def _billing_period(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    to_day: date,
    price: PlanPrice | None,
    frontier: MeteringFrontier,
) -> BillingPeriodUsage:
    """The calendar month around `to_day`, and what it has come to.

    Read for the whole organization on purpose: the bill is the organization's,
    so narrowing the day table below must not narrow this.
    """
    period = billing.calendar_month_of(to_day)
    services = await queries.services_in_range(
        session, org_id=org_id, from_day=period.first_day, to_day=period.last_day
    )
    ingested = await queries.bytes_in_range(
        session, org_id=org_id, from_day=period.first_day, to_day=period.last_day
    )
    cost = billing.service_cost_minor_units(services, price)
    # What sizes the allowance on a paid tier — the plan's own ceiling sizes it
    # on the free one, and then this is unused. G6a.
    services_committed = await queries.active_services_committed(session, org_id=org_id)

    return BillingPeriodUsage(
        first_day=period.first_day,
        last_day=period.last_day,
        # A month is finished only when the meter has passed its last instant,
        # which is never true of the month in progress.
        settled=is_settled(period.last_day, frontier),
        services=services,
        billed_services=billing.billed_services(services, price),
        bytes_ingested=ingested.total,
        browser_bytes_ingested=ingested.browser,
        service_cost_minor_units=cost,
        # The currency belongs to the amount; without one there is nothing to
        # denominate.
        currency=price.currency if cost is not None and price is not None else None,
        included_bytes=billing.included_bytes(price, services_committed),
        # Null means the row records no split, and the whole is what every door
        # spends. See `billing.browser_cap_bytes`.
        browser_included_bytes=billing.browser_cap_bytes(price, services_committed),
        allowance_enforced=billing.allowance_is_enforced(price, services_committed),
    )


async def _cardinality_warnings(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    to_day: date,
) -> list[CardinalityWarning]:
    """Projects whose service count jumped, and the name families behind it.

    Two reads, and the second only for projects big enough to be the cause. A
    warning is emitted when the count jumped or when a family was found — the
    jump is the change worth showing, the family is the reason, and neither
    implies the other.
    """
    window = timedelta(days=cardinality.RECENT_WINDOW_DAYS)
    recent_from = to_day - window + timedelta(days=1)
    counted = await queries.service_counts_by_project(
        session,
        org_id=org_id,
        previous_from=recent_from - window,
        recent_from=recent_from,
        to_day=to_day,
    )
    counts = [
        ProjectServiceCounts(project_id=row.project_id, recent=row.recent, previous=row.previous)
        for row in counted
    ]

    examined = [count for count in counts if cardinality.worth_examining(count)]
    names = await queries.service_names(
        session,
        org_id=org_id,
        project_ids=[count.project_id for count in examined],
        from_day=recent_from,
        to_day=to_day,
        limit_per_project=cardinality.MAX_NAMES_EXAMINED,
    )
    by_project: dict[uuid.UUID, list[str]] = {}
    for row in names:
        by_project.setdefault(row.project_id, []).append(row.service_name)

    warnings = []
    for count in examined:
        families = cardinality.identifier_families(by_project.get(count.project_id, []))
        if not families and not cardinality.has_jumped(count):
            continue
        warnings.append(
            CardinalityWarning(
                project_id=count.project_id,
                services_now=count.recent,
                services_before=count.previous,
                window_days=cardinality.RECENT_WINDOW_DAYS,
                families=[_family_summary(family) for family in families],
            )
        )
    return warnings


def _family_summary(family: cardinality.ServiceFamily) -> ServiceNameFamily:
    return ServiceNameFamily(
        stem=family.stem,
        kind=family.kind,
        members=len(family.members),
        examples=list(cardinality.examples_of(family)),
    )


def _totals(rows: Sequence[UsageDaily], *, services: int) -> UsageTotals:
    """The counters add; the services were counted as a union by the database."""
    return UsageTotals(
        spans=sum(row.spans for row in rows),
        logs=sum(row.logs for row in rows),
        metric_points=sum(row.metric_points for row in rows),
        profile_samples=sum(row.profile_samples for row in rows),
        bytes_ingested=sum(row.bytes for row in rows),
        services=services,
    )


def _to_day_usage(
    row: UsageDaily,
    services_by_day: dict[_ProjectDay, int],
    frontier: MeteringFrontier,
) -> ProjectDayUsage:
    return ProjectDayUsage(
        project_id=row.project_id,
        day=row.day,
        spans=row.spans,
        logs=row.logs,
        metric_points=row.metric_points,
        profile_samples=row.profile_samples,
        bytes_ingested=row.bytes,
        services=services_by_day.get((row.project_id, row.day), 0),
        settled=is_settled(row.day, frontier),
    )
