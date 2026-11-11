"""The evidence job's reads of Postgres (F3 design, *Reads*; plan T1.4).

Every statement binds `org_id` (and the project where the table has one), which
`test_scheduler_postgres_tenancy_structure.py` holds. Deploys, coverage, the release
before and the 2-hour list are F28's readers (`releases/facts.py`), called here and
never copied (A71, D56). A check is read by name and state only, never its `reason`,
which holds a run's own words (`models.py`, `CheckStateChange.reason`). The intake
fact is install-wide and read on a self-hosted install only (FR11).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform.evidence import DEPLOY_LOOKBACK
from nexovue_platform.releases import DEPLOYS_AND_VERSIONS, DEPLOYS_ONLY, VERSIONS_ONLY
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from nexovue_scheduler.evidence.rules import (
    BASE_RATE_DAYS,
    CheckChange,
    DeployFact,
    DeployHistory,
    LinkedIncident,
    OtherFinding,
    Release,
)
from nexovue_scheduler.releases import facts as release_facts

# F28's list of releases before a finding (its FR52, a guess).
RELEASES_NEAR_LOOKBACK: Final = timedelta(hours=2)
# A check that changed this long before the start is in the window (FR8, rule 9).
CHECK_LOOKBACK: Final = timedelta(minutes=30)
# Intake refusals this long before the start show (FR11).
INTAKE_LOOKBACK: Final = timedelta(minutes=15)
# Coverage values that hold signed deploys, and seen versions (F28's four values).
_SIGNED_COVERAGE: Final = (DEPLOYS_AND_VERSIONS, DEPLOYS_ONLY)
_SEEN_COVERAGE: Final = (DEPLOYS_AND_VERSIONS, VERSIONS_ONLY)
# The most of each list one read keeps.
MOST_CHECK_CHANGES: Final = 20
MOST_OTHER_FINDINGS: Final = 50
MOST_INCIDENTS: Final = 20

_FINDING: Final = text(
    """
    SELECT findings.id, findings.org_id, findings.project_id, findings.scope,
           findings.service_name, findings.severity, findings.state, findings.muted,
           findings.flapping,
           findings.signals, findings.services, findings.started_at, findings.closed_at,
           projects.timezone, projects.deleted_at AS project_deleted_at
    FROM findings
    JOIN projects ON projects.org_id = findings.org_id AND projects.id = findings.project_id
    WHERE findings.org_id = :org_id AND findings.id = :finding_id
    """
)
_LATEST_VERSION: Final = text(
    """
    SELECT version, fingerprint, aliases, wording_state, wording_reason, wording,
           wording_from_version
    FROM finding_explanations
    WHERE org_id = :org_id AND finding_id = :finding_id
    ORDER BY version DESC
    LIMIT 1
    """
)
_OPEN_FINDINGS_OF: Final = text(
    """
    SELECT id, service_name, started_at
    FROM findings
    WHERE org_id = :org_id AND project_id = :project_id AND state = 'open'
      AND scope = 'service' AND id <> :finding_id AND service_name IN :services
    ORDER BY started_at
    LIMIT :limit
    """
).bindparams(bindparam("services", expanding=True))
_INCIDENTS: Final = text(
    """
    SELECT incidents.id, incidents.rule_name, incidents.opened_at
    FROM finding_incidents
    JOIN incidents ON incidents.org_id = finding_incidents.org_id
      AND incidents.id = finding_incidents.incident_id
    WHERE finding_incidents.org_id = :org_id AND finding_incidents.finding_id = :finding_id
    ORDER BY incidents.opened_at
    LIMIT :limit
    """
)
# Name and state only: `reason` is a run's own words and is never read (design, *Reads*).
_CHECK_CHANGES: Final = text(
    """
    SELECT changes.check_id, checks.name, changes.to_state, changes.changed_at
    FROM check_state_changes AS changes
    JOIN checks ON checks.org_id = changes.org_id AND checks.id = changes.check_id
    WHERE changes.org_id = :org_id AND changes.project_id = :project_id
      AND changes.changed_at >= :start AND changes.changed_at < :end
    ORDER BY changes.changed_at DESC
    LIMIT :limit
    """
)
# The rules this organization stopped from naming a cause (FR54, plan T2.12).
_STOPPED_RULES: Final = text("SELECT rule FROM explanation_stopped_rules WHERE org_id = :org_id")
# Install-wide, no tenant: read only on a self-hosted install (FR11), where every
# project on it is the operator's own.
_INTAKE_REFUSALS: Final = text(
    """
    SELECT DISTINCT minute FROM intake_refusals
    WHERE minute >= :start AND minute < :end
    ORDER BY minute
    """
)


@dataclass(frozen=True, slots=True)
class FindingRow:
    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    scope: str
    service_name: str | None
    severity: str
    state: str
    muted: bool
    flapping: bool
    signals: list[dict[str, Any]]
    services: list[str]
    started_at: datetime
    closed_at: datetime | None
    timezone: str
    project_deleted: bool


async def finding(
    connection: AsyncConnection, *, org_id: uuid.UUID, finding_id: uuid.UUID
) -> FindingRow | None:
    row = (await connection.execute(_FINDING, {"org_id": org_id, "finding_id": finding_id})).first()
    if row is None:
        return None
    return FindingRow(
        id=row.id,
        org_id=row.org_id,
        project_id=row.project_id,
        scope=row.scope,
        service_name=row.service_name,
        severity=row.severity,
        state=row.state,
        muted=row.muted,
        flapping=row.flapping,
        signals=list(row.signals or []),
        services=list(row.services or []),
        started_at=row.started_at,
        closed_at=row.closed_at,
        timezone=row.timezone,
        project_deleted=row.project_deleted_at is not None,
    )


async def latest_version(
    connection: AsyncConnection, *, org_id: uuid.UUID, finding_id: uuid.UUID
) -> Any:
    return (
        await connection.execute(_LATEST_VERSION, {"org_id": org_id, "finding_id": finding_id})
    ).first()


async def stopped_rules(connection: AsyncConnection, *, org_id: uuid.UUID) -> frozenset[str]:
    """FR54: a stopped rule names no cause in this organization; its facts stay."""
    rows = await connection.execute(_STOPPED_RULES, {"org_id": org_id})
    return frozenset(row.rule for row in rows)


async def open_findings_of(
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    finding_id: uuid.UUID,
    services: Sequence[str],
) -> list[OtherFinding]:
    """Open service findings on these services: the dependencies' own (rules 2, 6)."""
    if not services:
        return []
    rows = await connection.execute(
        _OPEN_FINDINGS_OF,
        {
            "org_id": org_id,
            "project_id": project_id,
            "finding_id": finding_id,
            "services": list(services),
            "limit": MOST_OTHER_FINDINGS,
        },
    )
    return [OtherFinding(str(row.id), row.service_name, row.started_at) for row in rows]


