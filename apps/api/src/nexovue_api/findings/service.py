"""Findings (F6, ADR-022): the feed, one finding, and the false-finding count (FR26).

Findings are written only by the scheduler's project job; here they are read.
A finding's own row carries everything it says. Its trend comes from the frozen
readings, which expire after 35 days (AC40), so an old finding has no trend.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Final

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.findings import queries
from nexovue_api.findings.schemas import (
    FeedState,
    Finding,
    FindingForMember,
    FindingListResponse,
    FindingSignal,
    FindingState,
    FindingStats,
    SignalName,
    SignalUnit,
    StoredSignal,
)

NOT_FOUND: Final = "No such finding."

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
    return FindingListResponse(
        findings=[to_finding(row, trend=trends.get(row.id, [])) for row in rows],
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
) -> Finding | FindingForMember:
    """One finding. `project_scope` is a token's (BR11): a finding in another of the
    organization's projects is the same 404 as one that does not exist.

    `for_member` adds who acknowledged it and the false alarm's reason, which an
    API token never sees (design 2.1, item 8).
    """
    row = await queries.find_finding(session, org_id=org_id, finding_id=finding_id)
    if row is None or (project_scope is not None and row.project_id != project_scope):
        raise NotFoundError(NOT_FOUND)

    signals = parse_signals(row.signals)
    trend: list[float] = []
    if row.service_name is not None and signals:
        until = row.closed_at or now
        readings = await queries.recent_readings(
            repository,
            project_id=row.project_id,
            services=[row.service_name],
            since=until - TREND_LOOKBACK,
            until=until,
        )
        trend = trend_values(signals[0].signal, list(reversed(readings)))

    finding = to_finding(row, trend=trend, signals=signals)
    if not for_member:
        return finding
    return FindingForMember(
        **finding.model_dump(),
        acknowledged_by=row.acknowledged_by,
        feedback_reason=row.feedback_reason,
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
    return FindingStats(
        days=days,
        opened=opened,
        false_alarms=false_alarms,
        judged_services=services,
        false_finding_rate_per_service_week=false_finding_rate(
            false_alarms=false_alarms, services=services, days=days
        ),
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


def false_finding_rate(*, false_alarms: int, services: int, days: int) -> float | None:
    """False findings per service per week; None when no service was judged."""
    service_weeks = services * days / DAYS_PER_WEEK
    return false_alarms / service_weeks if service_weeks else None


def to_finding(
    row: FindingRow, *, trend: list[float], signals: list[FindingSignal] | None = None
) -> Finding:
    return Finding(
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
