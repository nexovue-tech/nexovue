"""What the billing endpoints put on the wire.

Two audiences, and the difference shapes every model below.

**The webhook answers a payment processor**, not a person. Its body is
deliberately tiny and deliberately not the event: anything more would be this
application telling an unauthenticated caller what it knows about a tenant.

**The billing screen answers the customer**, and reuses `usage`'s models rather
than restating them. `BillingPeriodUsage` is where the allowance, what has been
spent against it and whether it is enforced already live; a second shape here
would be a second place for those numbers to be wrong, which is the failure the
receiver's `allowance` package warns about in its own header.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from nexovue_api.usage.schemas import BillingPeriodUsage, PlanPriceSummary, PlanSummary


class DeliveryOutcome(StrEnum):
    """What became of one signed delivery.

    All three are a **200**, which is the difference between this door and the
    deploy webhook's. A processor delivering at least once retries anything that
    is not a 200, so answering a duplicate with a 409 — as `deploys` correctly
    does for a CI system that retries on failure — would ask for the same
    already-handled event forever.
    """

    RECORDED = "recorded"
    # Seen before. The unique index means it changed nothing the second time.
    DUPLICATE = "duplicate"
    # Correctly signed, and about something this product does not act on.
    IGNORED = "ignored"


class DeliveryAcknowledgement(BaseModel):
    outcome: DeliveryOutcome


class SubscriptionSummary(BaseModel):
    """The paid arrangement behind the plan, as far as a customer needs it.

    **No processor reference and no processor name.** ADR 0102 keeps the vendor
    out of everything above the adapter, and a subscription id is useful to
    exactly nobody reading a screen — it is what a support conversation asks
    for, and support has the database. What a customer needs is which tier,
    what state it is in, and since when.
    """

    plan_slug: str
    # `pending`, `active` or `cancelled` — the row's own status, unmapped: the
    # three words mean what they say and a screen renders them directly.
    status: str
    # ADR 0107's ceiling: the count the customer committed to, which sized the
    # mandate. Shown because a ceiling nobody can see is one they cannot act on
    # before it stops their ingestion.
    services_committed: int
    # When the mandate was authorised and when it ended. Null is "not yet" and
    # "not ended", which are different facts from a date of zero.
    activated_at: datetime | None = None
    cancelled_at: datetime | None = None


class BillingOverview(BaseModel):
    """Everything the billing screen shows (P1-75).

    The plan, the arrangement paying for it, and where the month stands against
    the allowance. The last of those is `usage`'s own model computed by `usage`'s
    own functions, so this screen and the usage screen cannot disagree — and
    neither can disagree with the ingest door, which computes the same figures
    from the same row.
    """

    plan: PlanSummary
    # The rate in force today. Null while a tier has no price row, which is what
    # `enterprise` is and will stay: a tier priced by agreement has no list price.
    price: PlanPriceSummary | None = None
    # Null when this organization has never had one — every free-tier account.
    subscription: SubscriptionSummary | None = None
    # The calendar month's allowance, spend and whether going past it stops
    # ingestion. `allowance_enforced` is the sentence ADR 0107 requires the
    # screen to be able to write.
    period: BillingPeriodUsage


class StartCheckoutRequest(BaseModel):
    """A customer asking to move onto a paid tier.

    **`services_committed` is ADR 0107's ceiling**, chosen by the customer and
    not by us. ADR 0056 prices per service monitored, so the number of services
    a customer commits to is at once the most they can be charged in a month and
    the volume they may send — `plan_prices.included_bytes_per_service`
    multiplies the same figure. One number, both meanings, and the customer sets
    it: 0107's "an amount the customer sets and can see, above which we still
    stop".

    **The contact is not stored** and exists only for the length of one call.
    ADR 0109: the processor holds what the bank's pre-debit notice needs, and a
    copy of ours would be personal data kept so nobody has to retype.
    """

    plan_slug: str
    # At least one: an arrangement covering no services authorises no charge and
    # grants no allowance, which is not a plan anybody meant to buy.
    services_committed: int = Field(ge=1)
    contact_name: str | None = None
    contact_email: str
    # Asked for only when the configured processor declares it. See
    # `PaymentProcessor.required_contact_fields` and ADR 0109.
    contact_phone: str | None = None


class CheckoutHandoff(BaseModel):
    """Where to send the customer to authorise the mandate.

    A URL and nothing else. The card details never reach this application, which
    is why ADR 0102 made the port answer with one.
    """

    url: str
