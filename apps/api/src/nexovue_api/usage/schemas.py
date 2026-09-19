"""What the usage endpoint puts on the wire."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field


class UsageCounters(BaseModel):
    """What was ingested. Five counters, in the units they are metered in.

    The guardrail meter, not the bill: pricing is per service, and these
    are the volume behind that price. Profile samples are shown and not
    billed while profiling is in preview (F17, D1).

    `bytes_ingested` rather than `bytes`: the column is `usage_daily.bytes`, but
    a field of that name shadows a builtin everywhere it is unpacked.
    """

    spans: int = 0
    logs: int = 0
    metric_points: int = 0
    profile_samples: int = 0
    bytes_ingested: int = 0


class ProjectDayUsage(UsageCounters):
    """One project's counters for one day, and the day's billable quantity."""

    project_id: uuid.UUID
    day: date

    # The billing unit: distinct `service.name` this project sent something for
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
    """The tier this organization is on, and what it grants.

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
    table ships as — the unit is fixed and the amounts are not.
    """

    effective_from: date
    currency: str
    # Minor units of `currency`, and named as such: the yen's minor unit is the
    # yen, so anything dividing by a hardcoded hundred misquotes a yen bill.
    price_per_service_minor_units: int
    # Null is uncapped. The ceiling applies to the service-count
    # component of the bill.
    max_billed_services: int | None = None
    # Ingested bytes each billed service brings, per calendar month, pooled
    # across the organization. Null is "no allowance recorded", which is not an
    # allowance of zero.
    included_bytes_per_service: int | None = None
    # How much of that allowance the browser door may spend, as a whole
    # percentage; the server door has the rest. Null is "no split
    # recorded": both doors spend the one allowance.
    browser_share_percent: int | None = None


class BillingPeriodUsage(BaseModel):
    """The calendar month the bill covers, and what it comes to so far.

    Always the whole organization, even when the request narrowed the day table
    to one project: the bill is the organization's, and a narrowed cost would be
    a number nobody is ever charged.
    """

    first_day: date
    last_day: date

    # False until the meter has counted past the last instant of the month, so
    # a month in progress is labelled rather than read as a small bill.
    settled: bool = False

    # The union of distinct services over the month — a service running all
    # month is one service.
    services: int = 0
    # The same number after the plan's ceiling. Equal to `services` when the
    # tier is uncapped or unpriced.
    billed_services: int = 0
    bytes_ingested: int = 0

    # The service-count component of the bill, in minor units of `currency`.
    # Null when no price is recorded: absent, never zero, because zero reads as
    # free.
    service_cost_minor_units: int | None = None
    currency: str | None = None

    # The month's pooled volume allowance in bytes, null when the rate records
    # none. Overage above it is deliberately not priced — see `service`.
    included_bytes: int | None = None

    # The browser door's cap and what it has spent of it. The cap is
    # null where the plan row records no split, which is when every door spends
    # `included_bytes` itself.
    #
    # A cap, not a share: the server door's limit is `included_bytes`, the whole
    # month, and there is deliberately no second figure beside it. What the
    # browser has spent comes out of the whole as it is spent, so an unused cap
    # is still the server's to use — which is why a `server_included_bytes`
    # field would be a number that changes with usage rather than a limit, and
    # is not sent.
    #
    # `browser_bytes_ingested` is a part of `bytes_ingested`, never a figure to
    # add to it: the server door's spend is the difference between the two.
    #
    # Sent because the door applies it: a screen computing the cap itself would
    # be a second place for the policy to be wrong — the same reason
    # `allowance_enforced` is sent rather than derived.
    browser_bytes_ingested: int = 0
    browser_included_bytes: int | None = None

    # True when going past `included_bytes` refuses further ingest for the rest
    # of the month, which is the free tier and nothing else. The API
    # decides this rather than the browser: which tiers are capped is policy,
    # and a screen that worked it out from the price row would be a second place
    # for it to be wrong.
    allowance_enforced: bool = False


class ServiceNameFamily(BaseModel):
    """Service names differing only by a trailing identifier."""

    # The name they would share if the identifier were moved off it.
    stem: str
    # Which shape the identifiers matched: a uuid, a hex run, a Kubernetes pod
    # suffix, or an ordinal.
    kind: str
    members: int
    examples: list[str] = Field(default_factory=list)


class CardinalityWarning(BaseModel):
    """A project whose distinct service count jumped, and why it may have.

    The billing unit is distinct service names, so a name carrying a pod id
    bills once per pod. Nothing is capped on the strength of this: dropping a
    customer's telemetry to hold their bill down would leave them debugging an
    outage without it.
    """

    project_id: uuid.UUID
    # Distinct services in the last `window_days`, and in the equal window
    # before it.
    services_now: int
    services_before: int
    window_days: int
    # Empty when no family was found. A count that jumped for an honest reason
    # says so by having nothing here — a wrong accusation is worse than silence.
    families: list[ServiceNameFamily] = Field(default_factory=list)


class UsageResponse(BaseModel):
    """Ingestion counters, billable services, and what the month costs so far.

    The cost is the service-count component only: the union of services over the
    calendar month, held down by the plan's ceiling, at the recorded rate. What
    volume above the allowance costs is not in it, because neither the allowance
    amount nor what happens above it has been decided. See `service`.
    """

    plan: PlanSummary
    # The rate that applied on `to_day`. Null until a price row exists.
    price: PlanPriceSummary | None = None
    # The calendar month containing `to_day` — the period a bill covers,
    # whatever range of days the caller asked to look at.
    period: BillingPeriodUsage
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
    # Projects whose service count jumped, and any name families behind it.
    # Empty for almost every organization, which is the intended state.
    cardinality: list[CardinalityWarning] = Field(default_factory=list)
