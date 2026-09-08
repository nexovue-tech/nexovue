"""Billing logic: what a signed delivery is allowed to change.

The order of the steps is the security property, and it is `deploys`' order:

1. verify the signature over the **raw** bytes and translate — both inside the
   processor, so nothing here ever sees the vendor's payload;
2. refuse a delivery that has arrived before;
3. only then write.

Nothing is written before step 1 holds, and a missing signature, a wrong one and
one over a body altered after signing are all refused with the same words —
otherwise the endpoint answers "is this account real?" for anyone who asks.

**Moving the organization onto the plan is P1-74's question, and it is answered
here now.** It used to be left open — whether a plan starts at mandate
authorisation or at the first successful charge, and whether a cancellation
downgrades at once or at the end of the paid period. Both halves are settled by
`_plan_after`, and the reasoning sits there rather than here so it is read by
whoever changes it.

**The processor's event is the only thing that moves a plan.** No route sets
`organizations.plan` directly. A customer asking for a plan starts or ends an
arrangement at the processor; the plan follows when the processor says the
arrangement changed. The alternative — moving the plan optimistically and
correcting it on the webhook — grants a tier to somebody whose mandate the bank
then declines, and takes it back a day later.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.billing import (
    BillingContact,
    BillingError,
    BillingEvent,
    BillingEventKind,
    ConfiguredProcessor,
    Money,
    SignatureInvalidError,
    UnhandledBillingEventError,
    queries,
)
from nexovue_api.billing.schemas import (
    BillingOverview,
    CheckoutHandoff,
    CheckoutRequirements,
    DeliveryAcknowledgement,
    DeliveryOutcome,
    StartCheckoutRequest,
    SubscriptionSummary,
)
from nexovue_api.data.postgres.models import (
    BillingSubscription,
    BillingWebhookDelivery,
    Organization,
    PlanPrice,
)
from nexovue_api.errors.exceptions import (
    ConflictError,
    InternalError,
    InvalidRequestError,
    UnauthenticatedError,
)

# The screen and the meter must state one number, so both take it from the same
# functions. `usage.billing` holds the rule ADR 0083 fixed and the receiver's
# `allowance` package implements a second time in Go; a third arithmetic here
# would tell a customer a limit the ingest door does not apply.
from nexovue_api.usage import billing as usage_billing
from nexovue_api.usage import queries as usage_queries
from nexovue_api.usage import service as usage_service
from nexovue_api.usage.schemas import BillingPeriodUsage, PlanPriceSummary, PlanSummary

# One sentence for every way a delivery failed to prove itself. It names no
# account and distinguishes no cause on purpose.
REFUSED_DETAIL: Final = (
    "The delivery was not signed by the configured payment account, or carried "
    "no delivery id to tell a replay from a redelivery."
)

MALFORMED_DELIVERY_DETAIL: Final = (
    "The signed body was not a billing event this product could read."
)

# The statuses a subscription row is put into, spelled once so the model's enum
# and this module cannot drift apart.
STATUS_PENDING: Final = "pending"
STATUS_ACTIVE: Final = "active"
STATUS_CANCELLED: Final = "cancelled"

# Where an organization lands with no live arrangement, and the tier every
# organization starts on. Named rather than spelled `"free"` at each use: it is
# the same fact as `organizations.plan`'s server default, and a literal in three
# places is three places for it to stop matching.
PLAN_WITHOUT_A_SUBSCRIPTION: Final = "free"


_log = structlog.get_logger(__name__)


async def record_from_webhook(
    session: AsyncSession,
    *,
    configured: ConfiguredProcessor,
    body: bytes,
    headers: dict[str, str],
) -> DeliveryAcknowledgement:
    """Act on a billing event, if and only if the processor signed it."""
    try:
        event = configured.processor.event_from_webhook(body=body, headers=headers)
    except SignatureInvalidError as refused:
        raise UnauthenticatedError(REFUSED_DETAIL) from refused
    except UnhandledBillingEventError:
        # Correctly signed and about something with no member in
        # `BillingEventKind`. Accepted so the processor stops redelivering it.
        return DeliveryAcknowledgement(outcome=DeliveryOutcome.IGNORED)
    except BillingError as malformed:
        raise InvalidRequestError(MALFORMED_DELIVERY_DETAIL) from malformed

    # Whose subscription this is, is our question rather than the processor's —
    # answered from the row the checkout wrote. ADR 0110.
    subscription = await queries.subscription_by_processor_ref(
        session, processor=configured.name, subscription_ref=event.subscription_ref
    )
    if subscription is None:
        # Correctly signed, and about a subscription we have no record of
        # starting. There is no honest way to attribute it: inventing an owner
        # would put somebody else's money against a tenant. Acknowledged so the
        # processor stops redelivering, and loud in the log because a signed
        # event we cannot place means our record and theirs have diverged.
        _log.error(
            "a signed billing event names a subscription this system did not start",
            processor=configured.name,
            subscription_ref=event.subscription_ref,
            kind=event.kind.value,
        )
        return DeliveryAcknowledgement(outcome=DeliveryOutcome.IGNORED)

    if await queries.delivery_already_recorded(
        session,
        org_id=subscription.org_id,
        processor=configured.name,
        delivery_id=event.delivery_id,
    ):
        return DeliveryAcknowledgement(outcome=DeliveryOutcome.DUPLICATE)

    await _apply(session, event=event, subscription=subscription)
    queries.add_delivery(
        session,
        BillingWebhookDelivery(
            org_id=subscription.org_id,
            processor=configured.name,
            delivery_id=event.delivery_id,
            kind=event.kind.value,
            subscription_ref=event.subscription_ref,
        ),
    )
    return DeliveryAcknowledgement(outcome=DeliveryOutcome.RECORDED)


async def _apply(
    session: AsyncSession, *, event: BillingEvent, subscription: BillingSubscription
) -> None:
    """Bring the arrangement's row in line with what the event said.

    The row is resolved by the caller and always exists here. It used to be
    created on the spot when a webhook named one we had not seen — possible only
    while the event carried an organization id in the processor's own tags. With
    that gone (ADR 0110) an unknown reference cannot be attributed at all, so the
    caller refuses it there rather than inventing an owner here.
    """
    now = datetime.now(UTC)
    match event.kind:
        case BillingEventKind.SUBSCRIPTION_ACTIVATED:
            subscription.status = STATUS_ACTIVE
            # Left alone once set: a re-activation after a hold is not the day
            # the customer started paying, and an invoice question asks for the
            # first one.
            subscription.activated_at = subscription.activated_at or now
        case BillingEventKind.SUBSCRIPTION_CANCELLED:
            subscription.status = STATUS_CANCELLED
            subscription.cancelled_at = now
        case BillingEventKind.PAYMENT_SUCCEEDED | BillingEventKind.PAYMENT_FAILED:
            # A charge does not change what the arrangement *is*. A failed one
            # ends a subscription only when the processor says so, in its own
            # event, and acting on it here would cancel a customer over a card
            # that declined once.
            return

    await _move_plan(session, org_id=subscription.org_id, to_slug=_plan_after(subscription))


def _plan_after(subscription: BillingSubscription) -> str:
    """The tier an organization is on once its arrangement is in this state.

    **P1-74's two questions, answered.**

    *When does an upgrade take effect?* At authorisation, not at the first
    successful charge. A mandate is authorised days before a bank debits it, and
    the customer who has just paid to be unblocked retries immediately — the
    plan row says so, and the ingest path picks it up within one
    `RECEIVER_ALLOWANCE_CACHE_TTL` (30 seconds by default, 5 minutes at most).
    Waiting for the charge would leave them refused with a receipt in hand.

    *When does a cancellation take effect?* When the processor says the
    arrangement ended, which is the processor's own period boundary and not a
    date computed here. There is no proration rule in this module because there
    is no proration decision to implement: we ask a processor to cancel, and the
    tier changes on the event that says it did.

    **Nothing stored is deleted by a downgrade.** Retention is stamped on each
    row as it is written (ADR 0072), so telemetry kept under a longer plan keeps
    it and only new rows get the shorter one. That is forward-only, which
    phase-1-plan §9 already settled and this does not reopen.
    """
    if subscription.status == STATUS_ACTIVE:
        return subscription.plan_slug
    return PLAN_WITHOUT_A_SUBSCRIPTION


async def _move_plan(session: AsyncSession, *, org_id: uuid.UUID, to_slug: str) -> None:
    """Put the organization on a tier, if it is not on it already.

    A missing organization is left alone rather than raised: the event was
    signed, so the tenant existed when the arrangement was made, and a tenant
    deleted since is not a reason to refuse a delivery the processor will
    otherwise send forever.
    """
    organization = await queries.find_organization(session, org_id=org_id)
    if organization is None or organization.plan == to_slug:
        return
    organization.plan = to_slug


# ---------------------------------------------------------------- the screen


async def overview(
    session: AsyncSession,
    *,
    organization: Organization,
    today: date,
    configured: ConfiguredProcessor | None,
) -> BillingOverview:
    """What this organization is on, what pays for it, and where the month stands.

    Every figure about the allowance comes from `usage.billing`, which is the
    one place ADR 0083's arithmetic lives on this side. The receiver implements
    it a second time in Go because the ingest door cannot call Python; a third
    copy here would be the one that tells a customer a limit nobody enforces.
    """
    price = await usage_queries.find_effective_price(
        session, plan_slug=organization.plan, on_day=today
    )
    return BillingOverview(
        plan=await _plan_summary(session, organization),
        price=_price_summary(price),
        subscription=_subscription_summary(
            await queries.latest_subscription(session, org_id=organization.id)
        ),
        period=await _period(session, org_id=organization.id, today=today, price=price),
        checkout=_checkout_requirements(configured),
    )


def _checkout_requirements(configured: ConfiguredProcessor | None) -> CheckoutRequirements | None:
    """What the upgrade form must collect, or nothing when nobody can be charged.

    Sorted so the screen renders the fields in a stable order across requests —
    a form whose boxes move between loads reads as a bug.
    """
    if configured is None:
        return None
    return CheckoutRequirements(
        required_contact_fields=sorted(
            field.value for field in configured.processor.required_contact_fields()
        )
    )


async def _plan_summary(session: AsyncSession, organization: Organization) -> PlanSummary:
    """The tier and what it grants.

    A missing row is a broken database rather than a caller's mistake:
    `organizations.plan` is a foreign key onto `plans.slug` with ON DELETE
    RESTRICT, so it cannot dangle unless something bypassed the constraint.
    `usage.service._plan_of` refuses the same way for the same reason.
    """
    plan = await usage_queries.find_plan(session, slug=organization.plan)
    if plan is None:
        raise InternalError()
    return PlanSummary(
        slug=plan.slug,
        name=plan.name,
        raw_retention_days=plan.raw_retention_days,
        derived_retention_days=plan.derived_retention_days,
    )


def _price_summary(price: PlanPrice | None) -> PlanPriceSummary | None:
    """The rate, or nothing. A tier priced by agreement has no list price, and
    reporting one as zero would read as free."""
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


def _subscription_summary(
    subscription: BillingSubscription | None,
) -> SubscriptionSummary | None:
    """The arrangement, stripped of everything the customer has no use for —
    which is the processor's name and its reference for the subscription."""
    if subscription is None:
        return None
    return SubscriptionSummary(
        plan_slug=subscription.plan_slug,
        status=subscription.status,
        services_committed=subscription.services_committed,
        activated_at=subscription.activated_at,
        cancelled_at=subscription.cancelled_at,
    )


