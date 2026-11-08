"""FR7 and FR8 on what ClickHouse holds: the suggestion and its arithmetic (D10).

The arithmetic is the platform's (`nexovue_platform.slo`); this reads the four weeks it
needs and shapes the answer. It has never been tried on real traffic (design,
*`/slo-readings/suggestion`*): Step 2 prints it for each demo service, for the founder.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform import slo
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.dependencies import Identity
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.projects.service import require_project
from nexovue_api.slos import queries as slo_queries
from nexovue_api.slos.readings import queries
from nexovue_api.slos.readings.schemas import (
    CheckAllowance,
    CheckFacts,
    CheckWindow,
    EndpointChoice,
    SloSuggestion,
    SuggestedWeek,
)
from nexovue_api.window import RECEIVE_CLOCK_SLACK

_LOOKBACK: Final = timedelta(days=slo.SUGGESTION_WEEKS * slo.DAYS_IN_A_WEEK)
# The form lists at most this many of a service's endpoints, busiest first.
ENDPOINTS_LISTED: Final = 200
NAME_THE_SERVICE: Final = "Name the service whose history to read."
NAME_THE_ENDPOINT: Final = "Name the endpoint whose history to read."
CHOOSE_A_CHECK: Final = "Choose the check whose history to read."
NO_SUCH_CHECK: Final = "This project has no such check."


async def suggestion(
    db: AsyncSession,
    repository: TenantScopedRepository,
    *,
    identity: Identity,
    project_id: uuid.UUID,
    kind: str,
    service_name: str | None,
    endpoint_name: str | None,
    check_id: uuid.UUID | None,
    probe_locations: int,
    now: datetime,
) -> SloSuggestion:
    """The project is proved to be the caller's before anything is read; a check is
    proved to be the project's."""
    org_id = identity.organization.id
    await require_project(db, org_id=org_id, project_id=project_id)
    check = None
    if kind == slo.CHECK_AVAILABILITY:
        if check_id is None:
            raise InvalidRequestError(CHOOSE_A_CHECK, field="check_id")
        found = await slo_queries.check_in_project(
            db, org_id=org_id, project_id=project_id, check_id=check_id
        )
        if found is None:
            raise InvalidRequestError(NO_SUCH_CHECK, field="check_id")
        check = CheckFacts(
            id=found.id,
            interval_seconds=found.interval_seconds,
            locations=len(found.locations) or probe_locations,
        )
    elif not service_name:
        raise InvalidRequestError(NAME_THE_SERVICE, field="service")
    elif kind == slo.ENDPOINT_AVAILABILITY and not endpoint_name:
        raise InvalidRequestError(NAME_THE_ENDPOINT, field="endpoint")
    has_checks = await slo_queries.has_checks(db, org_id=org_id, project_id=project_id)
    return await suggest(
        repository,
        project_id=project_id,
        kind=kind,
        service_name=service_name,
        endpoint_name=endpoint_name,
        check=check,
        has_checks=has_checks,
        now=now,
    )


async def suggest(
    repository: TenantScopedRepository,
    *,
    project_id: uuid.UUID,
    kind: str,
    service_name: str | None,
    endpoint_name: str | None,
    check: CheckFacts | None,
    has_checks: bool,
    now: datetime,
) -> SloSuggestion:
    """Read the four weeks the kind needs and apply FR7 and FR8 to them."""
    bound: dict[str, Any] = {
        "project_id": str(project_id),
        "service": service_name or "",
        "now": now,
        "from_minute": now - _LOOKBACK,
    }
    endpoints: list[EndpointChoice] = []
    if kind == slo.SERVICE_LATENCY:
        rows = await repository.query(queries.LATENCY_WEEKS_SQL, bound)
        buckets = _by_week(rows, lambda row: [int(n) for n in row["buckets"]])
        found = slo.suggest_latency(
            [week or [0] * slo.BUCKET_COUNT for week in buckets],
            days_of_data=_days_of_data(rows, "first_minute", now),
        )
        return _answer(found, rows, "first_minute", now, kind=kind, has_checks=has_checks)
    if kind == slo.CHECK_AVAILABILITY:
        assert check is not None
        rows = await repository.query(
            queries.CHECK_WEEKS_SQL,
            bound
            | {
                "check_id": str(check.id),
                "received_from": now - _LOOKBACK - RECEIVE_CLOCK_SLACK,
                "received_to": now + RECEIVE_CLOCK_SLACK,
            },
        )
        weeks = _weeks(rows, good=lambda row: int(row["good"]))
        found = slo.suggest_availability(weeks, days_of_data=_days_of_data(rows, "first_run", now))
        return _answer(
            found, rows, "first_run", now, kind=kind, has_checks=False, weeks=weeks, check=check
        )
    statement = (
        queries.ENDPOINT_WEEKS_SQL
        if kind == slo.ENDPOINT_AVAILABILITY
        else queries.SERVICE_WEEKS_SQL
    )
    rows = await repository.query(statement, bound | {"endpoint": endpoint_name or ""})
    if kind == slo.SERVICE_AVAILABILITY:
        named = await repository.query(
            queries.ENDPOINT_NAMES_SQL, bound | {"limit": ENDPOINTS_LISTED}
        )
        endpoints = [
            EndpointChoice(name=name, health_check=slo.looks_like_health_check(name))
            for name in (str(row["span_name"]) for row in named)
        ]
    weeks = _weeks(rows, good=lambda row: int(row["total"]) - int(row["errors"]))
    found = slo.suggest_availability(weeks, days_of_data=_days_of_data(rows, "first_minute", now))
    return _answer(
        found,
        rows,
        "first_minute",
        now,
        kind=kind,
        has_checks=has_checks,
        weeks=weeks,
        endpoints=endpoints,
    )


