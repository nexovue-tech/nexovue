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
from sqlalchemy import String, case, func, literal, or_, select, text, tuple_
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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
    Organization,
    Plan,
    Project,
    ServiceDetectionSettings,
    User,
)

# How many findings one feed read returns. Open findings sort first, so the cap
# only ever drops the oldest closed ones.
FEED_PAGE: Final = 200

# How many readings a trend draws: an hour, one reading a judged minute.
TREND_POINTS: Final = 60

# ------------------------------------------------------------------ the feed

# The one rank (F1 AC1.5, F22 FR21), shared by the feed, its case filters and
# AC27's test set, which the console's `orderFindings` must agree with: open and
# unacknowledged, or a root whose case has an open joined finding; acknowledged;
# closed with an active case; other closed. Then the case's severity — the worse
# of the row's and an open joined finding's — then newest first, a closed
# finding by when it closed. Each `EXISTS` is served by the partial index on
# `(org_id, root_finding_id)`.
_JOINED = aliased(Finding, name="joined")


def _open_joined(*, critical: bool = False) -> Any:
    found = select(1).where(
        _JOINED.org_id == Finding.org_id,
        _JOINED.root_finding_id == Finding.id,
        _JOINED.state == "open",
    )
    if critical:
        found = found.where(_JOINED.severity == "critical")
    return found.exists()


CASE_RANK: Final = case(
    ((Finding.state == "open") & Finding.acknowledged_at.is_(None), 0),
    (_open_joined() & (Finding.case_state == "investigating"), 0),
    (Finding.state == "open", 1),
    (Finding.case_state == "investigating", 2),
    else_=3,
)
CASE_SEVERITY_RANK: Final = case(
    ((Finding.severity == "critical") | _open_joined(critical=True), 0), else_=1
)
_RECENCY: Final = func.coalesce(Finding.closed_at, Finding.started_at)
FEED_ORDER: Final = (CASE_RANK, CASE_SEVERITY_RANK, _RECENCY.desc(), Finding.id)

# When the current assignee took or was given the case (D76): the newest
# `assigned` event naming them, read in the feed's statement (AC71).
_TAKEN_AT: Final = (
    select(func.max(FindingEvent.at))
    .where(
        FindingEvent.org_id == Finding.org_id,
        FindingEvent.finding_id == Finding.id,
        FindingEvent.kind == "assigned",
        FindingEvent.detail["assignee_id"].astext == sql_cast(Finding.assignee_id, String),
    )
    .scalar_subquery()
)


@dataclass(frozen=True, slots=True)
class FeedRow:
    """One feed row with what a person's row names, from the one statement."""

    finding: Finding
    assignee_name: str | None
    case_taken_at: datetime | None
    case_resolved_by_name: str | None
    project_name: str


_ASSIGNEE = aliased(User, name="assignee")
_RESOLVER = aliased(User, name="resolver")


def _feed_statement(org_id: uuid.UUID) -> Any:
    """Findings of `org_id` with their project's name, who has the case and when
    they took it, and who resolved it: a person's row needs no second read (AC71)."""
    return (
        select(
            Finding,
            func.coalesce(_ASSIGNEE.name, _ASSIGNEE.email).label("assignee_name"),
            _TAKEN_AT.label("case_taken_at"),
            func.coalesce(_RESOLVER.name, _RESOLVER.email).label("case_resolved_by_name"),
            Project.name.label("project_name"),
        )
        .join(
            Project,
            (Project.org_id == Finding.org_id) & (Project.id == Finding.project_id),
        )
        .outerjoin(
            _ASSIGNEE,
            (_ASSIGNEE.org_id == Finding.org_id) & (_ASSIGNEE.id == Finding.assignee_id),
        )
        .outerjoin(
            _RESOLVER,
            (_RESOLVER.org_id == Finding.org_id) & (_RESOLVER.id == Finding.case_resolved_by),
        )
        .where(Finding.org_id == org_id, Project.deleted_at.is_(None))
    )


def _feed_rows(result: Any) -> list[FeedRow]:
    return [
        FeedRow(
            finding=row.Finding,
            assignee_name=row.assignee_name,
            case_taken_at=row.case_taken_at,
            case_resolved_by_name=row.case_resolved_by_name,
            project_name=row.project_name,
        )
        for row in result
    ]


async def feed(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    state: str | None,
    muted: bool,
    limit: int,
) -> list[FeedRow]:
    """One project's findings in feed order. `state` None reads every state."""
    statement = (
        _feed_statement(org_id)
        .where(Finding.project_id == project_id, Finding.muted.is_(muted))
        .order_by(*FEED_ORDER)
        .limit(limit)
    )
    if state is not None:
        statement = statement.where(Finding.state == state)
    return _feed_rows(await session.execute(statement))