async def _period(
    session: AsyncSession, *, org_id: uuid.UUID, today: date, price: PlanPrice | None
) -> BillingPeriodUsage:
    """The calendar month, and what it has come to against the allowance.

    Assembled from `usage.billing`'s functions rather than from arithmetic of
    its own — every number below is the return value of the function the usage
    screen calls for the same number.
    """
    month = usage_billing.calendar_month_of(today)
    services = await usage_queries.services_in_range(
        session, org_id=org_id, from_day=month.first_day, to_day=month.last_day
    )
    ingested = await usage_queries.bytes_in_range(
        session, org_id=org_id, from_day=month.first_day, to_day=month.last_day
    )
    frontier = await usage_queries.metering_frontier(session)
    cost = usage_billing.service_cost_minor_units(services, price)
    # What sizes the allowance on a paid tier. G6a.
    services_committed = await usage_queries.active_services_committed(session, org_id=org_id)

    return BillingPeriodUsage(
        first_day=month.first_day,
        last_day=month.last_day,
        settled=usage_service.is_settled(month.last_day, frontier),
        services=services,
        billed_services=usage_billing.billed_services(services, price),
        bytes_ingested=ingested.total,
        browser_bytes_ingested=ingested.browser,
        service_cost_minor_units=cost,
        # The currency belongs to the amount; without one there is nothing to
        # denominate.
        currency=price.currency if cost is not None and price is not None else None,
        included_bytes=usage_billing.included_bytes(price, services_committed),
        browser_included_bytes=usage_billing.browser_cap_bytes(price, services_committed),
        allowance_enforced=usage_billing.allowance_is_enforced(price, services_committed),
    )


