"""Support access (F31, FR28, FR29, BR14, BR15). No HTTP here.

An owner grants nexovue support a window in which staff may view the console as
one of their people. Staff start a *view as* only inside a live grant; the grant
withdrawn or lapsed ends every session started under it on its next request.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import SupportAccessGrant, User
from nexovue_api.errors.exceptions import ConflictError, NotFoundError
from nexovue_api.members import queries as members
from nexovue_api.support import queries
from nexovue_api.support.schemas import SupportAccessState, SupportView
from nexovue_api.support.tickets import TicketStore, ViewAsTicket

# A support session lasts half an hour, then staff start another if they need it.
VIEW_AS_MINUTES: Final = 30
NO_GRANT: Final = "This organization has not granted support access."
NOT_GRANTED: Final = "Support access is not granted, so there is nothing to withdraw."
NO_SUCH_PERSON: Final = "No such person in this organization."


async def state(db: AsyncSession, *, org_id: uuid.UUID, now: datetime) -> SupportAccessState:
    grant = await queries.active_grant(db, org_id=org_id, at=now)
    views = await queries.recent_views(db, org_id=org_id)
    return SupportAccessState(
        granted_until=grant.expires_at if grant else None,
        views=[
            SupportView(
                at=view.at,
                staff=view.actor_label or "nexovue support",
                viewed=_text(view.detail, "viewed"),
                reason=_text(view.detail, "reason"),
                impersonation_id=view.impersonation_context,
            )
            for view in views
        ],
    )


async def grant(db: AsyncSession, *, owner: User, hours: int, now: datetime) -> None:
    """A new window replaces the one in force: one grant at a time."""
    current = await queries.active_grant(db, org_id=owner.org_id, at=now)
    if current is not None:
        current.withdrawn_at = now
    until = now + timedelta(hours=hours)
    db.add(
        SupportAccessGrant(
            org_id=owner.org_id, granted_by=owner.id, granted_at=now, expires_at=until
        )
    )
    audit.record_support_access_granted(
        db, org_id=owner.org_id, owner_id=owner.id, until=until.isoformat()
    )
    await db.flush()


async def withdraw(db: AsyncSession, *, owner: User, now: datetime) -> None:
    current = await queries.active_grant(db, org_id=owner.org_id, at=now)
    if current is None:
        raise ConflictError(NOT_GRANTED)
    current.withdrawn_at = now
    audit.record_support_access_withdrawn(db, org_id=owner.org_id, owner_id=owner.id)
    await db.flush()


async def start_view(
    db: AsyncSession,
    tickets: TicketStore,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    operator_label: str,
    reason: str,
    now: datetime,
) -> str:
    """A ticket to view as `user_id`, only inside the owner's grant (BR14)."""
    grant = await queries.active_grant(db, org_id=org_id, at=now)
    if grant is None:
        raise ConflictError(NO_GRANT)
    viewed = await members.find_member(db, org_id=org_id, user_id=user_id)
    if viewed is None or viewed.password_hash is None:
        raise NotFoundError(NO_SUCH_PERSON)
    impersonation_id = uuid.uuid4()
    until = min(now + timedelta(minutes=VIEW_AS_MINUTES), grant.expires_at)
    audit.record_support_view_started(
        db,
        org_id=org_id,
        viewed=viewed,
        operator_label=operator_label,
        reason=reason.strip(),
        impersonation_id=impersonation_id,
        until=until.isoformat(),
    )
    await db.flush()
    return await tickets.issue(
        ViewAsTicket(
            user_id=viewed.id,
            org_id=org_id,
            operator_label=operator_label,
            reason=reason.strip(),
            grant_id=grant.id,
            impersonation_id=impersonation_id,
            until=until,
        )
    )


def _text(detail: dict[str, object] | None, key: str) -> str | None:
    value = (detail or {}).get(key)
    return value if isinstance(value, str) else None
