"""The reads behind findings: Postgres for the findings, ClickHouse for their readings.

Every Postgres statement binds `org_id`. The ClickHouse statement names
`{tenant_id:String}`, which the repository binds from the caller, and
`{project_id:String}`, so one project's readings never answer for another's (AC28).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from nexovue_platform.detection import (
    RESOLVE_FINDING_SQL,
    SHIFT_COLUMNS,
    compute_baselines_request,
    resolve_parameters,
)
from nexovue_platform.jobs import ENQUEUE
from sqlalchemy import case, func, literal, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.base import UuidType as UuidColumn
from nexovue_api.data.postgres.models import (
    AlertRule,
    AlertRuleChannel,
    BaselineShift,
    DetectionState,
    Finding,
    FindingEvent,
    FindingIncident,
    FindingNote,
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
    """The finding, locked until the transaction ends, so a person's action and a
    detection run never interleave: the second waits, then sees what the first did.

    `FOR NO KEY UPDATE`, not `FOR UPDATE` (F22 D78): no person act changes a key
    column, and `FOR UPDATE` would block the foreign key check a new joined
    finding's insert takes on its root, which can deadlock against a detection
    batch (AC101).
    """
    statement = (
        select(Finding)
        .where(Finding.org_id == org_id, Finding.id == finding_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    found: Finding | None = await session.scalar(statement)
    return found


async def find_row(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID
) -> Finding | None:
    """The row with no lock, to learn its root before locking root-first (F22,
    *Locks*): a case act locks the root, then the named finding when it must."""
    statement = (
        select(Finding)
        .where(Finding.org_id == org_id, Finding.id == finding_id)
        .execution_options(populate_existing=True)
    )
    found: Finding | None = await session.scalar(statement)
    return found


async def open_joined_finding(
    session: AsyncSession, *, org_id: uuid.UUID, root_id: uuid.UUID
) -> Finding | None:
    """The open finding joined to this root, if one is (F22 FR23). F6 allows one
    open finding per service and joins stay inside one scope and service, so at
    most one row matches."""
    statement = select(Finding).where(
        Finding.org_id == org_id,
        Finding.root_finding_id == root_id,
        Finding.state == "open",
    )
    found: Finding | None = await session.scalar(statement)
    return found


async def lock_working_member(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID
) -> User | None:
    """The would-be assignee, `FOR SHARE` so a concurrent removal or demotion
    waits until this assignment commits (F22 FR4). The caller re-checks the row."""
    statement = (
        select(User)
        .where(User.org_id == org_id, User.id == user_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    found: User | None = await session.scalar(statement)
    return found


async def active_case_on_scope(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    scope: str,
    service_name: str | None,
) -> Finding | None:
    """The one finding holding this scope's active case (BR7): the row the partial
    unique index just defended, read after it refused a second (AC14)."""
    statement = select(Finding).where(
        Finding.org_id == org_id,
        Finding.project_id == project_id,
        Finding.scope == scope,
        Finding.service_name.is_(None)
        if service_name is None
        else Finding.service_name == service_name,
        Finding.case_state == "investigating",
    )
    found: Finding | None = await session.scalar(statement)
    return found


async def assignment_email_sent_since(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    root_id: uuid.UUID,
    assignee_id: uuid.UUID,
    since: datetime,
) -> bool:
    """FR5's 5-minute limit, from `assigned` events alone (D91): one to this
    recipient on this root, by someone else, since `since`. Read under the root's
    lock, so two assignments at one moment cannot both pass."""
    statement = select(func.count()).where(
        FindingEvent.org_id == org_id,
        FindingEvent.finding_id == root_id,
        FindingEvent.kind == "assigned",
        FindingEvent.at >= since,
        FindingEvent.detail["assignee_id"].astext == str(assignee_id),
        FindingEvent.actor_id.is_not(None),
        FindingEvent.actor_id != assignee_id,
    )
    return bool(await session.scalar(statement))


async def note_count(session: AsyncSession, *, org_id: uuid.UUID, root_id: uuid.UUID) -> int:
    counted: int = (
        await session.scalar(
            select(func.count()).where(
                FindingNote.org_id == org_id, FindingNote.finding_id == root_id
            )
        )
        or 0
    )
    return counted


async def find_note(
    session: AsyncSession, *, org_id: uuid.UUID, root_id: uuid.UUID, note_id: uuid.UUID
) -> FindingNote | None:
    statement = select(FindingNote).where(
        FindingNote.org_id == org_id,
        FindingNote.finding_id == root_id,
        FindingNote.id == note_id,
    )
    found: FindingNote | None = await session.scalar(statement)
    return found


async def lock_assigned_cases_skip_locked(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID
) -> list[Finding]:
    """A person's active cases, locked `FOR NO KEY UPDATE SKIP LOCKED` (AC10): a
    row the detection job holds is skipped and healed by the next sweep, so
    clearing an assignee can never deadlock against a batch."""
    statement = (
        select(Finding)
        .where(
            Finding.org_id == org_id,
            Finding.assignee_id == user_id,
            Finding.case_state == "investigating",
        )
        .with_for_update(key_share=True, skip_locked=True)
        .execution_options(populate_existing=True)
    )
    return list(await session.scalars(statement))


# The timeline's page (FR17): the newest rows first; `before` reads further back.
TIMELINE_PAGE: Final = 100


async def timeline(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    root_id: uuid.UUID,
    before: tuple[datetime, uuid.UUID] | None,
    limit: int = TIMELINE_PAGE,
) -> list[tuple[FindingEvent, str | None]]:
    """One case's timeline (NFR3): the root's events and its joined findings',
    newest first, each with its note's words when it has one. A deleted note
    joins to nothing, so its text is gone the moment the row is (AC19)."""
    joined_findings = select(Finding.id).where(
        Finding.org_id == org_id, Finding.root_finding_id == root_id
    )
    note_id = FindingEvent.detail["note_id"].astext.cast(UuidColumn)
    statement = (
        select(FindingEvent, FindingNote.body)
        .outerjoin(
            FindingNote,
            (FindingNote.org_id == FindingEvent.org_id) & (FindingNote.id == note_id),
        )
        .where(
            FindingEvent.org_id == org_id,
            (FindingEvent.finding_id == root_id) | FindingEvent.finding_id.in_(joined_findings),
        )
        .order_by(FindingEvent.at.desc(), FindingEvent.id.desc())
        .limit(limit)
    )
    if before is not None:
        before_at, before_id = before
        statement = statement.where(
            tuple_(FindingEvent.at, FindingEvent.id)
            < tuple_(literal(before_at), literal(before_id))
        )
    return [(event, body) for event, body in (await session.execute(statement)).all()]


async def latest_assignment_at(
    session: AsyncSession, *, org_id: uuid.UUID, root_id: uuid.UUID, assignee_id: uuid.UUID
) -> datetime | None:
    """When the current assignee took or was given the case (D76): the newest
    `assigned` event naming them, read from the timeline rather than stored twice."""
    found: datetime | None = await session.scalar(
        select(func.max(FindingEvent.at)).where(
            FindingEvent.org_id == org_id,
            FindingEvent.finding_id == root_id,
            FindingEvent.kind == "assigned",
            FindingEvent.detail["assignee_id"].astext == str(assignee_id),
        )
    )
    return found


async def open_joined_of(
    session: AsyncSession, *, org_id: uuid.UUID, root_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Finding]:
    """Each root's open joined finding, in one read, for the feed (F22 FR23)."""
    if not root_ids:
        return {}
    statement = select(Finding).where(
        Finding.org_id == org_id,
        Finding.root_finding_id.in_(root_ids),
        Finding.state == "open",
    )
    rows = await session.scalars(statement)
    return {row.root_finding_id: row for row in rows if row.root_finding_id is not None}


