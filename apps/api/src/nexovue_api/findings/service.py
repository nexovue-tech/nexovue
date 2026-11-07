"""Findings (F6, ADR-022): the feed, one finding and its page, the false-finding count
(FR26), and what a person may do to one (FR19 to FR21).

The scheduler's project job opens, updates and closes findings. A person
acknowledges one, calls it a false alarm, or makes its level the new normal; each
locks the row first, as the job does, so the two never interleave, and each
refuses a finding that changed since it was shown (409). A finding's own row
carries everything it says. Its trend and chart come from the frozen readings,
which expire after 35 days (AC40), so an old finding has neither.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_platform.detection import (
    SHIFT_READINGS,
    SHIFT_READINGS_NEEDED,
    normal_band,
    shift_ratio,
    signal_reading,
)
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.permissions import Action, may
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.data.postgres.models import User
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.findings import cases, queries
from nexovue_api.findings.schemas import (
    FeedState,
    Finding,
    FindingChanged,
    FindingDetail,
    FindingForMember,
    FindingListResponse,
    FindingSignal,
    FindingState,
    FindingStats,
    LinkedIncident,
    NewNormalPreview,
    ProposedShift,
    ServiceFindingStats,
    SignalName,
    SignalPoint,
    SignalSeries,
    SignalUnit,
    StoredSignal,
    TimelineEvent,
    TimelineEventForMember,
    TimelineResponse,
)

NOT_FOUND: Final = "No such finding."
# Each says what changed, because the page reloads to show it (design, *action failed*).
ALREADY_HANDLED: Final = "This finding has closed or was already acknowledged since you opened it."
NOT_A_FALSE_ALARM: Final = (
    "This finding was already marked, or closed in a way a false alarm does not undo."
)
NO_LONGER_OPEN: Final = "This finding has closed since you opened it."
NO_NORMAL_OF_ITS_OWN: Final = (
    "A finding about the whole project has no normal of its own to move. "
    "Mark it a false alarm, or let it recover."
)
TOO_FEW_READINGS: Final = (
    "The new level is not known well enough yet: {readings} of the {needed} readings it "
    "needs. Try again in a few minutes."
)

# The page's chart starts this long before a finding opened, so its rise is seen.
PAGE_LEAD: Final = timedelta(hours=1)

# FR26's window: a week, the budget's own unit, unless the caller asks for another.
DEFAULT_STATS_DAYS: Final = 7
MAX_STATS_DAYS: Final = 90
DAYS_PER_WEEK: Final = 7

# How far back a trend looks for its readings: room for TREND_POINTS judged
# minutes with gaps, while bounding what the statement scans.
TREND_LOOKBACK: Final = timedelta(hours=3)

SIGNAL_UNITS: Final[dict[SignalName, SignalUnit]] = {
    "slower": "ms",
    "failing": "%",
    "traffic": "rpm",
}
PERCENT: Final = 100

# The stored states a feed request reads; None reads both.
_STORED_STATE: Final[dict[FeedState, str | None]] = {
    "open": "open",
    "closed": "closed",
    "all": None,
}

_STORED_SIGNALS: Final = TypeAdapter(list[StoredSignal])


async def list_findings(
    session: AsyncSession,
    repository: TenantScopedRepository,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    state: FeedState,
    muted: bool,
    now: datetime,
) -> FindingListResponse:
    """The feed in F1 AC1.5's order, at most `FEED_PAGE` findings.

    Only open findings carry a trend here, read in one statement: a closed
    finding's trend is on its own page, where its readings end when it closed.
    """
    stored_state = _STORED_STATE[state]
    rows = await queries.feed(
        session,
        org_id=org_id,
        project_id=project_id,
        state=stored_state,
        muted=muted,
        limit=queries.FEED_PAGE + 1,
    )
    truncated = len(rows) > queries.FEED_PAGE
    rows = rows[: queries.FEED_PAGE]
    trends = await _open_trends(repository, project_id=project_id, rows=rows, now=now)
    quiet = await queries.quiet_services(session, org_id=org_id, project_id=project_id, now=now)
    open_joined = await queries.open_joined_of(
        session, org_id=org_id, root_ids=[row.id for row in rows if row.case_state is not None]
    )
    return FindingListResponse(
        findings=[
            to_finding(
                row,
                trend=trends.get(row.id, []),
                quiet=quiet.get(row.service_name or ""),
                open_joined=open_joined.get(row.id),
            )
            for row in rows
        ],
        muted_count=await queries.muted_count(
            session, org_id=org_id, project_id=project_id, state=stored_state
        ),
        truncated=truncated,
    )


async def get_finding(
    session: AsyncSession,
    repository: TenantScopedRepository,
    *,
    org_id: uuid.UUID,
    finding_id: uuid.UUID,
    project_scope: uuid.UUID | None,
    for_member: bool,
    now: datetime,
    email_available: bool = False,
) -> FindingDetail | FindingForMember:
    """One finding and its page. `project_scope` is a token's (BR11): a finding in
    another of the organization's projects is the same 404 as one that does not exist.

    `for_member` adds who acknowledged it, the false alarm's reason and what a new
    normal would be, which an API token never sees (design 2.1, item 8).
    """
    row = await queries.find_finding(session, org_id=org_id, finding_id=finding_id)
    if row is None or (project_scope is not None and row.project_id != project_scope):
        raise NotFoundError(NOT_FOUND)
    if for_member:
        return await _member_view(
            session, repository, row, now=now, email_available=email_available
        )
    detail, _, _ = await _detail(session, repository, row, now=now)
    return detail


async def acknowledge(
    session: AsyncSession,
    repository: TenantScopedRepository,
    *,
    actor: User,
    finding_id: uuid.UUID,
    now: datetime,
) -> FindingChanged:
    """FR19, AC33: who is handling it, and since when. It stays open.

    Under F22 (FR3, D20, AC60) it also starts or takes the case: the console
    shows one button, **Take this**, and this route stays for the API. The
    transaction commits before the view is built, so no ClickHouse read runs
    under the row's lock (AC102).
    """
    row, root = await cases.lock_with_root(session, org_id=actor.org_id, finding_id=finding_id)
    if row.state != "open" or row.acknowledged_at is not None:
        raise ConflictError(ALREADY_HANDLED)
    row.acknowledged_by, row.acknowledged_at = actor.id, now
    audit.record_finding_acknowledged(
        session, org_id=row.org_id, finding_id=row.id, actor_user_id=actor.id
    )
    await session.flush()
    await cases.on_acknowledged(session, row, root, actor, now=now)
    await session.commit()
    return FindingChanged(
        finding=await _member_view(session, repository, row, now=now), resolved_to=[]
    )


async def mark_false_alarm(
    session: AsyncSession,
    repository: TenantScopedRepository,
    *,
    actor: User,
    finding_id: uuid.UUID,
    reason: str | None,
    now: datetime,
) -> FindingChanged:
    """FR20, AC32: it was never a problem. An open finding closes, with one *resolved*
    to each channel told of it; one that recovered is reclassified, and sends nothing,
    since its close was already sent. It is counted either way (FR26).

    Only someone who may configure detection returns its minutes to the normal (D28);
    the next project job holds its signals until they read normal again.
    """
    row, root = await cases.lock_with_root(session, org_id=actor.org_id, finding_id=finding_id)
    resolved_to: list[str] = []
    if row.state == "open":
        _close(row, reason="false_alarm", at=now)
        await session.flush()
        resolved_to = await queries.resolve(
            session, row, quiet=await _quiet(session, row, now=now), at=now
        )
    elif row.closed_reason == "recovered" and row.false_alarm_at is None:
        row.closed_reason = "false_alarm"
    else:
        raise ConflictError(NOT_A_FALSE_ALARM)
    returns_minutes = may(actor.role, Action.CONFIGURE_DETECTION)
    row.false_alarm_by, row.false_alarm_at = actor.id, now
    row.false_alarm_returns_minutes = returns_minutes
    row.feedback_reason = reason
    audit.record_finding_false_alarm(
        session,
        org_id=row.org_id,
        finding_id=row.id,
        actor_user_id=actor.id,
        reason=reason,
        returns_minutes=returns_minutes,
    )
    if returns_minutes:
        await queries.queue_baselines(session, org_id=row.org_id, project_id=row.project_id)
    await session.flush()
    # F22 (BR6, AC8): on a root, "it was never a problem" resolves the case with
    # that reason; on a joined finding the case stands. Committed before the
    # view, so no ClickHouse read runs under the lock (AC102).
    await cases.on_never_a_problem(session, row, root, actor, kind="false_alarm", now=now)
    await session.commit()
    return FindingChanged(
        finding=await _member_view(session, repository, row, now=now), resolved_to=resolved_to
    )


async def accept_new_normal(
    session: AsyncSession,
    repository: TenantScopedRepository,
    *,
    actor: User,
    finding_id: uuid.UUID,
    now: datetime,
) -> FindingChanged:
    """FR21, AC15: the finding's level becomes its service's normal, from the minute
    it started, keeping the normal's daily and weekly shape. Refused until the level
    is known from enough readings. The finding closes with one *resolved*, and the
    service's normal is rebuilt at once, so judging continues with no learning gap."""
    row, root = await cases.lock_with_root(session, org_id=actor.org_id, finding_id=finding_id)
    if row.service_name is None:
        raise InvalidRequestError(NO_NORMAL_OF_ITS_OWN)
    if row.state != "open":
        raise ConflictError(NO_LONGER_OPEN)
    readings = await queries.service_readings(
        repository,
        project_id=row.project_id,
        service_name=row.service_name,
        since=_minute_of(row.started_at),
        until=now,
    )
    preview = new_normal_preview(parse_signals(row.signals), readings)
    if not preview.available:
        raise ConflictError(
            TOO_FEW_READINGS.format(readings=preview.readings, needed=preview.needed)
        )
    for shift in preview.signals:
        queries.add_shift(
            session,
            row,
            signal=shift.signal,
            ratio=shift.ratio,
            from_minute=_minute_of(row.started_at),
        )
    _close(row, reason="new_normal", at=now)
    await session.flush()
    resolved_to = await queries.resolve(
        session, row, quiet=await _quiet(session, row, now=now), at=now
    )
    audit.record_finding_new_normal(
        session,
        org_id=row.org_id,
        finding_id=row.id,
        actor_user_id=actor.id,
        shifts=[{"signal": shift.signal, "ratio": shift.ratio} for shift in preview.signals],
    )
    await queries.queue_baselines(session, org_id=row.org_id, project_id=row.project_id)
    await session.flush()
    await cases.on_never_a_problem(session, row, root, actor, kind="new_normal", now=now)
    await session.commit()
    return FindingChanged(
        finding=await _member_view(session, repository, row, now=now), resolved_to=resolved_to
    )


