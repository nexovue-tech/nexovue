"""The explanation's reads and writes (F3 design, *API*; plan T1.10).

Postgres only (A32): polling a page must never re-read a day of readings. Every
statement binds `org_id`; a finding's project is proved by its row before anything
scopes on it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from nexovue_platform.evidence import GATHER_EVIDENCE_TASK, ReadReason, gather_evidence_request
from nexovue_platform.jobs import ENQUEUE_UNLESS_WAITING, WAKE
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    Finding,
    FindingExplanation,
    Job,
    Organization,
    Plan,
    Project,
)


async def version(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID, number: int | None
) -> FindingExplanation | None:
    """One version, or the latest when `number` is None."""
    statement = select(FindingExplanation).where(
        FindingExplanation.org_id == org_id, FindingExplanation.finding_id == finding_id
    )
    if number is None:
        statement = statement.order_by(FindingExplanation.version.desc()).limit(1)
    else:
        statement = statement.where(FindingExplanation.version == number)
    found: FindingExplanation | None = await session.scalar(statement)
    return found


async def latest_number(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID
) -> int | None:
    found: int | None = await session.scalar(
        select(func.max(FindingExplanation.version)).where(
            FindingExplanation.org_id == org_id, FindingExplanation.finding_id == finding_id
        )
    )
    return found


async def last_read_again(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID
) -> datetime | None:
    found: datetime | None = await session.scalar(
        select(func.max(FindingExplanation.read_again_at)).where(
            FindingExplanation.org_id == org_id, FindingExplanation.finding_id == finding_id
        )
    )
    return found


async def mark_read_again(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID, number: int, at: datetime
) -> None:
    await session.execute(
        update(FindingExplanation)
        .where(
            FindingExplanation.org_id == org_id,
            FindingExplanation.finding_id == finding_id,
            FindingExplanation.version == number,
        )
        .values(read_again_at=at)
    )


async def lock_latest(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID
) -> FindingExplanation | None:
    """The latest version, held until the transaction ends: two clicks at once are one."""
    found: FindingExplanation | None = await session.scalar(
        select(FindingExplanation)
        .where(FindingExplanation.org_id == org_id, FindingExplanation.finding_id == finding_id)
        .order_by(FindingExplanation.version.desc())
        .limit(1)
        .with_for_update()
    )
    return found


async def read_is_queued(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID
) -> bool:
    """Whether a gather of this finding still waits, or runs, and has not expired."""
    found = await session.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            Job.org_id == org_id,
            Job.task == GATHER_EVIDENCE_TASK,
            Job.kwargs["finding_id"].astext == str(finding_id),
            (Job.expires_at.is_(None)) | (Job.expires_at > func.now()),
            Job.attempts < Job.max_attempts,
        )
    )
    return bool(found)


async def shared_cause_count(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, evidence_id: str
) -> int:
    """FR47: open findings in the project whose latest version names this cause."""
    latest = (
        select(
            FindingExplanation.finding_id,
            func.max(FindingExplanation.version).label("version"),
        )
        .where(FindingExplanation.org_id == org_id, FindingExplanation.project_id == project_id)
        .group_by(FindingExplanation.finding_id)
        .subquery()
    )
    counted: int = (
        await session.scalar(
            select(func.count())
            .select_from(FindingExplanation)
            .join(
                latest,
                (latest.c.finding_id == FindingExplanation.finding_id)
                & (latest.c.version == FindingExplanation.version),
            )
            .join(
                Finding,
                (Finding.org_id == FindingExplanation.org_id)
                & (Finding.id == FindingExplanation.finding_id),
            )
            .where(
                FindingExplanation.org_id == org_id,
                Finding.state == "open",
                FindingExplanation.likely_cause["evidence_id"].astext == evidence_id,
            )
        )
        or 0
    )
    return counted


async def latest_causes(
    session: AsyncSession, *, org_id: uuid.UUID, finding_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Any]:
    """Each finding's latest stored cause and card line, for the feed (FR18). One read."""
    if not finding_ids:
        return {}
    rows = await session.execute(
        select(
            FindingExplanation.finding_id,
            FindingExplanation.likely_cause,
            FindingExplanation.summary,
        )
        .where(FindingExplanation.org_id == org_id, FindingExplanation.finding_id.in_(finding_ids))
        .distinct(FindingExplanation.finding_id)
        .order_by(FindingExplanation.finding_id, FindingExplanation.version.desc())
    )
    return {row.finding_id: row for row in rows}


async def raw_retention_days(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> int | None:
    """The project's raw retention, as `project_retention` reads it (migration 0005)."""
    found: int | None = await session.scalar(
        select(func.least(Project.retention_days, Plan.raw_retention_days))
        .join(Organization, Organization.id == Project.org_id)
        .join(Plan, Plan.slug == Organization.plan)
        .where(Project.org_id == org_id, Project.id == project_id, Project.deleted_at.is_(None))
    )
    return found


async def queue_read(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID, reason: ReadReason
) -> None:
    """A gather for a person waiting on it, unless one like it already waits (FR41)."""
    request = gather_evidence_request(finding_id, org_id, reason)
    await session.execute(text(ENQUEUE_UNLESS_WAITING), request.parameters())
    await session.execute(text(WAKE), request.wake_parameters())
