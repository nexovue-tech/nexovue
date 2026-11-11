"""What a person's act on a case asks of its synced issue (F22 FR38; design, *Operations
on a case*): the issue follows the case through `wanted_state`, which
`scheduler-connections` reconciles, and a sync job is added for the connection.

Kept apart from `links.py` so `cases.py`, which every act runs through, can call it
without importing the link routes' code.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from nexovue_platform.cases import (
    CLEAR_HOLD_SQL,
    SET_WANTED_STATE_SQL,
    clear_hold_parameters,
    set_wanted_state_parameters,
)
from nexovue_platform.connections import COMMENT_FOR_RESOLUTION, STOPPED, SYNCED, sync_job
from nexovue_platform.jobs import ENQUEUE_UNLESS_WAITING, WAKE
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.data.postgres.models import FindingLink


async def want_for_resolve(
    session: AsyncSession,
    root: FindingRow,
    *,
    resolution: str,
    leave_issue_open: bool,
    at: datetime,
) -> None:
    """A person's ending: the synced issue closes, with one comment, unless they chose
    to leave it open (D72) or it is already closed in the tracker (a hold, D73).
    Whatever resolves a case ends its hold, in this transaction."""
    held = await session.execute(
        text(CLEAR_HOLD_SQL), clear_hold_parameters(org_id=root.org_id, finding_id=root.id)
    )
    if held.first() is not None or leave_issue_open:
        return
    await _want(
        session,
        root,
        wanted="closed",
        comment=COMMENT_FOR_RESOLUTION.get(resolution),
        at=at,
    )


async def want_for_reopen(session: AsyncSession, root: FindingRow, *, at: datetime) -> None:
    """A person's reopen: the issue reopens, with no comment; if it was left open,
    nothing is pending."""
    await _want(session, root, wanted="open", comment=None, at=at)


async def _want(
    session: AsyncSession, root: FindingRow, *, wanted: str, comment: str | None, at: datetime
) -> None:
    changed = await session.execute(
        text(SET_WANTED_STATE_SQL),
        set_wanted_state_parameters(
            org_id=root.org_id,
            finding_id=root.id,
            wanted_state=wanted,
            comment_wanted=comment,
            at=at,
        ),
    )
    if changed.first() is None:
        return
    connection_id = await session.scalar(
        select(FindingLink.connection_id).where(
            FindingLink.org_id == root.org_id,
            FindingLink.finding_id == root.id,
            FindingLink.role == SYNCED,
            FindingLink.sync_status != STOPPED,
        )
    )
    if connection_id is not None:
        await ask_sync(session, org_id=root.org_id, connection_id=connection_id, wake=False)


async def ask_sync(
    session: AsyncSession, *, org_id: uuid.UUID, connection_id: uuid.UUID, wake: bool
) -> None:
    """One sync job for the connection unless one already waits (AC48), committed with
    the act; a click a person waits on wakes the workers."""
    job = sync_job(org_id=str(org_id), connection_id=str(connection_id))
    await session.execute(text(ENQUEUE_UNLESS_WAITING), job.parameters())
    if wake:
        await session.execute(text(WAKE), job.wake_parameters())