async def _lock(session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID) -> FindingRow:
    row = await queries.lock_finding(session, org_id=org_id, finding_id=finding_id)
    if row is None:
        raise NotFoundError(NOT_FOUND)
    return row


def _close(row: FindingRow, *, reason: str, at: datetime) -> None:
    row.state, row.closed_reason = "closed", reason
    row.closed_at = max(at, row.started_at)


async def _quiet(session: AsyncSession, row: FindingRow, *, now: datetime) -> bool:
    """Muted, flapping, or its service muted or snoozed: people hear nothing more."""
    if row.muted or row.flapping:
        return True
    if row.service_name is None:
        return False
    return await queries.service_quiet(
        session,
        org_id=row.org_id,
        project_id=row.project_id,
        service_name=row.service_name,
        now=now,
    )


async def _detail(
    session: AsyncSession, repository: TenantScopedRepository, row: FindingRow, *, now: datetime
) -> tuple[FindingDetail, list[dict[str, Any]], FindingRow | None]:
    """The page, the readings it was drawn from (newest first), and the open
    joined finding when the row's case has one (F22)."""
    open_joined = (
        await queries.open_joined_finding(session, org_id=row.org_id, root_id=row.id)
        if row.case_state is not None
        else None
    )
    signals = parse_signals(row.signals)
    quiet = (
        await queries.quiet_services(session, org_id=row.org_id, project_id=row.project_id, now=now)
    ).get(row.service_name or "")
    readings: list[dict[str, Any]] = []
    if row.service_name is not None and signals:
        until = row.closed_at or now
        readings = await queries.service_readings(
            repository,
            project_id=row.project_id,
            service_name=row.service_name,
            since=row.started_at - PAGE_LEAD,
            until=until,
        )
    oldest_first = list(reversed(readings))
    trend = (
        trend_values(signals[0].signal, oldest_first[-queries.TREND_POINTS :]) if signals else []
    )
    incidents = await queries.linked_incidents(session, org_id=row.org_id, finding_id=row.id)
    detail = FindingDetail(
        **to_finding(
            row, trend=trend, signals=signals, quiet=quiet, open_joined=open_joined
        ).model_dump(),
        series=[
            SignalSeries(
                signal=signal,
                unit=SIGNAL_UNITS[signal],
                points=[signal_point(signal, reading) for reading in oldest_first],
            )
            for signal in _distinct_signals(signals)
        ]
        if oldest_first
        else [],
        incidents=[
            LinkedIncident(
                id=incident.id,
                rule_name=incident.rule_name,
                condition_kind=incident.condition_kind,
                opened_at=incident.opened_at,
                resolved_at=incident.resolved_at,
            )
            for incident in incidents
        ],
        services=list(row.services or []),
    )
    return detail, readings, open_joined