# ------------------------------------------------------------ the plan change


# Where a customer lands after the processor's page, and the only deployment
# fact `begin_checkout` needs beyond the price. It is the **app**, not this API —
# `auth/recovery._link` builds verification and reset links the same way, off
# the same `APP_BASE_URL`, for the same reason: a browser lands on a screen,
# which then calls an endpoint.
#
# It is the billing screen itself, and it carries nothing. A returning customer
# is shown the state `GET /billing` reports — `pending` while the mandate is
# being confirmed — so the outcome comes from the webhook rather than from a
# query parameter the customer could edit in the address bar.
CHECKOUT_RETURN_PATH: Final = "/settings/billing"

PLAN_UNKNOWN_DETAIL: Final = "No such plan."

UNPRICED_PLAN_DETAIL: Final = (
    "This plan has no list price, so a subscription for it cannot be started here."
)

FREE_PLAN_DETAIL: Final = (
    "This plan costs nothing, so there is no mandate to authorise. Cancel the "
    "current subscription instead."
)

ALREADY_ON_PLAN_DETAIL: Final = "This organization already has a live subscription to that plan."

NOTHING_TO_CANCEL_DETAIL: Final = "This organization has no live subscription to cancel."


NO_APP_BASE_URL_DETAIL: Final = (
    "APP_BASE_URL is not set, so there is nowhere to return the customer to after paying."
)