async def case_feed(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    which: str,
    assignee_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    limit: int,
) -> list[FeedRow]:
    """A case filter (F22 FR22): roots holding a case, muted included, across the
    organization, or one project for a token. A joined finding holds no case of
    its own, so it is never a row here. *Resolved cases* reads newest first; the
    other two in the feed's rank."""
    statement = _feed_statement(org_id).limit(limit)
    if project_id is not None:
        statement = statement.where(Finding.project_id == project_id)
    if which == "resolved":
        return _feed_rows(
            await session.execute(
                statement.where(Finding.case_state == "resolved").order_by(
                    Finding.case_resolved_at.desc(), Finding.id
                )
            )
        )
    statement = statement.where(Finding.case_state == "investigating").order_by(*FEED_ORDER)
    if which == "mine":
        statement = statement.where(Finding.assignee_id == assignee_id)
    return _feed_rows(await session.execute(statement))


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


async def latest_joined_closes(
    session: AsyncSession, *, org_id: uuid.UUID, root_ids: list[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """Each root's latest-closing joined finding, in one read: with the root's own
    close, when an untouched case's 72 hours start (FR49)."""
    if not root_ids:
        return {}
    statement = (
        select(Finding.root_finding_id, func.max(Finding.closed_at))
        .where(
            Finding.org_id == org_id,
            Finding.root_finding_id.in_(root_ids),
            Finding.closed_at.is_not(None),
        )
        .group_by(Finding.root_finding_id)
    )
    return {
        root: closed
        for root, closed in (await session.execute(statement)).tuples()
        if root is not None and closed is not None
    }


async def joined_count(session: AsyncSession, *, org_id: uuid.UUID, root_id: uuid.UUID) -> int:
    """How many findings that joined this case are still kept (FR24): each keeps
    its own retention, so the ones the sweep deleted are simply not counted."""
    counted: int = (
        await session.scalar(
            select(func.count()).where(Finding.org_id == org_id, Finding.root_finding_id == root_id)
        )
        or 0
    )
    return counted


async def earlier_case(session: AsyncSession, finding: Finding) -> Finding | None:
    """The scope's latest other case, opened by a finding that started before this
    one (FR9, FR13): an active one this finding missed joining, or a resolved one.
    Served by the organization-wide active and resolved case indexes."""
    statement = (
        select(Finding)
        .where(
            Finding.org_id == finding.org_id,
            Finding.project_id == finding.project_id,
            Finding.scope == finding.scope,
            Finding.service_name.is_(None)
            if finding.service_name is None
            else Finding.service_name == finding.service_name,
            Finding.case_state.is_not(None),
            Finding.id != finding.id,
            Finding.started_at < finding.started_at,
        )
        .order_by(Finding.case_opened_at.desc(), Finding.id.desc())
        .limit(1)
    )
    found: Finding | None = await session.scalar(statement)
    return found


async def derived_retention_days(session: AsyncSession, *, org_id: uuid.UUID) -> int | None:
    """The organization's plan's derived retention, as `project_retention` reads it
    (migration 0005): how long a resolved case's finding is kept (FR25)."""
    found: int | None = await session.scalar(
        select(Plan.derived_retention_days)
        .join(Organization, Organization.plan == Plan.slug)
        .where(Organization.id == org_id, Organization.deleted_at.is_(None))
    )
    return found


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
    quiet = await quiet_services_of(session, org_id=org_id, project_ids=[project_id], now=now)
    return {service: found for (_, service), found in quiet.items()}


async def quiet_services_of(
    session: AsyncSession, *, org_id: uuid.UUID, project_ids: list[uuid.UUID], now: datetime
) -> dict[tuple[uuid.UUID, str], ServiceQuiet]:
    """The services of these projects snoozed or muted right now, by project and
    name, in one read: the case filters' rows span projects (F22 FR22)."""
    if not project_ids:
        return {}
    settings = ServiceDetectionSettings
    rows = await session.execute(
        select(
            settings.project_id, settings.service_name, settings.snoozed_until, settings.muted_until
        ).where(
            settings.org_id == org_id,
            settings.project_id.in_(project_ids),
            or_(settings.snoozed_until > now, settings.muted_until > now),
        )
    )
    return {
        (project, name): ServiceQuiet(
            snoozed_until=snoozed if snoozed is not None and snoozed > now else None,
            muted_until=muted if muted is not None and muted > now else None,
        )
        for project, name, snoozed, muted in rows
    }
