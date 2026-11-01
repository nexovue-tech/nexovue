"""Which projects detection works on, how each is set, and the lock one job holds
on it while it works (design, "Where it runs").
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from nexovue_platform.detection import DEFAULT_TIMEZONE
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from nexovue_scheduler.detection.method import SENSITIVITIES, Sensitivity
from nexovue_scheduler.detection.store import ProjectBinding

# A project is judged while it has had traffic this recently, or has a finding open.
ACTIVE_FOR: Final = timedelta(hours=24)

# Across every organization on purpose: the coordinator hands each a job. The usage
# meter's `service_last_seen` says who sent anything, read from Postgres rather
# than scanning every tenant's rollup. Live projects of live organizations only;
# `:org_id` narrows it to one organization's, or is null.
_ACTIVE: Final = """
    SELECT live.org_id, live.project_id
    FROM project_retention AS live
    WHERE (CAST(:org_id AS uuid) IS NULL OR live.org_id = CAST(:org_id AS uuid))
      AND (
          EXISTS (
              SELECT 1 FROM service_last_seen AS seen
              WHERE seen.org_id = live.org_id AND seen.project_id = live.project_id
                AND seen.last_seen_at >= :since
          )
          OR EXISTS (
              SELECT 1 FROM findings
              WHERE findings.org_id = live.org_id AND findings.project_id = live.project_id
                AND findings.state = 'open'
          )
      )
"""
_ACTIVE_PROJECTS: Final = text(_ACTIVE + " ORDER BY live.org_id, live.project_id")
# D34: the clock a stop is measured by starts when a project becomes active, so one
# that never ran, or sends again after a quiet day, is given its chance first. Only
# rows that change are written, so the sweep does not lock every project's run.
_NO_LONGER_EXPECTED: Final = text(
    f"""
    UPDATE detection_project_runs AS runs SET expected_since = NULL
    WHERE runs.expected_since IS NOT NULL
      AND (CAST(:org_id AS uuid) IS NULL OR runs.org_id = CAST(:org_id AS uuid))
      AND NOT EXISTS (
          SELECT 1 FROM ({_ACTIVE}) AS active
          WHERE active.org_id = runs.org_id AND active.project_id = runs.project_id
      )
    """
)
_NEWLY_EXPECTED: Final = text(
    f"""
    INSERT INTO detection_project_runs (org_id, project_id, expected_since)
    SELECT active.org_id, active.project_id, :now
    FROM ({_ACTIVE}) AS active
    WHERE NOT EXISTS (
        SELECT 1 FROM detection_project_runs AS runs
        WHERE runs.org_id = active.org_id AND runs.project_id = active.project_id
          AND runs.expected_since IS NOT NULL
    )
    ON CONFLICT (org_id, project_id) DO UPDATE SET expected_since = EXCLUDED.expected_since
    WHERE detection_project_runs.expected_since IS NULL
    """
)

_SETTINGS: Final = text(
    """
    SELECT timezone, detection_sensitivity FROM projects
    WHERE org_id = :org_id AND id = :project_id
    """
)

# A transaction's lock, so it is released however the transaction ends, a killed
# job's included. Keyed by purpose and project: the judging and the hourly
# baseline of one project may run together, two of either may not.
_TRY_LOCK: Final = text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))")
# The same key held by the session, for a job that commits as it goes; it ends when
# released or when the connection does, a killed job's included.
_TRY_SESSION_LOCK: Final = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))")
_UNLOCK_SESSION: Final = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")


@dataclass(frozen=True, slots=True)
class ActiveProject:
    org_id: uuid.UUID
    project_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ProjectSettings:
    zone: ZoneInfo
    sensitivity: Sensitivity


async def active_projects(connection: AsyncConnection, *, now: datetime) -> list[ActiveProject]:
    rows = await connection.execute(_ACTIVE_PROJECTS, {"since": now - ACTIVE_FOR, "org_id": None})
    return [ActiveProject(row.org_id, row.project_id) for row in rows]


async def keep_expected(
    connection: AsyncConnection, *, now: datetime, org_id: uuid.UUID | None = None
) -> None:
    """Record since when each project detection works on has been expected to be
    judged, and forget it for those it no longer works on — every organization's,
    or one's. The stop sweep's clock (D24, D34) and the API's *not running* read it."""
    keys = {"since": now - ACTIVE_FOR, "org_id": org_id, "now": now}
    await connection.execute(_NO_LONGER_EXPECTED, keys)
    await connection.execute(_NEWLY_EXPECTED, keys)


def fair_order(projects: list[ActiveProject]) -> list[ActiveProject]:
    """One project of each organization in turn, so an organization with many
    projects cannot keep the others waiting (design 2.1, M5)."""
    by_org: dict[uuid.UUID, list[ActiveProject]] = {}
    for project in projects:
        by_org.setdefault(project.org_id, []).append(project)
    queues = list(by_org.values())
    ordered: list[ActiveProject] = []
    for turn in range(max((len(queue) for queue in queues), default=0)):
        ordered.extend(queue[turn] for queue in queues if turn < len(queue))
    return ordered


async def project_settings(connection: AsyncConnection, binding: ProjectBinding) -> ProjectSettings:
    row = (
        await connection.execute(
            _SETTINGS,
            {"org_id": uuid.UUID(binding.org_id), "project_id": uuid.UUID(binding.project_id)},
        )
    ).one()
    return ProjectSettings(
        zone=ZoneInfo(row.timezone or DEFAULT_TIMEZONE),
        sensitivity=SENSITIVITIES[row.detection_sensitivity],
    )


async def try_lock(connection: AsyncConnection, purpose: str, project_id: uuid.UUID) -> bool:
    """Whether this transaction now holds the project's lock for `purpose`."""
    held = await connection.scalar(_TRY_LOCK, {"key": _lock_key(purpose, project_id)})
    return bool(held)


@asynccontextmanager
async def session_lock(
    connection: AsyncConnection, purpose: str, project_id: uuid.UUID
) -> AsyncIterator[bool]:
    """Whether this connection holds the project's lock for `purpose`, across every
    transaction inside the block, with none open when it yields. Released however the
    block ends; a connection that cannot release it is closed, which releases it too,
    so a pooled connection never carries the lock into another job."""
    key = {"key": _lock_key(purpose, project_id)}
    held = bool(await connection.scalar(_TRY_SESSION_LOCK, key))
    await connection.commit()
    if not held:
        yield False
        return
    try:
        yield True
    finally:
        try:
            await connection.rollback()
            await connection.scalar(_UNLOCK_SESSION, key)
            await connection.commit()
        except BaseException:
            await connection.invalidate()
            raise


def _lock_key(purpose: str, project_id: uuid.UUID) -> str:
    return f"detection:{purpose}:{project_id}"
