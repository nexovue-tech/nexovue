"""Which projects detection works on, how each is set, and the lock one job holds
on it while it works (design, "Where it runs").
"""

from __future__ import annotations

import uuid
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
# than scanning every tenant's rollup. Live projects of live organizations only.
_ACTIVE_PROJECTS: Final = text(
    """
    SELECT live.org_id, live.project_id
    FROM project_retention AS live
    WHERE EXISTS (
        SELECT 1 FROM service_last_seen AS seen
        WHERE seen.org_id = live.org_id AND seen.project_id = live.project_id
          AND seen.last_seen_at >= :since
    )
    OR EXISTS (
        SELECT 1 FROM findings
        WHERE findings.org_id = live.org_id AND findings.project_id = live.project_id
          AND findings.state = 'open'
    )
    ORDER BY live.org_id, live.project_id
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


@dataclass(frozen=True, slots=True)
class ActiveProject:
    org_id: uuid.UUID
    project_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ProjectSettings:
    zone: ZoneInfo
    sensitivity: Sensitivity


async def active_projects(connection: AsyncConnection, *, now: datetime) -> list[ActiveProject]:
    rows = await connection.execute(_ACTIVE_PROJECTS, {"since": now - ACTIVE_FOR})
    return [ActiveProject(row.org_id, row.project_id) for row in rows]


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
    held = await connection.scalar(_TRY_LOCK, {"key": f"detection:{purpose}:{project_id}"})
    return bool(held)
