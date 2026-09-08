"""Billing logic: what a signed delivery is allowed to change.

The order of the steps is the security property, and it is `deploys`' order:

1. verify the signature over the **raw** bytes and translate — both inside the
   processor, so nothing here ever sees the vendor's payload;
2. refuse a delivery that has arrived before;
3. only then write.

Nothing is written before step 1 holds, and a missing signature, a wrong one and
one over a body altered after signing are all refused with the same words —
otherwise the endpoint answers "is this account real?" for anyone who asks.

**What this deliberately does not do is move the organization onto the plan.**
Recording that a subscription became active is a fact the processor proved.
Promoting that to `organizations.plan` is a *policy* question nobody has
answered — whether a plan starts at mandate authorisation or at the first
successful charge, and whether a cancellation downgrades immediately or at the
end of the paid period. ADR 0107 settled what happens at the allowance ceiling
and left this untouched, and P1-74 is the row that owns the plan-change screen.
Guessing it here would put the guess in the one place nobody looks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.billing import (
    BillingError,
    BillingEvent,
    BillingEventKind,
    ConfiguredProcessor,
    SignatureInvalidError,
    UnhandledBillingEventError,
    queries,
)
from nexovue_api.billing.schemas import DeliveryAcknowledgement, DeliveryOutcome
from nexovue_api.data.postgres.models import BillingSubscription, BillingWebhookDelivery
from nexovue_api.errors.exceptions import InvalidRequestError, UnauthenticatedError

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

    if await queries.delivery_already_recorded(
        session,
        org_id=event.org_id,
        processor=configured.name,
        delivery_id=event.delivery_id,
    ):
        return DeliveryAcknowledgement(outcome=DeliveryOutcome.DUPLICATE)

    await _apply(session, event=event, processor=configured.name)
    queries.add_delivery(
        session,
        BillingWebhookDelivery(
            org_id=event.org_id,
            processor=configured.name,
            delivery_id=event.delivery_id,
            kind=event.kind.value,
            subscription_ref=event.subscription_ref,
        ),
    )
    return DeliveryAcknowledgement(outcome=DeliveryOutcome.RECORDED)


async def _apply(session: AsyncSession, *, event: BillingEvent, processor: str) -> None:
    """Bring the arrangement's row in line with what the event said.

    A payment event about a subscription we have never seen still creates the
    row, in `pending`. The alternative is dropping a proved fact because the
    checkout that started it did not finish writing, which is the failure mode
    worth avoiding on this path.
    """
    subscription = await queries.subscription_by_ref(
        session,
        org_id=event.org_id,
        processor=processor,
        subscription_ref=event.subscription_ref,
    )
    if subscription is None:
        subscription = BillingSubscription(
            org_id=event.org_id,
            processor=processor,
            subscription_ref=event.subscription_ref,
            plan_slug=event.plan_slug,
            status=STATUS_PENDING,
        )
        queries.add_subscription(session, subscription)

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
            pass