async def _member_view(
    session: AsyncSession,
    repository: TenantScopedRepository,
    row: FindingRow,
    *,
    now: datetime,
    email_available: bool = False,
) -> FindingForMember:
    detail, readings, open_joined = await _detail(session, repository, row, now=now)
    started = _minute_of(row.started_at)
    since_start = [reading for reading in readings if _reading_minute(reading) >= started]
    preview = (
        new_normal_preview(parse_signals(row.signals), since_start)
        if row.state == "open" and row.service_name is not None
        else None
    )
    named = await queries.person_names(
        session,
        org_id=row.org_id,
        user_ids=[
            user_id
            for user_id in (row.acknowledged_by, row.assignee_id, row.case_resolved_by)
            if user_id is not None
        ],
    )
    taken_at = (
        await queries.latest_assignment_at(
            session, org_id=row.org_id, root_id=row.id, assignee_id=row.assignee_id
        )
        if row.assignee_id is not None
        else None
    )
    return FindingForMember(
        **detail.model_dump(),
        acknowledged_by=row.acknowledged_by,
        acknowledged_by_name=named.get(row.acknowledged_by) if row.acknowledged_by else None,
        feedback_reason=row.feedback_reason,
        new_normal=preview,
        assignee_id=row.assignee_id,
        assignee_name=named.get(row.assignee_id) if row.assignee_id else None,
        case_taken_at=taken_at,
        assignee_acted_since_join=None
        if open_joined is None or row.assignee_id is None
        else bool(
            row.assignee_acted_at is not None and row.assignee_acted_at >= open_joined.started_at
        ),
        case_resolved_by_name=named.get(row.case_resolved_by) if row.case_resolved_by else None,
        project_has_pagerduty=await queries.project_has_pagerduty(
            session, org_id=row.org_id, project_id=row.project_id
        ),
        email_available=email_available,
    )


