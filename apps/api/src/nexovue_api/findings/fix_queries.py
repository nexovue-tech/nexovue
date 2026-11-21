"""The API's reads and writes of a finding's suggestions (F5, `finding_fixes`; plan T2b.7).

Every statement binds `org_id`, and each read of one finding binds it too. Postgres only:
the page polls these, so they read no ClickHouse and call no provider (AC8's last half).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from nexovue_platform.fixes.records import (
    COUNTED_STATES,
    IN_FLIGHT_STATES,
    KIND_GENERAL,
    PROPOSING,
    GeneralLanguage,
)
from nexovue_platform.jobs import ENQUEUE, WAKE, JobRequest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import FindingFix

# The latest and the quiet list of older ones (FR10a), newest first.
FIXES_LISTED: Final = 20


async def recent_fixes(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID, kind: str
) -> list[FindingFix]:
    """One rung's suggestions for the finding: rung 2b's read lists `general`, rung 3's
    `source`, so neither reads the other's states as its own."""
    rows = await session.scalars(
        select(FindingFix)
        .where(
            FindingFix.org_id == org_id,
            FindingFix.finding_id == finding_id,
            FindingFix.kind == kind,
        )
        .order_by(FindingFix.requested_at.desc(), FindingFix.id.desc())
        .limit(FIXES_LISTED)
    )
    return list(rows)


async def fix_of(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID, fix_id: uuid.UUID
) -> FindingFix | None:
    found: FindingFix | None = await session.scalar(
        select(FindingFix).where(
            FindingFix.org_id == org_id,
            FindingFix.finding_id == finding_id,
            FindingFix.id == fix_id,
        )
    )
    return found


async def counted_since(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID, since: datetime
) -> list[datetime]:
    """When each of the finding's tries in the window was asked: rows the model answered
    (D32), so a provider outage never uses one. A source row counts once its files were
    sent (FR15c), so reading files, listing them or a refusal before Send uses none."""
    rows = await session.scalars(
        select(FindingFix.requested_at).where(
            FindingFix.org_id == org_id,
            FindingFix.finding_id == finding_id,
            FindingFix.state.in_(COUNTED_STATES),
            FindingFix.requested_at > since,
            (FindingFix.kind == KIND_GENERAL) | FindingFix.sent_at.is_not(None),
        )
    )
    return list(rows)


async def in_flight(session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID) -> bool:
    """BR8: a suggestion for this finding is being worked on, of either kind."""
    found = await session.scalar(
        select(func.count())
        .select_from(FindingFix)
        .where(
            FindingFix.org_id == org_id,
            FindingFix.finding_id == finding_id,
            FindingFix.state.in_(IN_FLIGHT_STATES),
        )
    )
    return bool(found)


def new_general(
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    finding_id: uuid.UUID,
    explanation_version: int,
    language: GeneralLanguage,
    requested_by: uuid.UUID,
    at: datetime,
) -> FindingFix:
    return FindingFix(
        id=uuid.uuid4(),
        org_id=org_id,
        project_id=project_id,
        finding_id=finding_id,
        kind=KIND_GENERAL,
        explanation_version=explanation_version,
        state=PROPOSING,
        state_changed_at=at,
        general_language=language,
        requested_by=requested_by,
        requested_at=at,
    )


async def queue_suggestion(session: AsyncSession, request: JobRequest) -> None:
    """The job, in the click's transaction, waking the AI role when it commits."""
    await session.execute(text(ENQUEUE), request.parameters())
    await session.execute(text(WAKE), request.wake_parameters())
