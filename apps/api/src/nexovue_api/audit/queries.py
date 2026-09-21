"""Reading the audit log, for its screen. Every statement names its organization."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import Select, literal, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit.cursor import AuditPageCursor
from nexovue_api.audit.schemas import EventKind
from nexovue_api.data.postgres.models import AuditLogEntry, User

# Which actions each filter shows, by prefix. An action no kind names still
# appears when no kind is chosen.
KINDS: Final[dict[EventKind, tuple[str, ...]]] = {
    "people": ("member.",),
    "credentials": ("api_key.", "site_key.", "deploy.webhook_secret", "api_token."),
    "sign_in": ("session.", "user."),
    "configuration": (
        "check.",
        "check_secret.",
        "alert_rule.",
        "notification_channel.",
        "maintenance_window.",
        "integration.",
        "project.",
        "organization.",
    ),
    "support": ("support.",),
}

# **`changes` is the one filter defined by exclusion.** Seventeen consecutive
# "signed in" rows bury the events the log exists for, and this is the answer to
# that: everything somebody *did*, with the sign-ins left out.
#
# It subtracts rather than lists, because listing would mean a new action kind
# nobody remembered to add here vanishing from the default view of an audit log.
# Adding a prefix to `sign_in` is the only way to hide something, and that is a
# visible decision in a file about hiding things.
NOT_A_CHANGE: Final = KINDS["sign_in"]


async def page(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    kind: EventKind | None,
    since: datetime | None,
    until: datetime | None,
    after: AuditPageCursor | None,
    limit: int,
) -> list[AuditLogEntry]:
    """One page, newest first. `limit + 1` rows are read so the caller can tell
    whether another page exists without a count."""
    statement: Select[tuple[AuditLogEntry]] = select(AuditLogEntry).where(
        AuditLogEntry.org_id == org_id
    )
    if actor_id is not None:
        statement = statement.where(AuditLogEntry.actor_user_id == actor_id)
    if kind == "changes":
        statement = statement.where(
            *(~AuditLogEntry.action.startswith(prefix) for prefix in NOT_A_CHANGE)
        )
    elif kind is not None:
        statement = statement.where(
            or_(*(AuditLogEntry.action.startswith(prefix) for prefix in KINDS[kind]))
        )
    if since is not None:
        statement = statement.where(AuditLogEntry.at >= since)
    if until is not None:
        statement = statement.where(AuditLogEntry.at < until)
    if after is not None:
        statement = statement.where(
            tuple_(AuditLogEntry.at, AuditLogEntry.id)
            < tuple_(literal(after.at), literal(after.entry_id))
        )
    statement = statement.order_by(AuditLogEntry.at.desc(), AuditLogEntry.id.desc()).limit(
        limit + 1
    )
    return list(await session.scalars(statement))


async def names_of(
    session: AsyncSession, *, org_id: uuid.UUID, user_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """A name to show for each person, removed or not (their row stays, BR6)."""
    if not user_ids:
        return {}
    rows = await session.execute(
        select(User.id, User.name, User.email, User.removed_at).where(
            User.org_id == org_id, User.id.in_(user_ids)
        )
    )
    return {
        user_id: name or ("a removed person" if removed_at is not None else email)
        for user_id, name, email, removed_at in rows.tuples()
    }