async def member_view(
    session: AsyncSession,
    repository: TenantScopedRepository,
    *,
    org_id: uuid.UUID,
    finding_id: uuid.UUID,
    now: datetime,
    email_available: bool,
) -> FindingForMember:
    """The page after a case action, built after the action committed (AC102)."""
    row = await queries.find_finding(session, org_id=org_id, finding_id=finding_id)
    if row is None:
        raise NotFoundError(NOT_FOUND)
    return await _member_view(session, repository, row, now=now, email_available=email_available)


# What a support view reads where a note's words would be (FR20, D62).
HIDDEN_DURING_SUPPORT_VIEW: Final = "Hidden during support view"
BAD_CURSOR: Final = "That is not a timeline position. Read the newest page and follow next_before."
# The cursor's two halves: when and which, so paging never skips same-instant rows.
_CURSOR_SEPARATOR: Final = "~"


def _parse_before(before: str | None) -> tuple[datetime, uuid.UUID] | None:
    if before is None:
        return None
    try:
        at_part, id_part = before.split(_CURSOR_SEPARATOR, 1)
        # A Unix timestamp, not ISO text: an ISO offset's `+` dies in a query string.
        return datetime.fromtimestamp(float(at_part), UTC), uuid.UUID(id_part)
    except ValueError as wrong:
        raise InvalidRequestError(BAD_CURSOR) from wrong


async def read_timeline(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    finding_id: uuid.UUID,
    project_scope: uuid.UUID | None,
    real_person: bool,
    token: bool,
    before: str | None,
) -> TimelineResponse:
    """FR16, FR17: one case's timeline, merged from the root and its joined
    findings, newest first, 100 rows a page.

    A person reads all of it; a token reads kinds and times alone (FR20, AC22);
    a support view reads a person's shape with note bodies hidden (D62).
    """
    row = await queries.find_finding(session, org_id=org_id, finding_id=finding_id)
    if row is None or (project_scope is not None and row.project_id != project_scope):
        raise NotFoundError(NOT_FOUND)
    root_id = row.root_finding_id or row.id
    found = await queries.timeline(
        session, org_id=org_id, root_id=root_id, before=_parse_before(before)
    )
    next_before = (
        f"{found[-1][0].at.timestamp()}{_CURSOR_SEPARATOR}{found[-1][0].id}"
        if len(found) == queries.TIMELINE_PAGE
        else None
    )
    if token:
        return TimelineResponse(
            events=[TimelineEvent(id=event.id, kind=event.kind, at=event.at) for event, _ in found],
            next_before=next_before,
        )
    named = await queries.person_names(
        session,
        org_id=org_id,
        user_ids=list({event.actor_id for event, _ in found if event.actor_id is not None}),
    )
    return TimelineResponse(
        events=[
            TimelineEventForMember(
                id=event.id,
                kind=event.kind,
                at=event.at,
                actor_kind=event.actor_kind,
                actor_id=event.actor_id,
                actor_name=named.get(event.actor_id) if event.actor_id else None,
                detail=event.detail,
                note_body=None
                if body is None
                else (body if real_person else HIDDEN_DURING_SUPPORT_VIEW),
            )
            for event, body in found
        ],
        next_before=next_before,
    )