async def person_names(
    session: AsyncSession, *, org_id: uuid.UUID, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """What the screen calls each person: their name, else their address."""
    if not user_ids:
        return {}
    statement = select(User.id, User.name, User.email).where(
        User.org_id == org_id, User.id.in_(user_ids)
    )
    return {row.id: row.name or row.email for row in (await session.execute(statement)).all()}


async def project_has_pagerduty(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> bool:
    """Whether an enabled rule of this project reaches a PagerDuty channel (FR48),
    so the page can say the page there is not acknowledged by Take this."""
    statement = (
        select(func.count())
        .select_from(AlertRule)
        .join(
            AlertRuleChannel,
            (AlertRuleChannel.rule_id == AlertRule.id)
            & (AlertRuleChannel.org_id == AlertRule.org_id),
        )
        .join(
            NotificationChannel,
            (NotificationChannel.id == AlertRuleChannel.channel_id)
            & (NotificationChannel.org_id == AlertRuleChannel.org_id),
        )
        .where(
            AlertRule.org_id == org_id,
            AlertRule.project_id == project_id,
            AlertRule.enabled,
            NotificationChannel.kind == "pagerduty",
        )
    )
    return bool(await session.scalar(statement))


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


@dataclass(frozen=True, slots=True)
class ServiceQuiet:
    """A service's snooze and mute, each only while it is still ahead."""

    snoozed_until: datetime | None
    muted_until: datetime | None


async def quiet_services(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, now: datetime
) -> dict[str, ServiceQuiet]:
    """The project's services that are snoozed or muted right now, by name."""
    settings = ServiceDetectionSettings
    rows = await session.execute(
        select(settings.service_name, settings.snoozed_until, settings.muted_until).where(
            settings.org_id == org_id,
            settings.project_id == project_id,
            or_(settings.snoozed_until > now, settings.muted_until > now),
        )
    )
    return {
        name: ServiceQuiet(
            snoozed_until=snoozed if snoozed is not None and snoozed > now else None,
            muted_until=muted if muted is not None and muted > now else None,
        )
        for name, snoozed, muted in rows
    }
