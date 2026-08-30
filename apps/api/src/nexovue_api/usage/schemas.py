"""What the usage endpoint puts on the wire."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field


class UsageCounters(BaseModel):
    """What was ingested. Four counters, in the units they are metered in.

    The guardrail meter, not the bill: ADR 0056 prices per service, and these
    are the volume behind that price.

    `bytes_ingested` rather than `bytes`: the column is `usage_daily.bytes`, but
    a field of that name shadows a builtin everywhere it is unpacked.
    """

    spans: int = 0
    logs: int = 0
    metric_points: int = 0
    bytes_ingested: int = 0


class ProjectDayUsage(UsageCounters):
    """One project's counters for one day, and the day's billable quantity."""

    project_id: uuid.UUID
    day: date

    # ADR 0056's unit: distinct `service.name` this project sent something for
    # on this day.
    services: int = 0

    # False while the meter has not yet counted past the end of this day, so a
    # number still growing is never read as a final one. See `service`.
    settled: bool = False


class UsageTotals(UsageCounters):
    """The range's totals.

    The counters add. `services` does not: it is the distinct services seen
    across the whole range, so a service running every day counts once. Summing
    the daily numbers would bill it once per day.
    """

    services: int = 0


class PlanSummary(BaseModel):
    """The tier this organization is on, and what it grants (ADR 0071).

    Retention is here because it is the plan attribute a usage screen needs
    beside the counters: how long what was ingested will still be readable.
    """

    slug: str
    name: str
    raw_retention_days: int
    derived_retention_days: int


class PlanPriceSummary(BaseModel):
    """The price in force at the end of the range being read.

    Null on the response when the tier has no price row yet, which is what the
    table ships as — ADR 0056 fixed the unit and not the amounts.
    """

    effective_from: date
    currency: str
    price_per_service_cents: int
    # Null is uncapped. ADR 0056's ceiling applies to the service-count
    # component of the bill.
    max_billed_services: int | None = None


class UsageResponse(BaseModel):
    """Ingestion counters and billable services for one organization.

    **There is still no cost field**, and now for a narrower reason than before:
    the rate is a row rather than a missing column, but what a period costs also
    needs the billing period and the volume allowance, neither of which is
    decided. See `service`.
    """

    plan: PlanSummary
    # The rate that applied on `to_day`. Null until a price row exists.
    price: PlanPriceSummary | None = None
    from_day: date
    to_day: date
    # False while any part of the range could still be counted. Distinct from
    # the per-day flag, which can only speak for the days that already have a
    # row: a day the meter has not reached yet is absent from `days` entirely,
    # so nothing in that list can say the totals have stopped growing.
    settled: bool = False
    totals: UsageTotals
    # Day order, then project. A day with no ingestion is absent rather than
    # zero-filled: nothing writes a zero row, so a gap means "no data recorded",
    # which is not the same claim as "nothing was ingested".
    days: list[ProjectDayUsage] = Field(default_factory=list)
