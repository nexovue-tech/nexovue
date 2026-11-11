"""Release facts the scheduler reads from Postgres (F28 design, *Signatures, for F3*;
D56, D29): a service's coverage as of a moment, the release before a moment, the
releases near one, the signed deploys in a window, and the versions only seen in one.
F3's evidence job and FR44's line in a finding's notice call these; neither adds its
own. `deploys_in_window` and `releases_seen_in_window` were built ahead of F28's T5.2,
by its published signatures, for F3's T1.4 (F3 `status.md`, *Open decisions*).

Every statement binds `org_id` and the project, and carries no sender text: a period's
facts are ids, names, a basis and a time (AC45). Names are telemetry text, which the
caller aliases (F3) or passes through `one_line` and the channel's escaping (FR44).
Order only: nothing here says a release caused anything.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform import releases
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

# The most `releases_near` returns, newest first after the service's own.
NEAR_LIMIT: Final = 50

_COVERAGE: Final = text(
    """
    SELECT (SELECT min(first_reported_at) FROM releases
            WHERE org_id = :org_id AND project_id = :project_id
              AND service_name = :service) AS first_reported_at,
           (SELECT min(first_seen_at) FROM releases
            WHERE org_id = :org_id AND project_id = :project_id
              AND service_name = :service) AS first_seen_at,
           (SELECT max(deployed_at) FROM deploy_events
            WHERE org_id = :org_id AND project_id = :project_id
              AND service_name = :service AND deployed_at <= :at) AS last_reported_at
    """
)
# The last period starting at or before the moment, at any age (FR32, FR44). One in
# the moment's own minute came before it (AC41).
_RELEASE_BEFORE: Final = text(
    """
    SELECT periods.id AS period_id, periods.release_id, periods.service_name,
           known.version, periods.start_basis, periods.started_at, periods.already_running
    FROM release_periods AS periods
    JOIN releases AS known ON known.org_id = periods.org_id AND known.id = periods.release_id
    WHERE periods.org_id = :org_id AND periods.project_id = :project_id
      AND periods.service_name = :service AND periods.started_at <= :at
    ORDER BY periods.started_at DESC, periods.id DESC
    LIMIT 1
    """
)
# Every release in the project in the lookback, the service's own first (FR52). One
# already running when counting began is not a release (FR43).
_RELEASES_NEAR: Final = text(
    """
    SELECT periods.id AS period_id, periods.release_id, periods.service_name,
           known.version, periods.start_basis, periods.started_at, periods.already_running
    FROM release_periods AS periods
    JOIN releases AS known ON known.org_id = periods.org_id AND known.id = periods.release_id
    WHERE periods.org_id = :org_id AND periods.project_id = :project_id
      AND periods.started_at >= :since AND periods.started_at <= :at
      AND NOT periods.already_running
    ORDER BY periods.service_name = :service DESC, periods.started_at DESC, periods.id DESC
    LIMIT :limit
    """
)


# Signed deploy events in a window, oldest first (D29): only the states that went out
# (`OPENING_STATES`). `metadata`, `url`, `source` and `delivery_id` are never read (AC45).
_DEPLOYS_IN_WINDOW: Final = text(
    """
    SELECT id, service_name, version, deployed_at, state, release_id
    FROM deploy_events
    WHERE org_id = :org_id AND project_id = :project_id
      AND service_name IN :services
      AND deployed_at >= :start AND deployed_at < :end
      AND state IN :states
    ORDER BY deployed_at, id
    LIMIT :limit
    """
).bindparams(bindparam("services", expanding=True), bindparam("states", expanding=True))
# Periods whose start was only seen in traffic (D54): F3's `telemetry_text`.
_RELEASES_SEEN_IN_WINDOW: Final = text(
    """
    SELECT periods.id AS period_id, periods.release_id, periods.service_name,
           known.version, periods.start_basis, periods.started_at, periods.already_running
    FROM release_periods AS periods
    JOIN releases AS known ON known.org_id = periods.org_id AND known.id = periods.release_id
    WHERE periods.org_id = :org_id AND periods.project_id = :project_id
      AND periods.service_name = :service AND periods.start_basis = :seen
      AND periods.started_at >= :start AND periods.started_at < :end
      AND NOT periods.already_running
    ORDER BY periods.started_at, periods.id
    LIMIT :limit
    """
)
# The most rows one window read returns: a week of a frequent deployer fits.
WINDOW_LIMIT: Final = 2000


@dataclass(frozen=True, slots=True)
class DeployFact:
    """One signed deploy event, as F3 may read it (D29). No sender text (AC45)."""

    deploy_id: uuid.UUID
    service_name: str
    version: str
    # The sender's claim, bounded at the door (F28 FR8).
    deployed_at: datetime
    state: str
    release_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class PeriodFact:
    """One release period, as F3 and FR44 may read it. No sender text (AC45)."""

    release_id: uuid.UUID
    period_id: uuid.UUID
    service_name: str
    version: str
    # How the period's start was learned: `reported` (signed) or `seen` (rollup, F3's
    # `telemetry_text`, D54).
    basis: str
    started_at: datetime
    # The version was running when counting began: not a release (FR43).
    already_running: bool


async def coverage(
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service: str,
    at: datetime,
) -> releases.Coverage:
    """A service's coverage as of `at`, by the rule the API uses (AC73)."""
    row = (
        await connection.execute(
            _COVERAGE,
            {"org_id": org_id, "project_id": project_id, "service": service, "at": at},
        )
    ).one()
    return releases.coverage_of(
        row.first_reported_at,
        row.first_seen_at,
        at=at,
        last_reported_at=row.last_reported_at,
    )


