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
from collections.abc import Sequence
from typing import Final

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    BillingSubscription,
    BillingWebhookDelivery,
    Organization,
    User,
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


# Every state in which an arrangement can still debit: a checkout that may yet be
# authorised, a live mandate, and one halted for failed payments, which the
# processor resumes if the card is fixed.
LIVE_STATUSES: Final = ("pending", "active", "lapsed")


async def live_subscriptions(
    session: AsyncSession, *, org_id: uuid.UUID
) -> Sequence[BillingSubscription]:
    """Every arrangement of this organization that could still take money, oldest first."""
    statement = (
        select(BillingSubscription)
        .where(
            BillingSubscription.org_id == org_id,
            BillingSubscription.status.in_(LIVE_STATUSES),
        )
        .order_by(BillingSubscription.created_at, BillingSubscription.id)
    )
    return (await session.scalars(statement)).all()


async def owner_addresses(session: AsyncSession, *, org_id: uuid.UUID) -> list[str]:
    """Where a billing notice goes: every current owner of the organization."""
    statement = select(User.email).where(
        User.org_id == org_id, User.role == "owner", User.removed_at.is_(None)
    )
    return list(await session.scalars(statement))


async def lock_organization(session: AsyncSession, *, org_id: uuid.UUID) -> Organization | None:
    """The organization row, held until this transaction ends, so two checkouts —
    two tabs — take turns rather than each seeing no live arrangement."""
    statement = select(Organization).where(Organization.id == org_id).with_for_update()
    found: Organization | None = await session.scalar(statement)
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


async def charges(
    session: AsyncSession, *, org_id: uuid.UUID, kinds: Sequence[str], limit: int
) -> Sequence[Row[tuple[BillingWebhookDelivery, BillingSubscription]]]:
    """Every delivery that was a charge, with the arrangement it was raised under.

    The delivery says a charge happened and when; the arrangement says what it
    was for. Both in one statement rather than a lookup per row, because a
    history is a list and the second shape is how a short list becomes N+1.

    Joined on the pair the delivery already carries and the subscription is
    unique by — `uq_billing_subscriptions_processor_subscription_ref` — so at
    most one arrangement answers. The tenant is bound on both sides of the join:
    the delivery's is the filter, and the subscription's makes a reference
    somehow shared across tenants unable to pull another organization's terms
    into this one's history.
    """
    statement = (
        select(BillingWebhookDelivery, BillingSubscription)
        .join(
            BillingSubscription,
            (BillingSubscription.org_id == BillingWebhookDelivery.org_id)
            & (BillingSubscription.processor == BillingWebhookDelivery.processor)
            & (BillingSubscription.subscription_ref == BillingWebhookDelivery.subscription_ref),
        )
        .where(
            BillingWebhookDelivery.org_id == org_id,
            BillingWebhookDelivery.kind.in_(kinds),
        )
        # Newest first, which is the order a customer looks for the last one in.
        # The id breaks a tie, so two deliveries recorded in the same instant do
        # not swap places between two reads of the same page.
        .order_by(BillingWebhookDelivery.received_at.desc(), BillingWebhookDelivery.id.desc())
        .limit(limit)
    )
    return (await session.execute(statement)).all()


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
