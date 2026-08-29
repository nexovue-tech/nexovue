"""Ingestion counters for one organization, and the tier they are measured against.

No SQL text and no HTTP.

**Why there is no projected cost, and what would be needed to add one.**

ADR 0056 (per-service pricing) fixed the billing unit as the service, with a
volume allowance per service and overage above it. ADR 0071 (plan tiers and
retention) then decided the four tiers and their retention, and its own
arithmetic was re-checked against measurements on 2026-08-29: the log-byte
estimate it rested on came in **3.5x too high**, and it says plainly that the
volume mix is still unmeasured and stays open into Phase 2.

The `plans` table records that decision faithfully — `slug`, `name`,
`raw_retention_days`, `derived_retention_days` — and holds **no price of any
kind**: no per-service rate, no included volume, no overage rate, no currency.
So the projection has no inputs. Hardcoding one here would put a number a
customer reads as a bill into a Python constant, derived from an ADR that says
its own largest term is an unmeasured estimate.

The counters are therefore served on their own. Cost arrives when a rate is a
row in the database — a price column on `plans` or a `plan_prices` table — and
then it is computed here from that row.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import Organization, UsageDaily
from nexovue_api.errors.exceptions import InternalError, InvalidRequestError
from nexovue_api.usage import queries
from nexovue_api.usage.schemas import (
    PlanSummary,
    ProjectDayUsage,
    UsageCounters,
    UsageResponse,
)

DEFAULT_DAYS: Final = 30

# Just past the longest retention any tier grants (395 days, ADR 0071), so every
# window that could still hold data is askable and nothing beyond it is.
MAX_DAYS: Final = 400

RANGE_BACKWARDS: Final = "`from` must not be later than `to`."
RANGE_TOO_LONG: Final = f"A usage range may span at most {MAX_DAYS} days."


async def usage_for_organization(
    session: AsyncSession,
    *,
    organization: Organization,
    from_day: date,
    to_day: date,
    project_id: uuid.UUID | None = None,
) -> UsageResponse:
    """Daily counters and their totals, for one organization or one of its projects."""
    rows = await queries.daily_usage(
        session,
        org_id=organization.id,
        from_day=from_day,
        to_day=to_day,
        project_id=project_id,
    )
    return UsageResponse(
        plan=await _plan_of(session, organization),
        from_day=from_day,
        to_day=to_day,
        totals=_totals(rows),
        days=[_to_day_usage(row) for row in rows],
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


def _totals(rows: Sequence[UsageDaily]) -> UsageCounters:
    return UsageCounters(
        spans=sum(row.spans for row in rows),
        logs=sum(row.logs for row in rows),
        metric_points=sum(row.metric_points for row in rows),
        bytes_ingested=sum(row.bytes for row in rows),
    )


def _to_day_usage(row: UsageDaily) -> ProjectDayUsage:
    return ProjectDayUsage(
        project_id=row.project_id,
        day=row.day,
        spans=row.spans,
        logs=row.logs,
        metric_points=row.metric_points,
        bytes_ingested=row.bytes,
    )
