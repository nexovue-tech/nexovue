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

from nexovue_api.data.postgres.models import BillingSubscription, BillingWebhookDelivery


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


def add_subscription(session: AsyncSession, subscription: BillingSubscription) -> None:
    session.add(subscription)


def add_delivery(session: AsyncSession, delivery: BillingWebhookDelivery) -> None:
    session.add(delivery)
