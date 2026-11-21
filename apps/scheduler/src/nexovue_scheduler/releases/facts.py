"""Release facts the scheduler reads (F28 design, *Signatures, for F3*; D56, D29; plan
T5.2): a service's coverage as of a moment, the release before a moment, the releases
near one, the signed deploys in a window, the versions only seen in one, and a period's
comparison. F3's evidence job, FR44's line in a finding's notice and the stored reading
(`summary`) call these; none adds its own.

Every Postgres statement binds `org_id` and the project; the comparison's ClickHouse
reads go through the release store, bound to the same project. Nothing carries sender
text: a fact is ids, names, a basis, times, states, counts and words of our own, an
exact list `test_release_facts.py` holds (AC45). Names are telemetry text, which the
caller aliases (F3) or passes through `one_line` and the channel's escaping (FR44).
Order only: nothing here says a release caused anything.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform import releases
from nexovue_platform.releases import DeployFact
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from nexovue_scheduler.releases.store import ReleaseStore

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
    SELECT id, release_id, service_name, version, deployed_at, created_at, state, commit_sha
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
# One period to compare, with what the comparison needs beside it: when its previous
# version last started (its warm-up), the settled minute, and how long the organization's
# plan keeps derived numbers (`project_retention`, the plan's value the API reads).
_PERIOD_TO_COMPARE: Final = text(
    """
    SELECT periods.id AS period_id, periods.release_id, periods.service_name,
           known.version, periods.started_at, periods.start_basis,
           periods.previous_version, periods.already_running,
           (SELECT earlier.started_at
            FROM release_periods AS earlier
            JOIN releases AS other
              ON other.org_id = earlier.org_id AND other.id = earlier.release_id
            WHERE earlier.org_id = :org_id AND earlier.project_id = :project_id
              AND other.service_name = periods.service_name
              AND other.version = periods.previous_version
              AND earlier.started_at <= periods.started_at
            ORDER BY earlier.started_at DESC
            LIMIT 1) AS previous_started_at,
           (SELECT settled_minute FROM detection_sweep) AS settled_minute,
           (SELECT retention.derived_retention_days FROM project_retention AS retention
            WHERE retention.org_id = :org_id
              AND retention.project_id = :project_id) AS kept_days
    FROM release_periods AS periods
    JOIN releases AS known ON known.org_id = periods.org_id AND known.id = periods.release_id
    WHERE periods.org_id = :org_id AND periods.project_id = :project_id
      AND periods.id = :period_id
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
            release_id=row.release_id,
            service_name=row.service_name,
            version=row.version,
            deployed_at=row.deployed_at,
            received_at=row.created_at,
            state=row.state,
            commit_sha=row.commit_sha,
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


@dataclass(frozen=True, slots=True)
class ComparisonFact:
    """One period's comparison, as F3 and the stored reading take it (AC45, D54: F3's
    `telemetry`). Methods, reasons and words of our own, counts, times, and the two
    version names (`version`, each block's sides); never the other versions a service
    sends, and never sender text."""

    release_id: uuid.UUID
    period_id: uuid.UUID
    service_name: str
    version: str
    previous_version: str | None
    # FR21's reason when nothing is compared; else None.
    reason: str | None
    # The block that leads (`releases.ROLLOUT`, `AFTER_ROLLOUT`, `AROUND_DEPLOY`).
    lead: str | None
    blocks: tuple[releases.Block, ...]
    # Too little traffic: the counts of the largest block tried (AC31).
    traffic: tuple[releases.Side, releases.Side] | None
    # Where the reads stopped, and whether that is "as of", not the settled minute.
    compared_to: datetime
    as_of: bool
    # FR28: compared minutes whose version rows differ from the service's totals.
    minutes_differing: int
    # FR17: the worker combined versions in a compared minute.
    versions_combined: bool
    # FR45: the service's stored spans carry more than one environment label.
    mixed_environments: bool