async def finding_stats(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    days: int,
    now: datetime,
) -> FindingStats:
    """FR26 over the last `days` days, ending now."""
    opened, false_alarms = await queries.opened_and_false_alarms(
        session, org_id=org_id, project_id=project_id, since=now - timedelta(days=days)
    )
    services = await queries.judged_service_count(session, org_id=org_id, project_id=project_id)
    per_service = await queries.opened_and_false_alarms_per_service(
        session, org_id=org_id, project_id=project_id, since=now - timedelta(days=days)
    )
    return FindingStats(
        days=days,
        opened=opened,
        false_alarms=false_alarms,
        judged_services=services,
        false_finding_rate_per_service_week=false_finding_rate(
            false_alarms=false_alarms, services=services, days=days
        ),
        services=[
            ServiceFindingStats(
                service_name=name,
                opened=count,
                false_alarms=marked,
                false_alarms_per_week=marked / (days / DAYS_PER_WEEK),
            )
            for name, count, marked in per_service
        ],
    )


# -------------------------------------------------------------- pure mapping


def view_state(stored_state: str, acknowledged_at: datetime | None) -> FindingState:
    """Acknowledged is an open finding somebody has acknowledged (FR19)."""
    if stored_state == "closed":
        return "closed"
    return "acknowledged" if acknowledged_at is not None else "open"


def parse_signals(raw: Any) -> list[FindingSignal]:
    """`findings.signals`, leading signal first, each with its unit.

    The column's default, `{}`, is a finding whose signals are not written yet.
    Anything else that is not the stored list is a defect in the writer, and
    raises rather than showing a finding with its evidence silently missing.
    """
    if raw == {}:
        return []
    return [
        FindingSignal(**stored.model_dump(), unit=SIGNAL_UNITS[stored.signal])
        for stored in _STORED_SIGNALS.validate_python(raw)
    ]


def trend_values(signal: SignalName, readings: Sequence[dict[str, Any]]) -> list[float]:
    """The leading signal's value at each reading, oldest first, in its own unit.

    A reading with no value for the signal — no p95, no requests — is left out
    rather than drawn as zero.
    """
    values: list[float] = []
    for reading in readings:
        value = _signal_value(signal, reading)
        if value is not None:
            values.append(value)
    return values


def _signal_value(signal: SignalName, reading: dict[str, Any]) -> float | None:
    requests = int(reading["window_requests"])
    if signal == "slower":
        p95 = reading["window_p95_ms"]
        return None if p95 is None else float(p95)
    if signal == "failing":
        return PERCENT * int(reading["window_errors"]) / requests if requests else None
    minutes = int(reading["window_minutes"])
    return requests / minutes if minutes else None


def signal_point(signal: SignalName, reading: dict[str, Any]) -> SignalPoint:
    """One reading of one signal, with its normal and the edge of its band."""
    judged = signal_reading(signal, reading)
    normal = None if judged is None else judged.normal
    return SignalPoint(
        at=datetime.fromtimestamp(int(reading["minute_epoch"]), UTC),
        value=_signal_value(signal, reading),
        normal=normal,
        edge=band_edge(signal, normal, int(reading["window_minutes"])),
    )


def band_edge(signal: SignalName, normal: float | None, window_minutes: int) -> float | None:
    """The band's far edge, where the minimum change lies for the sensitivity that
    judged the reading (`normal_band`): above the normal for *slower* and *failing
    more*, below it for *losing traffic*."""
    band = normal_band(signal, normal, window_minutes)
    if band is None:
        return None
    low, high = band
    return low if signal == "traffic" else high


