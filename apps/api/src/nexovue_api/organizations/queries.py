"""The statements an organization deletion runs.

One function per table in `tables.DELETED_DIRECTLY`, mapped by the table's own
name in `DELETE_TENANT_ROWS`. Named and mapped rather than assembled, for the
reason `purge/queries.py` gives: the set of tables a deletion touches has to be
readable, and a statement whose table arrived from a variable is invisible to
review and to `tests/test_postgres_tenancy_structure.py` alike.

Every one filters `org_id`. That is what makes deleting one tenant unable to
reach another, and it is checked structurally rather than trusted.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Final, cast

from sqlalchemy import CursorResult, delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    ApiToken,
    AuditLogEntry,
    BillingSubscription,
    BillingWebhookDelivery,
    NotificationChannel,
    Organization,
    PlanGrant,
    Project,
    SupportAccessGrant,
    User,
    WebsitePayment,
)


async def delete_billing_webhook_deliveries(session: AsyncSession, org_id: uuid.UUID) -> int:
    """Which billing events arrived for this tenant. Names no card and no body."""
    return await _rows_deleted(
        session, delete(BillingWebhookDelivery).where(BillingWebhookDelivery.org_id == org_id)
    )


async def delete_billing_subscriptions(session: AsyncSession, org_id: uuid.UUID) -> int:
    """The paying arrangement, and the processor's reference for the customer."""
    return await _rows_deleted(
        session, delete(BillingSubscription).where(BillingSubscription.org_id == org_id)
    )


async def delete_api_tokens(session: AsyncSession, org_id: uuid.UUID) -> int:
    """The organization's read credentials. Their daily counters go by cascade."""
    return await _rows_deleted(session, delete(ApiToken).where(ApiToken.org_id == org_id))


async def delete_website_payments(session: AsyncSession, org_id: uuid.UUID) -> int:
    """The website payment that bought the organization, and its processor reference."""
    return await _rows_deleted(
        session, delete(WebsitePayment).where(WebsitePayment.org_id == org_id)
    )


async def delete_plan_grants(session: AsyncSession, org_id: uuid.UUID) -> int:
    """Complimentary plans, with the reason nexovue gave for each."""
    return await _rows_deleted(session, delete(PlanGrant).where(PlanGrant.org_id == org_id))


async def delete_support_access_grants(session: AsyncSession, org_id: uuid.UUID) -> int:
    """The owner's consents to support; each names the owner who gave it."""
    return await _rows_deleted(
        session, delete(SupportAccessGrant).where(SupportAccessGrant.org_id == org_id)
    )


async def delete_audit_log(session: AsyncSession, org_id: uuid.UUID) -> int:
    """Who did what inside this tenant. `actor_label` can hold an address."""
    return await _rows_deleted(session, delete(AuditLogEntry).where(AuditLogEntry.org_id == org_id))


async def delete_users(session: AsyncSession, org_id: uuid.UUID) -> int:
    """The people. Their sessions go with them, by cascade."""
    return await _rows_deleted(session, delete(User).where(User.org_id == org_id))


async def delete_projects(session: AsyncSession, org_id: uuid.UUID) -> int:
    """The environments, and by cascade every key, check, counter and map row."""
    return await _rows_deleted(session, delete(Project).where(Project.org_id == org_id))


async def delete_notification_channels(session: AsyncSession, org_id: uuid.UUID) -> int:
    """Where alerts went, and their sealed secrets. Takes the delivery log with them."""
    return await _rows_deleted(
        session, delete(NotificationChannel).where(NotificationChannel.org_id == org_id)
    )


DELETE_TENANT_ROWS: Final[dict[str, Callable[[AsyncSession, uuid.UUID], Awaitable[int]]]] = {
    "billing_webhook_deliveries": delete_billing_webhook_deliveries,
    "billing_subscriptions": delete_billing_subscriptions,
    "api_tokens": delete_api_tokens,
    "website_payments": delete_website_payments,
    "plan_grants": delete_plan_grants,
    "support_access_grants": delete_support_access_grants,
    "audit_log": delete_audit_log,
    "notification_channels": delete_notification_channels,
    "users": delete_users,
    "projects": delete_projects,
}


async def lock_organization(session: AsyncSession, org_id: uuid.UUID) -> Organization | None:
    """The organization row, held until this transaction ends.

    Locked because two deletion requests arriving together would otherwise both
    read a live tenant and both write a tombstone, and the second would move
    `deleted_at` — the moment the erasure is owed from — forward.
    """
    statement = select(Organization).where(Organization.id == org_id).with_for_update()
    found: Organization | None = await session.scalar(statement)
    return found


async def tombstoned_organization_ids(session: AsyncSession) -> list[uuid.UUID]:
    """Every organization marked deleted, whether or not its erasure finished."""
    statement = select(Organization.id).where(Organization.deleted_at.is_not(None))
    return list(await session.scalars(statement))


async def erasure_is_certified(session: AsyncSession, *, action: str, target: str) -> bool:
    """Whether the tenantless entry that certifies an erasure has been written."""
    statement = select(
        exists().where(
            AuditLogEntry.org_id.is_(None),
            AuditLogEntry.action == action,
            AuditLogEntry.target == target,
        )
    )
    return bool(await session.scalar(statement))


async def _rows_deleted(session: AsyncSession, statement: Any) -> int:
    """How many rows a DELETE removed.

    `AsyncSession.execute` is typed as returning the abstract `Result`, which
    has no row count; the object it actually returns for a DML statement is a
    `CursorResult`, which does.
    """
    result = await session.execute(statement)
    return int(cast(CursorResult[Any], result).rowcount)