def checkout_return_url(app_base_url: str) -> str:
    """Where the processor sends the customer's browser when payment ends.

    Refused rather than defaulted when the setting is empty. A guessed host is a
    link to nowhere, and the customer meets it after their money has moved —
    the same reason `APP_BASE_URL` has no default for verification links.
    """
    if not app_base_url.strip():
        raise InvalidRequestError(NO_APP_BASE_URL_DETAIL)
    return f"{app_base_url.rstrip('/')}{CHECKOUT_RETURN_PATH}"


async def authorised_maximum_for(
    session: AsyncSession, *, plan_slug: str, services_committed: int, on_day: date
) -> Money:
    """The most one month of this plan may charge, at the count the customer chose.

    **Not a fixed monthly amount.** ADR 0056 prices per service monitored, so
    what a month comes to moves with the customer's own service count. What is
    fixed is the ceiling — `services_committed` services at the rate in force —
    which is ADR 0107's number: the customer sets it, sees it, and is never
    charged past it.

    The multiplication is `usage.billing`'s, not this module's, so the ceiling a
    mandate authorises and the cost the usage screen displays come out of one
    function. `billed_services` inside it applies the plan's own cap where the
    plan has one, which is why a commitment above a capped tier's ceiling
    authorises the ceiling rather than the commitment.
    """
    price = await usage_queries.find_effective_price(session, plan_slug=plan_slug, on_day=on_day)
    if price is None:
        raise InvalidRequestError(UNPRICED_PLAN_DETAIL)

    maximum = usage_billing.service_cost_minor_units(services_committed, price)
    if maximum is None or maximum <= 0:
        raise InvalidRequestError(FREE_PLAN_DETAIL)
    return Money(minor_units=maximum, currency=price.currency)


