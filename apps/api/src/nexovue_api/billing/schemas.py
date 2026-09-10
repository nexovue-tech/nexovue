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
from typing import Any

from pydantic import BaseModel, Field

from nexovue_api.billing.processor import EmbeddedCheckout, HostedPage
from nexovue_api.usage.schemas import BillingPeriodUsage, PlanPriceSummary, PlanSummary


class CheckoutStyle(StrEnum):
    """Which of the two handoffs the screen has been given.

    The screen branches on this and on nothing else — never on the processor's
    name, which it is not told and must not learn.
    """

    HOSTED_PAGE = "hosted_page"
    EMBEDDED = "embedded"


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


class CheckoutRequirements(BaseModel):
    """What a screen must collect before it can start a checkout.

    Asked of the processor rather than fixed here, so the form renders one
    provider's fields and not the union of every provider's. A switch then
    deletes a field instead of leaving a dead one. ADR 0109, and
    `PaymentProcessor.required_contact_fields` is where the answer comes from.
    """

    # `name`, `email`, `phone` — the members of `ContactField`, as strings,
    # because a screen renders a label per field and does not need our enum.
    required_contact_fields: list[str]


class PlanOption(BaseModel):
    """A tier the customer could move to, priced in the currency they are seeing.

    Sent with the overview rather than fetched from a second endpoint: the
    upgrade control and the current plan are read together, and two requests
    could disagree about the currency between them.
    """

    slug: str
    name: str
    currency: str
    # Per service, per month, in minor units. What one service costs; the total
    # is this times the count the customer commits to.
    price_per_service_minor_units: int
    included_bytes_per_service: int | None = None
    max_billed_services: int | None = None
    raw_retention_days: int
    derived_retention_days: int


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
    # Null when no payment processor is configured, which is a real state and not
    # an error: the plan and the month's usage are still readable, and the screen
    # says upgrading is unavailable rather than drawing a form that would 503.
    checkout: CheckoutRequirements | None = None
    # The currency every figure above is stated in.
    currency: str
    # Every currency this organization could be quoted in, so the screen can
    # offer a switch and never one no price row backs.
    available_currencies: list[str] = Field(default_factory=list)
    # The tiers that can be moved to, priced in `currency`. Excludes the one the
    # organization is already on and any tier with no row in this currency — a
    # plan a customer cannot actually buy is not an option.
    plans: list[PlanOption] = Field(default_factory=list)


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
    # **Chosen by the customer, never guessed from where they connected.** Tax
    # and settlement follow the payment instrument, not a network address, so a
    # traveller or anyone behind a VPN must not be quoted the wrong currency —
    # the screen offers what the tier is priced in and this is the answer.
    currency: str = Field(min_length=3, max_length=3)
    # At least one: an arrangement covering no services authorises no charge and
    # grants no allowance, which is not a plan anybody meant to buy.
    services_committed: int = Field(ge=1)
    # All optional, and all asked for only when the configured processor declares
    # it needs them — `PaymentProcessor.required_contact_fields`, ADR 0109. One
    # processor requires all three up front because its page collects none;
    # another collects them itself and declares nothing, and then a screen that
    # asked would make a customer type the same thing twice.
    #
    # The email falls back to the signed-in member's, because we already know it:
    # a field whose answer the product holds is a field nobody should retype.
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None


class CheckoutHandoff(BaseModel):
    """How to put the customer in front of the processor's own card form.

    Two shapes, discriminated by `style`, because processors differ in a way the
    screen cannot paper over: one owns a whole page, another owns a script that
    runs on ours. Never a form of our own — the card details do not reach this
    application in either shape, which is ADR 0102's reason for a port at all.

    **Everything here is published to a browser.** A field is added to this model
    only if it is safe in a page's source; the processor's secret key is not, and
    the publishable key is exactly what `public_key` is named for.
    """

    style: CheckoutStyle

    # style=hosted_page: where to send the browser.
    url: str | None = None

    # style=embedded: the processor's script, the constructor it defines, and
    # what to construct it with. `options` is opaque here and in the browser —
    # the script is the only thing that reads a key of it.
    script_url: str | None = None
    global_name: str | None = None
    options: dict[str, Any] | None = None

    @classmethod
    def of(cls, handoff: HostedPage | EmbeddedCheckout) -> CheckoutHandoff:
        """The port's answer as the browser receives it."""
        if isinstance(handoff, HostedPage):
            return cls(style=CheckoutStyle.HOSTED_PAGE, url=handoff.url)
        return cls(
            style=CheckoutStyle.EMBEDDED,
            script_url=handoff.script_url,
            global_name=handoff.global_name,
            options=dict(handoff.options),
        )
