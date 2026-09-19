"""The audit log screen's read (F26, FR36). No HTTP; the router owns that."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import queries
from nexovue_api.audit.cursor import AuditPageCursor, decode_cursor
from nexovue_api.audit.schemas import AuditActor, AuditEntry, AuditPage, EventKind
from nexovue_api.data.postgres.models import AuditLogEntry

DEFAULT_PAGE: Final = 50
MAX_PAGE: Final = 200

_PERSON_TARGET: Final = "user:"


async def read_page(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    kind: EventKind | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_PAGE,
) -> AuditPage:
    rows = await queries.page(
        session,
        org_id=org_id,
        actor_id=actor_id,
        kind=kind,
        since=since,
        until=until,
        after=decode_cursor(cursor) if cursor else None,
        limit=limit,
    )
    more = len(rows) > limit
    rows = rows[:limit]
    people = {row.actor_user_id for row in rows if row.actor_user_id is not None}
    people |= {target for row in rows if (target := _person_target(row)) is not None}
    names = await queries.names_of(session, org_id=org_id, user_ids=people)
    last = rows[-1] if rows else None
    return AuditPage(
        entries=[_entry(row, names) for row in rows],
        next_cursor=AuditPageCursor(at=last.at, entry_id=last.id).encode()
        if more and last is not None
        else None,
    )


def _entry(row: AuditLogEntry, names: dict[uuid.UUID, str]) -> AuditEntry:
    target = _person_target(row)
    return AuditEntry(
        id=row.id,
        at=row.at,
        actor=_actor(row, names),
        action=row.action,
        target=row.target,
        target_name=names.get(target) if target is not None else None,
        detail=row.detail,
        impersonation_context=row.impersonation_context,
    )


def _actor(row: AuditLogEntry, names: dict[uuid.UUID, str]) -> AuditActor:
    if row.actor_user_id is not None:
        return AuditActor(kind="person", id=row.actor_user_id, name=names.get(row.actor_user_id))
    if row.actor_label is not None:
        return AuditActor(kind="staff", id=None, name=row.actor_label)
    return AuditActor(kind="system", id=None, name=None)


def _person_target(row: AuditLogEntry) -> uuid.UUID | None:
    if not row.target.startswith(_PERSON_TARGET):
        return None
    try:
        return uuid.UUID(row.target.removeprefix(_PERSON_TARGET))
    except ValueError:
        return None
