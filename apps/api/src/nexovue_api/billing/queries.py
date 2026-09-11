"""Billing SQL. Decisions live in `service.py`.

Every statement binds `org_id`, including the two a webhook reaches. That is
safe here for the reason `deploys` gives about a project id: the organization is
not taken from the request, it is read out of the payload *after* the signature
proved the payload is the processor's — so it is established rather than
claimed. The unique index on `(processor, delivery_id)` is what catches a replay
aimed at a different tenant, and it is not scoped by one.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    BillingSubscription,
    BillingWebhookDelivery,
    Organization,
)


async def delivery_already_recorded(
    session: AsyncSession, *, org_id: uuid.UUID, processor: str, delivery_id: str
) -> bool:
    """Whether this exact delivery has already been acted on."""
    statement = select(BillingWebhookDelivery.id).where(
        BillingWebhookDelivery.org_id == org_id,
        BillingWebhookDelivery.processor == processor,
        BillingWebhookDelivery.delivery_id == delivery_id,
    )
    return await session.scalar(statement) is not None


async def subscription_by_ref(
    session: AsyncSession, *, org_id: uuid.UUID, processor: str, subscription_ref: str
) -> BillingSubscription | None:
    """The arrangement this reference names, within this organization."""
    statement = select(BillingSubscription).where(
        BillingSubscription.org_id == org_id,
        BillingSubscription.processor == processor,
        BillingSubscription.subscription_ref == subscription_ref,
    )
    # Named rather than returned inline: `scalar` is typed `Any`, and letting
    # that flow out of a query module would take the checking off every caller.
    found: BillingSubscription | None = await session.scalar(statement)
    return found


async def subscription_by_processor_ref(
    session: AsyncSession, *, processor: str, subscription_ref: str
) -> BillingSubscription | None:
    """The arrangement a processor's own reference names, whoever owns it.

    **Unscoped on purpose, and it is what establishes the tenant** — the same
    shape and the same reason as `find_live_key_by_prefix`. A webhook proves it
    came from the processor by its signature, and then names a subscription;
    whose it is, is answered here, from the row written when the checkout began.

    Scoping it by organization would need the caller to tell us the organization,
    which is the one thing a webhook must not be trusted for. This lookup replaced
    exactly that arrangement: the org id used to arrive in custom tags we asked
    the processor to echo back, which was our identifier living in their storage.

    The pair is unique — `uq_billing_subscriptions_processor_subscription_ref` —
    so this can name at most one organization.
    """
    statement = select(BillingSubscription).where(
        BillingSubscription.processor == processor,
        BillingSubscription.subscription_ref == subscription_ref,
    )
    found: BillingSubscription | None = await session.scalar(statement)
    return found


async def latest_subscription(
    session: AsyncSession, *, org_id: uuid.UUID
) -> BillingSubscription | None:
    """This organization's most recent arrangement, whatever became of it.

    Newest rather than "the active one", because a screen has to be able to say
    *cancelled* as well as *active* — a customer whose mandate just ended and
    who is shown nothing at all learns less than one shown the row that ended.
    Rows accumulate rather than being edited across arrangements, so the newest
    is the one the customer is living in.

    Not narrowed by processor: two are expected, and an organization pays
    through one of them at a time. Narrowing here would hide the arrangement
    from a screen that has no business knowing which processor answered.
    """
    statement = (
        select(BillingSubscription)
        .where(BillingSubscription.org_id == org_id)
        .order_by(BillingSubscription.created_at.desc(), BillingSubscription.id.desc())
        .limit(1)
    )
    found: BillingSubscription | None = await session.scalar(statement)
    return found


async def find_organization(session: AsyncSession, *, org_id: uuid.UUID) -> Organization | None:
    """The tenant row a webhook's event names, so its plan can be moved.

    Keyed on the primary key, which for this table *is* the tenant: there is no
    `org_id` column to bind because the identity is the row.
    """
    organization: Organization | None = await session.scalar(
        select(Organization).where(Organization.id == org_id)
    )
    return organization


def add_subscription(session: AsyncSession, subscription: BillingSubscription) -> None:
    session.add(subscription)


def add_delivery(session: AsyncSession, delivery: BillingWebhookDelivery) -> None:
    session.add(delivery)
