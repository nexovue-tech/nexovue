"""Release facts the scheduler reads from Postgres (F28 design, *Signatures, for F3*;
D56): a service's coverage as of a moment, the release before a moment, and the
releases near one. F3's evidence job and FR44's line in a finding's notice call these;
neither adds its own.

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
from sqlalchemy import text
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