def new_normal_preview(
    signals: Sequence[FindingSignal], readings: Sequence[dict[str, Any]]
) -> NewNormalPreview:
    """What *This is the new normal* would make each signal's normal, from the readings
    since the finding started, newest first (FR21). Available only when every signal
    has enough of them: half a finding's signals moved would reopen it at once."""
    proposed: list[ProposedShift] = []
    fewest = SHIFT_READINGS
    for signal in _distinct_signals(signals):
        usable = [
            found
            for found in (signal_reading(signal, each) for each in readings[:SHIFT_READINGS])
            if found is not None and found.normal > 0
        ]
        fewest = min(fewest, len(usable))
        ratio = shift_ratio(signal, readings)
        if ratio is None:
            continue
        before = usable[0].normal if usable else None
        proposed.append(
            ProposedShift(
                signal=signal,
                unit=SIGNAL_UNITS[signal],
                ratio=ratio,
                normal_before=before,
                normal_after=None if before is None else before * ratio,
            )
        )
    available = bool(signals) and len(proposed) == len(_distinct_signals(signals))
    return NewNormalPreview(
        available=available,
        readings=fewest,
        needed=SHIFT_READINGS_NEEDED,
        signals=proposed if available else [],
    )


def _distinct_signals(signals: Sequence[FindingSignal]) -> list[SignalName]:
    """Each signal once, leading first: an endpoint's entry is its service's *failing*."""
    seen: list[SignalName] = []
    for entry in signals:
        if entry.signal not in seen:
            seen.append(entry.signal)
    return seen


def _minute_of(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


def _reading_minute(reading: dict[str, Any]) -> datetime:
    return datetime.fromtimestamp(int(reading["minute_epoch"]), UTC)


def false_finding_rate(*, false_alarms: int, services: int, days: int) -> float | None:
    """False findings per service per week; None when no service was judged."""
    service_weeks = services * days / DAYS_PER_WEEK
    return false_alarms / service_weeks if service_weeks else None


def worse_severity(severity: str, other: str | None) -> str:
    """The worse of two severities (FR21's case severity): critical beats warning."""
    return "critical" if "critical" in (severity, other) else severity


def to_finding(
    row: FindingRow,
    *,
    trend: list[float],
    signals: list[FindingSignal] | None = None,
    quiet: queries.ServiceQuiet | None = None,
    open_joined: FindingRow | None = None,
) -> Finding:
    """`open_joined` is the open finding joined to this row's case, when one is:
    it decides the case signal and the case severity (F22, FR23)."""
    return Finding(
        service_snoozed_until=None if quiet is None else quiet.snoozed_until,
        service_muted_until=None if quiet is None else quiet.muted_until,
        id=row.id,
        project_id=row.project_id,
        scope=row.scope,
        service_name=row.service_name,
        title=row.title,
        severity=row.severity,
        state=view_state(row.state, row.acknowledged_at),
        closed_reason=row.closed_reason,
        muted=row.muted,
        started_at=row.started_at,
        closed_at=row.closed_at,
        acknowledged_at=row.acknowledged_at,
        signals=parse_signals(row.signals) if signals is None else signals,
        trend=trend,
        case_state=row.case_state,
        case_resolution=row.case_resolution,
        case_opened_at=row.case_opened_at,
        case_resolved_at=row.case_resolved_at,
        root_finding_id=row.root_finding_id,
        case_signal="open" if row.state == "open" or open_joined is not None else "closed",
        case_severity=worse_severity(
            row.severity, None if open_joined is None else open_joined.severity
        ),
        open_joined_finding_id=None if open_joined is None else open_joined.id,
        open_joined_since=None if open_joined is None else open_joined.started_at,
    )


async def _open_trends(
    repository: TenantScopedRepository,
    *,
    project_id: uuid.UUID,
    rows: Sequence[FindingRow],
    now: datetime,
) -> dict[uuid.UUID, list[float]]:
    """Each open service finding's trend, from one read of its services' readings."""
    leading: dict[uuid.UUID, tuple[str, SignalName]] = {}
    for row in rows:
        if row.state != "open" or row.service_name is None:
            continue
        signals = parse_signals(row.signals)
        if signals:
            leading[row.id] = (row.service_name, signals[0].signal)
    if not leading:
        return {}

    readings = await queries.recent_readings(
        repository,
        project_id=project_id,
        services=sorted({service for service, _ in leading.values()}),
        since=now - TREND_LOOKBACK,
        until=now,
    )
    by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for reading in readings:
        by_service[reading["service_name"]].append(reading)
    return {
        finding_id: trend_values(signal, list(reversed(by_service[service])))
        for finding_id, (service, signal) in leading.items()
    }
