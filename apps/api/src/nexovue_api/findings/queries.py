"""The reads behind findings: Postgres for the findings, ClickHouse for their readings.

Every Postgres statement binds `org_id`. The ClickHouse statement names
`{tenant_id:String}`, which the repository binds from the caller, and
`{project_id:String}`, so one project's readings never answer for another's (AC28).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import DetectionState, Finding

# How many findings one feed read returns. Open findings sort first, so the cap
# only ever drops the oldest closed ones.
FEED_PAGE: Final = 200

# How many readings a trend draws: an hour, one reading a judged minute.
TREND_POINTS: Final = 60

# Feed order (F1 AC1.5): open, then acknowledged, then closed; critical first; then
# newest first, a closed finding by when it closed.
_STATE_RANK: Final = case(
    (Finding.state == "closed", 2),
    (Finding.acknowledged_at.is_not(None), 1),
    else_=0,
)
_SEVERITY_RANK: Final = case((Finding.severity == "critical", 0), else_=1)
_RECENCY: Final = func.coalesce(Finding.closed_at, Finding.started_at)


async def feed(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    state: str | None,
    muted: bool,
    limit: int,
) -> list[Finding]:
    """One project's findings in feed order. `state` None reads every state."""
    statement = (
        select(Finding)
        .where(Finding.org_id == org_id, Finding.project_id == project_id)
        .where(Finding.muted.is_(muted))
        .order_by(_STATE_RANK, _SEVERITY_RANK, _RECENCY.desc(), Finding.id)
        .limit(limit)
    )
    if state is not None:
        statement = statement.where(Finding.state == state)
    return list(await session.scalars(statement))


async def muted_count(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, state: str | None
) -> int:
    statement = (
        select(func.count())
        .select_from(Finding)
        .where(Finding.org_id == org_id, Finding.project_id == project_id, Finding.muted)
    )
    if state is not None:
        statement = statement.where(Finding.state == state)
    counted: int = await session.scalar(statement) or 0
    return counted


async def find_finding(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID
) -> Finding | None:
    statement = select(Finding).where(Finding.org_id == org_id, Finding.id == finding_id)
    found: Finding | None = await session.scalar(statement)
    return found


async def opened_and_false_alarms(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, since: datetime
) -> tuple[int, int]:
    """Findings opened since `since`, and how many of them are marked a false alarm."""
    statement = select(
        func.count(), func.count().filter(Finding.false_alarm_at.is_not(None))
    ).where(
        Finding.org_id == org_id,
        Finding.project_id == project_id,
        Finding.started_at >= since,
    )
    opened, false_alarms = (await session.execute(statement)).one()
    return int(opened), int(false_alarms)


async def judged_service_count(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> int:
    """Services detection has judged at least one minute of."""
    statement = select(func.count()).where(
        DetectionState.org_id == org_id,
        DetectionState.project_id == project_id,
        DetectionState.last_judged_minute.is_not(None),
    )
    counted: int = await session.scalar(statement) or 0
    return counted


# ------------------------------------------------------------------ readings

# The last readings of each named service in a range, newest first. A minute
# re-judged after a crash is written twice; `argMax(…, version)` counts it once.
_RECENT_READINGS: Final = f"""
SELECT
    service_name,
    minute,
    argMax(window_minutes, version) AS window_minutes,
    argMax(window_requests, version) AS window_requests,
    argMax(window_errors, version) AS window_errors,
    argMax(window_p95_ms, version) AS window_p95_ms
FROM detection_readings
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND service_name IN {{services:Array(String)}}
  AND minute >= {{since:DateTime}}
  AND minute <= {{until:DateTime}}
GROUP BY service_name, minute
ORDER BY service_name, minute DESC
LIMIT {TREND_POINTS} BY service_name
"""


async def recent_readings(
    repository: TenantScopedRepository,
    *,
    project_id: uuid.UUID,
    services: list[str],
    since: datetime,
    until: datetime,
) -> list[dict[str, Any]]:
    """Up to `TREND_POINTS` readings per service in `[since, until]`, newest first."""
    if not services:
        return []
    return await repository.query(
        _RECENT_READINGS,
        {
            "project_id": str(project_id),
            "services": services,
            "since": since,
            "until": until,
        },
    )
