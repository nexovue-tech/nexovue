"""The ingest stop valve (P1-52), in one place for both of its callers.

The terminal command (`suspend.py`, the 3 a.m. path) and the console's platform
area (F31) close and open the same two columns through these functions, so the rules
cannot drift apart: a reason is required to close, a second close is refused
rather than restamped, and opening clears the reason with the suspension.

Every close and open is audited, named for who did it — nexovue's operator from
the console, "an operator at a terminal" from the command.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import Organization
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError

REASON_REQUIRED: Final = "Say why: the first message after a suspension is the customer asking."


async def suspend(
    session: AsyncSession,
    organization: Organization,
    *,
    reason: str,
    actor_label: str,
    now: datetime,
) -> None:
    """Close the valve. Refuses a second close: restamping would move when the
    suspension started and overwrite the reason the customer is about to be told."""
    if not reason.strip():
        raise InvalidRequestError(REASON_REQUIRED)
    if organization.ingest_suspended_at is not None:
        raise ConflictError(
            f"{organization.slug} was already suspended at "
            f"{organization.ingest_suspended_at.isoformat()}: "
            f"{organization.ingest_suspended_reason}"
        )
    organization.ingest_suspended_at = now
    organization.ingest_suspended_reason = reason.strip()
    audit.record_ingest_suspended(
        session, organization=organization, reason=reason.strip(), actor_label=actor_label
    )
    await session.flush()


async def resume(session: AsyncSession, organization: Organization, *, actor_label: str) -> None:
    """Open the valve, clearing the reason: a reason that outlives its suspension
    is a note that will be read as current."""
    if organization.ingest_suspended_at is None:
        raise ConflictError(f"{organization.slug} is not suspended")
    organization.ingest_suspended_at = None
    organization.ingest_suspended_reason = None
    audit.record_ingest_resumed(session, organization=organization, actor_label=actor_label)
    await session.flush()