async def incidents(
    connection: AsyncConnection, *, org_id: uuid.UUID, finding_id: uuid.UUID
) -> list[LinkedIncident]:
    rows = await connection.execute(
        _INCIDENTS, {"org_id": org_id, "finding_id": finding_id, "limit": MOST_INCIDENTS}
    )
    return [LinkedIncident(str(row.id), row.rule_name, row.opened_at) for row in rows]


async def check_changes(
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[CheckChange]:
    """A check's state changes in the window: project-wide facts, never a cause (A17)."""
    rows = await connection.execute(
        _CHECK_CHANGES,
        {
            "org_id": org_id,
            "project_id": project_id,
            "start": start - CHECK_LOOKBACK,
            "end": end,
            "limit": MOST_CHECK_CHANGES,
        },
    )
    return [CheckChange(str(row.check_id), row.name, row.to_state, row.changed_at) for row in rows]


async def intake_refusals(connection: AsyncConnection, *, start: datetime) -> list[datetime]:
    """Install-wide refusals in the 15 minutes before; the caller asks only when self-hosted."""
    rows = await connection.execute(
        _INTAKE_REFUSALS, {"start": start - INTAKE_LOOKBACK, "end": start}
    )
    return [row.minute for row in rows]


@dataclass(frozen=True, slots=True)
class Deploys:
    """Signed deploys in the window, each service's week for its base rate, and the
    versions seen only in traffic (D29, D54)."""

    in_window: tuple[DeployFact, ...]
    histories: dict[str, DeployHistory]
    seen_histories: dict[str, DeployHistory]


async def deploys(
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    services: Sequence[str],
    seen_service: str | None,
    start: datetime,
) -> Deploys:
    """Through F28's readers only: `deploys_in_window`, `releases_seen_in_window` and
    `coverage`. A week of records is known from coverage a week back; a shorter history
    starts at its first record in the week (FR7)."""
    week_from = start - timedelta(days=BASE_RATE_DAYS) - DEPLOY_LOOKBACK
    end = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    signed = await release_facts.deploys_in_window(
        connection,
        org_id=org_id,
        project_id=project_id,
        services=list(services),
        start=week_from,
        end=end,
    )
    window = tuple(
        DeployFact(str(d.deploy_id), d.service_name, d.version, d.deployed_at, True)
        for d in signed
        if start - DEPLOY_LOOKBACK <= d.deployed_at
    )
    histories: dict[str, DeployHistory] = {}
    for service in sorted({d.service for d in window} | set(services[:1])):
        times = [d.deployed_at for d in signed if d.service_name == service]
        week_back = await release_facts.coverage(
            connection, org_id=org_id, project_id=project_id, service=service, at=week_from
        )
        since = week_from if week_back.value in _SIGNED_COVERAGE else min(times, default=None)
        mine = [(d.version, d.deployed_at) for d in signed if d.service_name == service]
        histories[service] = DeployHistory(service, _one_per_rollout(mine), since)
    seen_histories: dict[str, DeployHistory] = {}
    seen: tuple[DeployFact, ...] = ()
    if seen_service is not None:
        periods = await release_facts.releases_seen_in_window(
            connection,
            org_id=org_id,
            project_id=project_id,
            service=seen_service,
            start=week_from,
            end=end,
        )
        week_back = await release_facts.coverage(
            connection, org_id=org_id, project_id=project_id, service=seen_service, at=week_from
        )
        times = [p.started_at for p in periods]
        since = week_from if week_back.value in _SEEN_COVERAGE else min(times, default=None)
        seen_histories[seen_service] = DeployHistory(seen_service, tuple(times), since)
        seen = tuple(
            DeployFact(str(p.period_id), p.service_name, p.version, p.started_at, False)
            for p in periods
            if start - DEPLOY_LOOKBACK <= p.started_at
        )
    return Deploys(in_window=window + seen, histories=histories, seen_histories=seen_histories)


def _one_per_rollout(events: Sequence[tuple[str, datetime]]) -> tuple[datetime, ...]:
    """A rollout told as `started` then `finished` is one deploy for the base rate: a
    version's events inside one lookback count once (F28's deploy states)."""
    kept: list[datetime] = []
    last: dict[str, datetime] = {}
    for version, at in sorted(events, key=lambda event: event[1]):
        previous = last.get(version)
        if previous is None or at - previous >= DEPLOY_LOOKBACK:
            kept.append(at)
        last[version] = at
    return tuple(kept)


@dataclass(frozen=True, slots=True)
class Releases:
    coverage: str
    before: Release | None
    near: tuple[Release, ...]


async def releases(
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service: str,
    start: datetime,
    callees: Sequence[str],
) -> Releases:
    """FR49 and F28's FR32 and FR52: the last release at any age, and the project's
    2-hour list with the service's own first, marking those this service calls."""
    coverage = await release_facts.coverage(
        connection, org_id=org_id, project_id=project_id, service=service, at=start
    )
    before = await release_facts.release_before(
        connection, org_id=org_id, project_id=project_id, service=service, at=start
    )
    near = await release_facts.releases_near(
        connection,
        org_id=org_id,
        project_id=project_id,
        service=service,
        at=start,
        lookback=RELEASES_NEAR_LOOKBACK,
    )
    called = set(callees)
    return Releases(
        coverage=coverage.value,
        before=None if before is None or before.already_running else _release(before, called),
        near=tuple(_release(period, called) for period in near),
    )


def _release(period: release_facts.PeriodFact, called: set[str]) -> Release:
    return Release(
        release_id=str(period.release_id),
        period_id=str(period.period_id),
        service=period.service_name,
        version=period.version,
        started_at=period.started_at,
        signed=period.basis == "reported",
        called=period.service_name in called,
    )
