"""The reads behind findings: Postgres for the findings, ClickHouse for their readings.

Every Postgres statement binds `org_id`. The ClickHouse statement names
`{tenant_id:String}`, which the repository binds from the caller, and
`{project_id:String}`, so one project's readings never answer for another's (AC28).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from nexovue_platform.detection import (
    RESOLVE_FINDING_SQL,
    SHIFT_COLUMNS,
    compute_baselines_request,
    resolve_parameters,
)
from nexovue_platform.jobs import ENQUEUE
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import (
    BaselineShift,
    DetectionState,
    Finding,
    FindingIncident,
    Incident,
    NotificationChannel,
    ServiceDetectionSettings,
    User,
)

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


async def lock_finding(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID
) -> Finding | None:
    """The finding, locked until the transaction ends. The project job locks it the
    same way while it judges, so a person's action and a run never interleave: the
    second waits, then sees what the first did."""
    statement = (
        select(Finding)
        .where(Finding.org_id == org_id, Finding.id == finding_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    found: Finding | None = await session.scalar(statement)
    return found


async def linked_incidents(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID
) -> list[Incident]:
    statement = (
        select(Incident)
        .join(
            FindingIncident,
            (FindingIncident.incident_id == Incident.id)
            & (FindingIncident.org_id == Incident.org_id),
        )
        .where(FindingIncident.org_id == org_id, FindingIncident.finding_id == finding_id)
        .order_by(Incident.opened_at, Incident.id)
    )
    return list(await session.scalars(statement))


async def person_name(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID
) -> str | None:
    """What the screen calls a person: their name, else their address."""
    statement = select(User.name, User.email).where(User.org_id == org_id, User.id == user_id)
    found = (await session.execute(statement)).one_or_none()
    return None if found is None else found.name or found.email


async def service_quiet(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service_name: str,
    now: datetime,
) -> bool:
    """Whether the service is muted or snoozed now: people hear nothing more of it."""
    statement = select(func.count()).where(
        ServiceDetectionSettings.org_id == org_id,
        ServiceDetectionSettings.project_id == project_id,
        ServiceDetectionSettings.service_name == service_name,
        (ServiceDetectionSettings.muted_until > now)
        | (ServiceDetectionSettings.snoozed_until > now),
    )
    return bool(await session.scalar(statement))


async def resolve(
    session: AsyncSession, finding: Finding, *, quiet: bool, at: datetime
) -> list[str]:
    """One *resolved* per channel told of the finding, as the project job writes it
    (`nexovue_platform.detection`), in this transaction. Returns the channels' names."""
    told = (
        await session.execute(
            text(RESOLVE_FINDING_SQL),
            resolve_parameters(
                org_id=finding.org_id,
                finding_id=finding.id,
                severity=finding.severity,
                at=at,
                quiet=quiet,
            ),
        )
    ).scalars()
    channel_ids = list(told)
    if not channel_ids:
        return []
    names = select(NotificationChannel.name).where(
        NotificationChannel.org_id == finding.org_id, NotificationChannel.id.in_(channel_ids)
    )
    return sorted(await session.scalars(names))


def add_shift(
    session: AsyncSession,
    finding: Finding,
    *,
    signal: str,
    ratio: float,
    from_minute: datetime,
) -> None:
    """FR21: the service's normal for one signal moves by `ratio` from `from_minute`."""
    assert finding.service_name is not None, "only a service finding has a normal"
    session.add(
        BaselineShift(
            org_id=finding.org_id,
            project_id=finding.project_id,
            service_name=finding.service_name,
            signal=signal,
            from_minute=from_minute,
            ratio=ratio,
            reason="new_normal",
        )
    )


async def queue_baselines(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    """Ask the scheduler to rebuild the project's normal now, committed with the change
    that moved it, so judging resumes on the new normal at once (AC15)."""
    request = compute_baselines_request(project_id, org_id)
    await session.execute(text(ENQUEUE), request.parameters())


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


async def opened_and_false_alarms_per_service(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, since: datetime
) -> list[tuple[str, int, int]]:
    """Per service, findings opened since `since` and how many of them are marked a false
    alarm: most false alarms first, then most opened, then by name."""
    opened = func.count()
    false_alarms = func.count().filter(Finding.false_alarm_at.is_not(None))
    statement = (
        select(Finding.service_name, opened, false_alarms)
        .where(
            Finding.org_id == org_id,
            Finding.project_id == project_id,
            Finding.started_at >= since,
            Finding.service_name.is_not(None),
        )
        .group_by(Finding.service_name)
        .order_by(false_alarms.desc(), opened.desc(), Finding.service_name)
    )
    return [
        (str(name), int(count), int(marked))
        for name, count, marked in (await session.execute(statement)).all()
    ]


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


# A finding's page reads at most a day of its minutes, the latest: the start of a
# week-long finding is in its stored values, and a chart of every minute in a week
# is no clearer than one of its last day.
PAGE_READINGS: Final = 1440

# What each signal's value and normal are read from
# (`nexovue_platform.detection.signal_reading`), and each by its name.
_READING_COLUMNS: Final = ", ".join(SHIFT_COLUMNS)
_READING_FIELDS: Final = ", ".join(
    f"latest.{index + 1} AS {column}" for index, column in enumerate(SHIFT_COLUMNS)
)

# One service's minutes in a range, newest first. One tuple, as the scheduler reads
# it, so a NULL never falls back to an older version's value.
_SERVICE_READINGS: Final = f"""
SELECT
    toUnixTimestamp(minute) AS minute_epoch,
    {_READING_FIELDS}
FROM (
    SELECT minute, argMax(tuple({_READING_COLUMNS}), version) AS latest
    FROM detection_readings
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND service_name = {{service:String}}
      AND minute >= {{since:DateTime}}
      AND minute <= {{until:DateTime}}
    GROUP BY minute
)
ORDER BY minute_epoch DESC
LIMIT {PAGE_READINGS}
"""


async def service_readings(
    repository: TenantScopedRepository,
    *,
    project_id: uuid.UUID,
    service_name: str,
    since: datetime,
    until: datetime,
) -> list[dict[str, Any]]:
    """Up to `PAGE_READINGS` of one service's readings in `[since, until]`, newest first."""
    return await repository.query(
        _SERVICE_READINGS,
        {"project_id": str(project_id), "service": service_name, "since": since, "until": until},
    )