async def release_comparison(
    store: ReleaseStore,
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    period_id: uuid.UUID,
    window: timedelta,
    now: datetime,
) -> ComparisonFact | None:
    """A period against its previous version, by the API's own rule (design 4): the
    shared `choose_method` over the same rows, read up to the settled minute. None when
    the period is not this project's. Reads ClickHouse as well as Postgres, so F3's
    table must list a ClickHouse read."""
    row = (
        await connection.execute(
            _PERIOD_TO_COMPARE,
            {"org_id": org_id, "project_id": project_id, "period_id": period_id},
        )
    ).first()
    if row is None:
        return None
    to, as_of = releases.comparison_end(row.started_at + window, row.settled_minute, now)
    kept_days = min(row.kept_days or releases.VERSION_ROLLUP_DAYS, releases.VERSION_ROLLUP_DAYS)
    start = releases.read_start(row.started_at, window)
    reader = store.for_project(org_id, project_id)
    rows: list[releases.VersionMinute] = []
    totals: list[releases.ServiceMinute] = []
    if start < to:
        rows = reader.compared_minutes(service_name=row.service_name, start=start, end=to)
        totals = reader.compared_totals(service_name=row.service_name, start=start, end=to)
    decided = releases.choose_method(
        releases.PeriodToCompare(
            started_at=row.started_at,
            version=row.version,
            previous_version=row.previous_version,
            already_running=row.already_running,
            previous_started_at=row.previous_started_at,
            reported_at=releases.reported_moment(row.started_at, row.start_basis),
        ),
        rows,
        to=to,
        window=window,
        expired_before=now - timedelta(days=kept_days),
        totals=totals,
    )
    spans = releases.compared_spans(decided)
    mixed = bool(spans) and (
        len(
            reader.environments(
                service_name=row.service_name,
                start=min(start for start, _ in spans),
                end=max(end for _, end in spans),
            )
        )
        >= releases.ENVIRONMENTS_ENOUGH
    )
    return ComparisonFact(
        release_id=row.release_id,
        period_id=row.period_id,
        service_name=row.service_name,
        version=row.version,
        previous_version=row.previous_version,
        reason=decided.reason,
        lead=decided.lead,
        blocks=decided.blocks,
        traffic=decided.traffic,
        compared_to=to,
        as_of=as_of,
        minutes_differing=releases.minutes_differing(rows, totals, spans),
        versions_combined=releases.versions_combined(rows, spans),
        mixed_environments=mixed,
    )


# A release summary's period, its release and its stored reading, for delivery to put
# into words after the claim (D52, AC42). The organization's, by the notice's own.
_SUMMARIZED_PERIODS: Final = text(
    """
    SELECT periods.id AS period_id, periods.release_id, known.service_name, known.version,
           periods.previous_version, periods.started_at, periods.last_reading
    FROM release_periods AS periods
    JOIN releases AS known ON known.org_id = periods.org_id AND known.id = periods.release_id
    WHERE periods.org_id = :org_id AND periods.id IN :period_ids
    """
).bindparams(bindparam("period_ids", expanding=True))


@dataclass(frozen=True, slots=True)
class SummarizedPeriod:
    """What a release summary says (FR55): the names, read at send, and the reading the
    job stored, which holds none."""

    period_id: uuid.UUID
    release_id: uuid.UUID
    service_name: str
    version: str
    previous_version: str | None
    started_at: datetime
    last_reading: dict[str, Any] | None


async def summarized_periods(
    connection: AsyncConnection, *, org_id: uuid.UUID, period_ids: list[uuid.UUID]
) -> dict[uuid.UUID, SummarizedPeriod]:
    """Each named period of this organization's, by id; a period gone is absent."""
    if not period_ids:
        return {}
    rows = await connection.execute(
        _SUMMARIZED_PERIODS, {"org_id": org_id, "period_ids": period_ids}
    )
    return {
        row.period_id: SummarizedPeriod(
            period_id=row.period_id,
            release_id=row.release_id,
            service_name=row.service_name,
            version=row.version,
            previous_version=row.previous_version,
            started_at=row.started_at,
            last_reading=row.last_reading,
        )
        for row in rows
    }


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
