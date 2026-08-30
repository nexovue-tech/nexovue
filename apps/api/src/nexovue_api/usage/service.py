"""Ingestion counters, the billable quantity, and the rate they meet.

No SQL text and no HTTP.

**The billable number.** ADR 0056 prices per service monitored; ADR 0077 makes
that countable, as `usage_daily_services` — one row per service a project sent
something for on a day. The count here is `count(*)` over that set, and the
range total is a union rather than a sum, because a service running all month is
one service that month.

**Why there is still no projected cost.** `plan_prices` now holds a rate, so the
old reason — that no price existed anywhere — is gone. Three things are still
missing, and each of them would have to be invented to produce a number a
customer would read as a bill:

- **the billing period.** A calendar month, or an anniversary of signup? The
  quantity differs by which, and so does the invoice.
- **which quantity the period bills.** The union of services over the period,
  the peak day, or an average. This service reports the union and the days it is
  made of; it does not choose between them.
- **the volume allowance per service**, which ADR 0056 requires and does not
  give a unit for — spans, bytes, or all signals together — and which is what
  overage is measured against.

The rate is therefore returned beside the quantity, and the arithmetic between
them waits for those three answers.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import Organization, PlanPrice, UsageDaily
from nexovue_api.errors.exceptions import InternalError, InvalidRequestError
from nexovue_api.usage import queries
from nexovue_api.usage.queries import MeteringFrontier
from nexovue_api.usage.schemas import (
    PlanPriceSummary,
    PlanSummary,
    ProjectDayUsage,
    UsageResponse,
    UsageTotals,
)

DEFAULT_DAYS: Final = 30

# Just past the longest retention any tier grants (395 days, ADR 0071), so every
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

    return UsageResponse(
        plan=await _plan_of(session, organization),
        price=_price_summary(
            await queries.find_effective_price(session, plan_slug=organization.plan, on_day=to_day)
        ),
        from_day=from_day,
        to_day=to_day,
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
        price_per_service_cents=price.price_per_service_cents,
        max_billed_services=price.max_billed_services,
    )


def _totals(rows: Sequence[UsageDaily], *, services: int) -> UsageTotals:
    """The counters add; the services were counted as a union by the database."""
    return UsageTotals(
        spans=sum(row.spans for row in rows),
        logs=sum(row.logs for row in rows),
        metric_points=sum(row.metric_points for row in rows),
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
        bytes_ingested=row.bytes,
        services=services_by_day.get((row.project_id, row.day), 0),
        settled=is_settled(row.day, frontier),
    )
