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

**What happens above the allowance is settled for the free tier only.** ADR 0056
promised both "block and say so rather than bill" and "overage stays live, so
growth still pays", in different paragraphs. ADR 0093 chooses the first where the
plan caps the services it bills — the free tier — and leaves the second open for
the tiers that pay. So the cost here is still the service-count component only,
and it says so.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Final, NamedTuple

from nexovue_api.data.postgres.models import PlanPrice

# The denominator `plan_prices.browser_share_percent` is written in: the column
# holds a whole-number percentage. `allowance.percentWhole` in the receiver is
# the same constant, and the two have to stay the same.
PERCENT_WHOLE: Final = 100


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


def allowance_service_count(price: PlanPrice | None, services_committed: int | None) -> int | None:
    """The count the month's allowance is sized on, or None when nothing sizes it.

    **This is the rule, and `allowance.allowanceServiceCount` in the receiver is
    the same rule in Go. The two have to agree or a customer is refused at a
    limit this screen never showed them.**

    A plan that caps the services it bills sets the count itself. A plan with no
    cap — every paying tier — is sized on what the customer committed to when
    they authorised the mandate, which is ADR 0107's ceiling: they chose it, they
    can see it, and they raise it themselves.

    None enforces nothing: an unpriced tier, or a paid tier with no active
    subscription. Deliberately not zero, which is a plan that includes nothing.
    """
    if price is None:
        return None
    if price.max_billed_services is not None:
        return price.max_billed_services
    return services_committed


def included_bytes(price: PlanPrice | None, services_committed: int | None) -> int | None:
    """The month's volume allowance, pooled across the organization.

    Pooled rather than enforced service by service, which is what the field
    does — Datadog's per-host ingestion allowance is "summed over all the APM
    hosts", so a quiet service lends its unused allowance to a busy one.
    Enforcing it per service would need bytes attributed to each service, and a
    payload carrying several services would have to have its bytes apportioned
    between them by a rule nobody has chosen.

    **The allowance is the ceiling's, never the running service count's.** That
    is because this number is enforced at the ingest door (ADR 0093), and an
    allowance that moved with the count would change what a customer may send as
    services came and went — and would tell a customer refused at 2 GiB that
    starting a second service is the remedy.

    It used to take the running count and return it on an uncapped tier, as a
    figure the screen could show a paying customer without implying a door that
    would refuse them. That was the honest thing to do while no paid ceiling
    existed. One does now, so the count is gone and this returns the boundary
    the door actually applies.

    None when no rate is recorded, the rate records no allowance, or nothing
    sizes it. An allowance nobody set is not an allowance of zero, which would
    report every byte as overage.
    """
    if price is None or price.included_bytes_per_service is None:
        return None
    services = allowance_service_count(price, services_committed)
    if services is None:
        return None
    return services * price.included_bytes_per_service


def browser_cap_bytes(price: PlanPrice | None, services_committed: int | None) -> int | None:
    """The most the browser door may spend of the month, or None when uncapped.

    A public site key is printed in the customer's page, so anyone who views
    source can spend at that door. ADR 0097 caps it so a scraped key cannot
    silence the customer's servers.

    **A cap, not a share of a partition.** The server door's limit stays the
    whole month: the browser's cap comes out of the whole only as it is actually
    spent, so a customer sending no browser telemetry keeps every byte of their
    plan. Reserving the share instead withdrew a quarter of the free tier for a
    door nobody could reach, since the browser SDK is P1-39 and unbuilt.

    None is "the row records no split", which is what every row before 0097
    means and still means: every door spends the one allowance. That is a
    different statement from a share of zero, which is a plan the browser door
    may send nothing through.

    `allowance.Usage.browserIncludedBytes` in the receiver computes the same
    number from the same row, with the same rounding. Divergence here is a
    customer refused at a limit the screen never showed them.

    The one place the two deliberately differ: on a row with no ceiling on
    billed services — every paying tier — the receiver records no allowance at
    all and enforces nothing, while this returns a figure from the services
    counted so far. That figure is a description, not a boundary, and
    `allowance_is_enforced` is what says so. Sending it lets the screen show a
    paying customer their included volume without implying a door that would
    ever refuse them.
    """
    included = included_bytes(price, services_committed)
    if included is None or price is None or price.browser_share_percent is None:
        return None
    return included * price.browser_share_percent // PERCENT_WHOLE


def allowance_is_enforced(price: PlanPrice | None, services_committed: int | None) -> bool:
    """Whether volume past the allowance is refused rather than billed.

    True wherever something sizes the allowance: the plan's own ceiling on the
    free tier, or the customer's committed count on a paid one.

    **This used to be true for the free tier and nothing else**, and the reason
    given was that ADR 0083 section 6 refuses to cap a paying customer to hold
    their bill down. That reason still stands and this still honours it — what
    it does not do any more is confuse *"we will not cap you to protect our
    margin"* with *"you have no limit at all"*. ADR 0107 replaced the second
    with a ceiling the customer sets for themselves, and enforcing that is
    keeping their instruction rather than imposing ours.

    False on a paid tier with no active subscription: there is nothing to
    enforce, which is not a ceiling of zero.
    """
    return included_bytes(price, services_committed) is not None