def _by_week[Value](rows: Sequence[Mapping[str, Any]], value: Any) -> list[Value | None]:
    """Each of the four weeks' value, newest first; None for a week with no row."""
    found: list[Value | None] = [None] * slo.SUGGESTION_WEEKS
    for row in rows:
        week = int(row["week"])
        if 0 <= week < slo.SUGGESTION_WEEKS:
            found[week] = value(row)
    return found


def _weeks(rows: Sequence[Mapping[str, Any]], *, good: Any) -> list[slo.Week]:
    by_week: list[slo.Week | None] = _by_week(
        rows, lambda row: slo.Week(good=good(row), total=int(row["total"]))
    )
    return [week or slo.Week(good=0, total=0) for week in by_week]


def _days_of_data(rows: Sequence[Mapping[str, Any]], column: str, now: datetime) -> int:
    """Whole days since the first event in the last 28; 0 with none."""
    firsts = [row[column] for row in rows if row.get(column) is not None]
    if not firsts:
        return 0
    first: datetime = min(firsts)
    if first.tzinfo is None:
        first = first.replace(tzinfo=now.tzinfo)
    return max(0, (now - first).days)


def _answer(
    found: slo.Suggestion,
    rows: Sequence[Mapping[str, Any]],
    first_column: str,
    now: datetime,
    *,
    kind: str,
    has_checks: bool,
    weeks: Sequence[slo.Week] = (),
    endpoints: Sequence[EndpointChoice] = (),
    check: CheckFacts | None = None,
) -> SloSuggestion:
    index = found.boundary_index
    return SloSuggestion(
        outcome=found.outcome,
        objective=found.objective,
        weeks=[
            SuggestedWeek(good=week.good, total=week.total, figure=figure)
            for week, figure in zip(weeks, found.weeks, strict=False)
        ],
        worst=found.worst,
        typical=found.typical,
        best=found.best,
        days_of_data=_days_of_data(rows, first_column, now),
        boundary_index=index,
        boundary_ms=None if index is None else slo.boundary_ms(index),
        week_boundaries_ms=[
            None if held is None else slo.boundary_ms(held) for held in found.week_boundaries
        ],
        highest_boundary_ms=slo.HIGHEST_BOUNDARY_MS,
        boundaries_ms=[slo.boundary_ms(index) for index in range(slo.BOUNDARY_COUNT)],
        steps=list(slo.SUGGESTION_STEPS),
        thin_allowed=slo.THIN_ALLOWED,
        endpoints=list(endpoints),
        offer_check=(
            kind != slo.CHECK_AVAILABILITY and has_checks and found.outcome == slo.Suggested.TOO_FEW
        ),
        check=None if check is None else _allowance(check),
    )


def _allowance(check: CheckFacts) -> CheckAllowance:
    return CheckAllowance(
        interval_seconds=check.interval_seconds,
        windows=[
            CheckWindow(
                window_days=days,
                expected_runs=slo.expected_runs(
                    check.interval_seconds, window_days=days, locations=check.locations
                ),
                fastest_expected_runs=slo.expected_runs(
                    slo.CHECK_FASTEST_INTERVAL_SECONDS, window_days=days, locations=check.locations
                ),
            )
            for days in slo.WINDOWS_IN_DAYS
        ],
        alerts_can_judge=slo.alerts_can_judge_interval(check.interval_seconds),
    )