async def begin_plan_change(
    session: AsyncSession,
    *,
    configured: ConfiguredProcessor,
    organization: Organization,
    request: StartCheckoutRequest,
    app_base_url: str,
    today: date,
) -> CheckoutHandoff:
    """Start the mandate that moves this organization onto a paid tier.

    **This changes no plan.** It hands back somewhere to authorise a mandate;
    `organizations.plan` moves when the processor says the arrangement became
    active, which is `_plan_after`. An upgrade applied optimistically here would
    grant a tier to somebody whose bank then declines the mandate.
    """
    if await usage_queries.find_plan(session, slug=request.plan_slug) is None:
        raise InvalidRequestError(PLAN_UNKNOWN_DETAIL)

    live = await queries.latest_subscription(session, org_id=organization.id)
    if live is not None and live.status == STATUS_ACTIVE and live.plan_slug == request.plan_slug:
        raise ConflictError(ALREADY_ON_PLAN_DETAIL)

    checkout = await configured.processor.begin_checkout(
        org_id=organization.id,
        plan_slug=request.plan_slug,
        contact=BillingContact(
            email=request.contact_email,
            name=request.contact_name,
            phone=request.contact_phone,
        ),
        authorised_maximum=await authorised_maximum_for(
            session,
            plan_slug=request.plan_slug,
            services_committed=request.services_committed,
            on_day=today,
        ),
        return_url=checkout_return_url(app_base_url),
    )

    # The row that makes the processor's later events attributable. ADR 0110:
    # the reference is theirs, but *whose subscription it is* is answered here,
    # from a row written while an authenticated member was on the call — never
    # from an organization id handed back by the processor.
    #
    # Written even though the customer may abandon the page. A pending row that
    # never activates is a record of an attempt; the alternative is a webhook
    # arriving for an arrangement we have no record of starting, which cannot be
    # attributed at all.
    queries.add_subscription(
        session,
        BillingSubscription(
            org_id=organization.id,
            processor=configured.name,
            subscription_ref=checkout.subscription_ref,
            processor_customer_ref=checkout.processor_customer_ref,
            plan_slug=request.plan_slug,
            status=STATUS_PENDING,
            services_committed=request.services_committed,
        ),
    )
    return CheckoutHandoff(url=checkout.url)


async def end_subscription(
    session: AsyncSession, *, configured: ConfiguredProcessor, organization: Organization
) -> None:
    """Ask the processor to end this organization's arrangement.

    **The tier does not move here either.** The processor decides when the
    arrangement ends — at its own period boundary, not on a date this module
    computes — and `SUBSCRIPTION_CANCELLED` is what drops the organization back
    to the free tier. That is why there is no proration rule in this file: there
    is no proration decision to implement.
    """
    subscription = await queries.latest_subscription(session, org_id=organization.id)
    if subscription is None or subscription.status == STATUS_CANCELLED:
        raise ConflictError(NOTHING_TO_CANCEL_DETAIL)
    if subscription.processor != configured.name:
        # ADR 0102 expects two processors. Cancelling an arrangement through the
        # wrong one would name a subscription the processor has never heard of,
        # and its "not found" would be reported as a failed cancellation.
        raise ConflictError(
            "This subscription was not taken out through the processor this "
            "deployment is configured with, so it cannot be cancelled here."
        )
    await configured.processor.cancel(subscription_ref=subscription.subscription_ref)
