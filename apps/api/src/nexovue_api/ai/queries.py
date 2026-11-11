"""The AI settings' reads and writes. Every tenant statement takes `org_id` explicitly.

Two read the catalog rather than a tenant's rows: whether `setup` made the AI account at all
(AC71), and whether it could take the advisory functions from it (AC43's *Beat lock exposed*).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit.service import AI_DISCLOSURE_AGREED
from nexovue_api.data.postgres.models import (
    AIConnection,
    AIProjectOptOut,
    AIRun,
    AIUsageMonth,
    AuditLogEntry,
    FindingExplanation,
    Job,
    JobResult,
    Project,
    User,
)

RUN_LIST_LIMIT: Final = 100


async def connection_of(
    session: AsyncSession, *, org_id: uuid.UUID, lock: bool = False
) -> AIConnection | None:
    statement = select(AIConnection).where(AIConnection.org_id == org_id)
    if lock:
        statement = statement.with_for_update()
    connection: AIConnection | None = await session.scalar(statement)
    return connection


async def delete_connection(session: AsyncSession, *, org_id: uuid.UUID) -> None:
    await session.execute(delete(AIConnection).where(AIConnection.org_id == org_id))


@dataclass(frozen=True, slots=True)
class Person:
    name: str | None
    left: bool


async def person(session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID) -> Person | None:
    row = (
        await session.execute(
            select(User.name, User.role, User.removed_at).where(
                User.org_id == org_id, User.id == user_id
            )
        )
    ).one_or_none()
    if row is None:
        return None
    return Person(name=row.name, left=row.removed_at is not None or row.role != "owner")


async def owner_addresses(session: AsyncSession, *, org_id: uuid.UUID) -> list[str]:
    """Each owner who can read mail: active, and past their invitation (AC102)."""
    rows = await session.scalars(
        select(User.email).where(
            User.org_id == org_id,
            User.role == "owner",
            User.removed_at.is_(None),
            User.password_hash.is_not(None),
        )
    )
    return list(rows)


async def newest_input(session: AsyncSession, *, org_id: uuid.UUID) -> FindingExplanation | None:
    """The newest explanation with facts a model may be sent, in a live project that is not
    opted out (FR25, AC39): what the preview shows."""
    opted_out = select(AIProjectOptOut.project_id).where(AIProjectOptOut.org_id == org_id)
    live = select(Project.id).where(Project.org_id == org_id, Project.deleted_at.is_(None))
    explanation: FindingExplanation | None = await session.scalar(
        select(FindingExplanation)
        .where(
            FindingExplanation.org_id == org_id,
            FindingExplanation.model_input.is_not(None),
            FindingExplanation.project_id.in_(live),
            FindingExplanation.project_id.not_in(opted_out),
        )
        .order_by(FindingExplanation.read_at.desc(), FindingExplanation.version.desc())
        .limit(1)
    )
    return explanation


async def explanation(
    session: AsyncSession, *, org_id: uuid.UUID, explanation_id: uuid.UUID
) -> FindingExplanation | None:
    found: FindingExplanation | None = await session.scalar(
        select(FindingExplanation).where(
            FindingExplanation.org_id == org_id, FindingExplanation.id == explanation_id
        )
    )
    return found


async def live_project(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Project | None:
    """The project, only if it is this organization's and not deleted."""
    project: Project | None = await session.scalar(
        select(Project).where(
            Project.org_id == org_id, Project.id == project_id, Project.deleted_at.is_(None)
        )
    )
    return project


async def projects_with_opt_outs(
    session: AsyncSession, *, org_id: uuid.UUID
) -> list[tuple[uuid.UUID, str, bool]]:
    opted = (
        select(AIProjectOptOut.project_id)
        .where(AIProjectOptOut.org_id == org_id, AIProjectOptOut.project_id == Project.id)
        .exists()
    )
    rows = await session.execute(
        select(Project.id, Project.name, opted)
        .where(Project.org_id == org_id, Project.deleted_at.is_(None))
        .order_by(Project.name, Project.id)
    )
    return [(row[0], row[1], bool(row[2])) for row in rows]