async def release_before(
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service: str,
    at: datetime,
) -> PeriodFact | None:
    row = (
        await connection.execute(
            _RELEASE_BEFORE,
            {"org_id": org_id, "project_id": project_id, "service": service, "at": at},
        )
    ).first()
    return None if row is None else _fact(row)


async def releases_near(
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service: str,
    at: datetime,
    lookback: timedelta,
) -> list[PeriodFact]:
    rows = await connection.execute(
        _RELEASES_NEAR,
        {
            "org_id": org_id,
            "project_id": project_id,
            "service": service,
            "since": at - lookback,
            "at": at,
            "limit": NEAR_LIMIT,
        },
    )
    return [_fact(row) for row in rows]


async def deploys_in_window(
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    services: list[str],
    start: datetime,
    end: datetime,
) -> list[DeployFact]:
    """Signed deploy events of these services in [start, end), oldest first."""
    if not services:
        return []
    rows = await connection.execute(
        _DEPLOYS_IN_WINDOW,
        {
            "org_id": org_id,
            "project_id": project_id,
            "services": services,
            "start": start,
            "end": end,
            "states": sorted(releases.OPENING_STATES),
            "limit": WINDOW_LIMIT,
        },
    )
    return [
        DeployFact(
            deploy_id=row.id,
            service_name=row.service_name,
            version=row.version,
            deployed_at=row.deployed_at,
            state=row.state,
            release_id=row.release_id,
        )
        for row in rows
    ]


async def releases_seen_in_window(
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service: str,
    start: datetime,
    end: datetime,
) -> list[PeriodFact]:
    """Periods of `service` that started in [start, end), seen only in traffic (D54)."""
    rows = await connection.execute(
        _RELEASES_SEEN_IN_WINDOW,
        {
            "org_id": org_id,
            "project_id": project_id,
            "service": service,
            "seen": releases.SEEN,
            "start": start,
            "end": end,
            "limit": WINDOW_LIMIT,
        },
    )
    return [_fact(row) for row in rows]


def _fact(row: Any) -> PeriodFact:
    return PeriodFact(
        release_id=row.release_id,
        period_id=row.period_id,
        service_name=row.service_name,
        version=row.version,
        basis=row.start_basis,
        started_at=row.started_at,
        already_running=row.already_running,
    )
