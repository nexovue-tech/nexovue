"""The period a bill covers, and the arithmetic between a rate and a quantity.

Pure functions. No SQL, no HTTP, no clock — every input is passed in, so every
rule below is testable without a database.

**The period is the calendar month.** Not the anniversary of signup: with
anniversaries every customer is mid-period at a different moment, every
proration question is bespoke, and no two usage screens are comparable. It is
also what the field does — Datadog "calculates fees based on product usage
during each calendar month".

**The quantity is the union of distinct services over that month.** A service
running all month is one service, which is what `queries.services_in_range`
already computes. Not a peak day and not a high-water mark: Datadog bills hosts
on a high-water mark of the lower 99% of hourly readings, and even with that
softening the standing complaint is that a few hours of scaling up sets the
whole month's bill. Predictable beats optimal for a first product.

**The allowance is ingested bytes.** Bytes are the only unit spanning traces,
logs and metrics, and they are what costs storage and transfer. Counting spans
would charge a well-instrumented service more for being well instrumented.

**What is still not computed.** The allowance amount is a row nobody has
inserted, and what happens above it — billed as overage, or blocked and said —
is not decided: ADR 0056 promises both in different paragraphs. So the cost
here is the service-count component only, and it says so.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import NamedTuple

from nexovue_api.data.postgres.models import PlanPrice


class BillingPeriod(NamedTuple):
    """The calendar month a day falls in, both ends included."""

    first_day: date
    last_day: date


def calendar_month_of(day: date) -> BillingPeriod:
    """The billing period containing a day."""
    _, days_in_month = calendar.monthrange(day.year, day.month)
    return BillingPeriod(day.replace(day=1), day.replace(day=days_in_month))


def billed_services(services: int, price: PlanPrice | None) -> int:
    """The quantity actually charged: the union, held down by the ceiling.

    ADR 0056's cap applies to the service-count component of the bill. A null
    ceiling is uncapped, which is not a ceiling of zero — without the price row
    there is no ceiling to apply and the quantity stands as counted.
    """
    if price is None or price.max_billed_services is None:
        return services
    return min(services, price.max_billed_services)


def service_cost_minor_units(services: int, price: PlanPrice | None) -> int | None:
    """What the period's services cost, or None when no rate is recorded.

    None, never zero: an unpriced tier is not a free one, and the absence is
    carried all the way to the browser rather than collapsing into a number a
    customer would read as their bill.
    """
    if price is None:
        return None
    return billed_services(services, price) * price.price_per_service_minor_units


def included_bytes(services: int, price: PlanPrice | None) -> int | None:
    """The month's volume allowance: what each billed service brings, pooled.

    Pooled across the organization rather than enforced service by service,
    which is what the field does — Datadog's per-host ingestion allowance is
    "summed over all the APM hosts", so a quiet service lends its unused
    allowance to a busy one. Enforcing it per service would need bytes
    attributed to each service, and a payload carrying several services would
    have to have its bytes apportioned between them by a rule nobody has
    chosen.

    None when no rate is recorded or the rate records no allowance. An
    allowance nobody set is not an allowance of zero, which would report every
    byte as overage.
    """
    if price is None or price.included_bytes_per_service is None:
        return None
    return billed_services(services, price) * price.included_bytes_per_service