async def opt_out(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    person_id: uuid.UUID,
    now: datetime,
) -> bool:
    """Opted out now, and that project's wording waiting for a run turned off in the same
    transaction, so a queued job finds nothing to do (FR28, AC39). True if it was new."""
    added = await session.execute(
        insert(AIProjectOptOut)
        .values(org_id=org_id, project_id=project_id, created_by=person_id, created_at=now)
        .on_conflict_do_nothing()
    )
    await session.execute(
        update(FindingExplanation)
        .where(
            FindingExplanation.org_id == org_id,
            FindingExplanation.project_id == project_id,
            FindingExplanation.wording_state == "pending",
        )
        .values(wording_state="off", wording_changed_at=now)
    )
    return bool(getattr(added, "rowcount", 0))


async def opt_in(session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> bool:
    removed = await session.execute(
        delete(AIProjectOptOut).where(
            AIProjectOptOut.org_id == org_id, AIProjectOptOut.project_id == project_id
        )
    )
    return bool(getattr(removed, "rowcount", 0))


async def usage_month(
    session: AsyncSession, *, org_id: uuid.UUID, month: date
) -> AIUsageMonth | None:
    usage: AIUsageMonth | None = await session.scalar(
        select(AIUsageMonth).where(AIUsageMonth.org_id == org_id, AIUsageMonth.month == month)
    )
    return usage


async def run_tokens(
    session: AsyncSession, *, org_id: uuid.UUID, since: datetime, until: datetime
) -> tuple[int, int]:
    """Input and output tokens of the month's runs, for the cost estimate (FR29)."""
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(AIRun.input_tokens), 0),
                func.coalesce(func.sum(AIRun.output_tokens), 0),
            ).where(AIRun.org_id == org_id, AIRun.started_at >= since, AIRun.started_at < until)
        )
    ).one()
    return int(row[0]), int(row[1])


async def runs(session: AsyncSession, *, org_id: uuid.UUID) -> list[AIRun]:
    found = await session.scalars(
        select(AIRun)
        .where(AIRun.org_id == org_id)
        .order_by(AIRun.started_at.desc(), AIRun.id.desc())
        .limit(RUN_LIST_LIMIT)
    )
    return list(found)


async def run(session: AsyncSession, *, org_id: uuid.UUID, run_id: uuid.UUID) -> AIRun | None:
    found: AIRun | None = await session.scalar(
        select(AIRun).where(AIRun.org_id == org_id, AIRun.id == run_id)
    )
    return found


async def consented_hosts(
    session: AsyncSession, *, org_id: uuid.UUID
) -> list[tuple[str, datetime]]:
    """Each host an owner agreed to send facts to, and when: the audit row is the record, as
    a broken-into AI account could insert a false run but cannot write `audit_log` (AC72)."""
    rows = await session.execute(
        select(AuditLogEntry.detail, AuditLogEntry.at).where(
            AuditLogEntry.org_id == org_id, AuditLogEntry.action == AI_DISCLOSURE_AGREED
        )
    )
    hosts: list[tuple[str, datetime]] = []
    for detail, at in rows:
        host = (detail or {}).get("new_host")
        if isinstance(host, str):
            hosts.append((host, at))
    return hosts


async def check_payload(
    session: AsyncSession, *, org_id: uuid.UUID, request_id: uuid.UUID, kind: str
) -> dict[str, Any] | None:
    payload: dict[str, Any] | None = await session.scalar(
        select(JobResult.payload).where(
            JobResult.request_id == request_id, JobResult.org_id == org_id, JobResult.kind == kind
        )
    )
    return payload


async def check_queued(
    session: AsyncSession, *, org_id: uuid.UUID, request_id: uuid.UUID, task: str
) -> bool:
    """Whether this organization's test job is still waiting or running."""
    found = await session.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            Job.org_id == org_id,
            Job.task == task,
            Job.kwargs["request_id"].astext == str(request_id),
        )
    )
    return bool(found)
