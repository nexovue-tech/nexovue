"""A project's burns at one settled minute: what each target's tiers read, where its
burn stands, and each rule's judgment of it (design, *What each job does*; FR14 to FR18).

`judge_slos` reads the minutes once per project; this module adds what a burn needs
beside them, also once per project: nexovue's lost minutes over the last day, each
target's live exclusions, the days its window sums for a budget, and the project's
`slo_burn` rules. Then, per target:

- **The tiers** are summed from those minutes (`burn.window_counts`) and judged
  (`burn.judge`), with excluded minutes left out and lost ones counted against coverage.
- **The status** keeps when the burn began, the last minute a tier held, and since when
  no tier could judge for lack of coverage, which the blind notice reads (FR13b). A burn
  stays until 10 minutes after its last held minute, or until its source has been silent
  for a day with that day measured (FR17, FR18).
- **Each rule** covering an enabled target judges it in its own savepoint
  (`incidents.judged_alone`), so one refused value costs that judgment alone.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

from nexovue_platform import slo
from nexovue_platform.alerts.model import SLO_RULE_CONDITION
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from nexovue_scheduler.alerts import incidents
from nexovue_scheduler.alerts.rules import Rule, enabled_rules
from nexovue_scheduler.detection import holds
from nexovue_scheduler.detection.store import ProjectBinding
from nexovue_scheduler.slos import alerting, burn
from nexovue_scheduler.slos.store import CheckMinute, MinuteCounts

if TYPE_CHECKING:
    from nexovue_scheduler.slos.job import Target

SLO_BURN: Final = SLO_RULE_CONDITION.kind
_MINUTE: Final = timedelta(minutes=1)

# A project's live exclusions overlapping [since, until), by target (FR29).
_EXCLUSIONS: Final = text(
    """
    SELECT id, slo_id, starts_at, ends_at, created_at
    FROM slo_exclusions
    WHERE org_id = :org_id AND slo_id = ANY(:ids) AND removed_at IS NULL
      AND ends_at > :since AND starts_at < :until
    ORDER BY starts_at, id
    """
)
# The days a window sums, for the budget a burn's floor and "what is left" read (FR14).
_WINDOW_DAYS: Final = text(
    """
    SELECT slo_id, day, total, failures
    FROM slo_daily_counts
    WHERE org_id = :org_id AND slo_id = ANY(:ids) AND day >= :since
    """
)


@dataclass(frozen=True, slots=True)
class Exclusion:
    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WindowTotals:
    """What a target's stored window holds: its events, failures and days."""

    total: int
    failures: int
    days_held: int


@dataclass(frozen=True, slots=True)
class Inputs:
    """What every target's burn reads beside its minutes, read once for the project."""

    org_id: uuid.UUID
    project_id: uuid.UUID
    zone: ZoneInfo
    start: datetime
    end: datetime
    lost: frozenset[datetime]
    exclusions: Mapping[uuid.UUID, tuple[Exclusion, ...]]
    totals: Mapping[uuid.UUID, WindowTotals]
    rules: tuple[Rule, ...]


async def inputs(
    connection: AsyncConnection,
    binding: ProjectBinding,
    targets: Sequence[Target],
    *,
    zone: ZoneInfo,
    settled: datetime,
    start: datetime,
    end: datetime,
) -> Inputs:
    org_id, project_id = uuid.UUID(binding.org_id), uuid.UUID(binding.project_id)
    ids = [target.id for target in targets]
    lost = await holds.lost_minutes(connection, start=start, end=end)
    exclusions: dict[uuid.UUID, list[Exclusion]] = {}
    # A burn's detail names exclusions overlapping it since it began, up to a window.
    for row in await connection.execute(
        _EXCLUSIONS,
        {
            "org_id": org_id,
            "ids": ids,
            "since": settled - timedelta(days=max(slo.WINDOWS_IN_DAYS)),
            "until": end,
        },
    ):
        exclusions.setdefault(row.slo_id, []).append(
            Exclusion(row.id, row.starts_at, row.ends_at, row.created_at)
        )
    today = settled.astimezone(zone).date()
    held: dict[uuid.UUID, dict[date, tuple[int, int]]] = {}
    for row in await connection.execute(
        _WINDOW_DAYS,
        {
            "org_id": org_id,
            "ids": ids,
            "since": today - timedelta(days=max(slo.WINDOWS_IN_DAYS) - 1),
        },
    ):
        held.setdefault(row.slo_id, {})[row.day] = (row.total, row.failures)
    rules = await enabled_rules(
        connection, org_id=org_id, project_id=project_id, condition_kind=SLO_BURN
    )
    return Inputs(
        org_id=org_id,
        project_id=project_id,
        zone=zone,
        start=start,
        end=end,
        lost=lost,
        exclusions={target: tuple(found) for target, found in exclusions.items()},
        totals={
            target.id: _window_totals(target, held.get(target.id, {}), today, zone)
            for target in targets
        },
        rules=tuple(rules),
    )


def _window_totals(
    target: Target, held: Mapping[date, tuple[int, int]], today: date, zone: ZoneInfo
) -> WindowTotals:
    first_day = target.first_event_at.astimezone(zone).date() if target.first_event_at else None
    window = [
        held[day]
        for day in slo.window_days_of(today, target.window_days, first_event_day=first_day)
        if day in held
    ]
    return WindowTotals(
        total=sum(total for total, _ in window),
        failures=sum(failures for _, failures in window),
        days_held=len(window),
    )


# --------------------------------------------------------------- one target


@dataclass(frozen=True, slots=True)
class TargetBurn:
    """One target's burn at one minute: what its status keeps, and what rules judge."""

    target: Target
    minute: alerting.Minute
    tiers: dict[str, Any]
    burning_since: datetime | None
    last_held_minute: datetime | None
    unjudged_since: datetime | None


def judge_target(
    target: Target,
    found: Inputs,
    *,
    minutes: Mapping[datetime, MinuteCounts] | None,
    runs: Mapping[datetime, CheckMinute] | None,
    first_event_at: datetime | None,
    last_event: datetime | None,
    settled: datetime,
    zone: ZoneInfo,
    probe_locations: int,
) -> TargetBurn:
    exclusions = found.exclusions.get(target.id, ())
    excluded = slo.excluded_minutes_in(
        ((each.starts_at, each.ends_at) for each in exclusions), start=found.start, end=found.end
    )
    readings = _readings(
        target,
        minutes=minutes,
        runs=runs,
        lost=found.lost,
        excluded=excluded,
        end=found.end,
        since=first_event_at,
        probe_locations=probe_locations,
    )
    totals = found.totals.get(target.id, WindowTotals(0, 0, 0))
    allowed = slo.allowed_failures(target.objective, totals.total)
    left = allowed - totals.failures
    judgment = burn.judge(
        readings,
        objective=target.objective,
        window_days=target.window_days,
        budget=allowed if totals.total else None,
        left=left if totals.total else None,
        is_check=target.is_check,
    )
    slow_long = readings[slo.TIERS[-1].name].long
    source_stopped = (
        last_event is not None
        and settled - last_event >= slo.LONGEST_TIER_WINDOW
        and slow_long.coverage is not None
        and slow_long.coverage >= slo.COVERAGE_TO_JUDGE
    )
    burning_since, last_held = _burning(target, judgment, settled, source_stopped)
    detail = alerting.burn_detail(
        target_id=target.id,
        kind=target.kind,
        service_name=target.service_name,
        endpoint_name=target.endpoint_name,
        check_id=target.check_id,
        objective=target.objective,
        window_days=target.window_days,
        judgment=judgment,
        burning_since=burning_since,
        allowed=allowed,
        left=left,
        zone=str(zone),
        minute=settled,
        exclusions=_overlapping(exclusions, burning_since, settled),
    )
    return TargetBurn(
        target=target,
        minute=alerting.Minute(
            subject=alerting.burn_subject(
                target.id,
                label=target.label,
                kind=target.kind,
                service_name=target.service_name,
                endpoint_name=target.endpoint_name,
                check_id=target.check_id,
            ),
            minute=settled,
            judgment=judgment,
            detail=detail,
            source_stopped=source_stopped,
        ),
        tiers=_stored(target, judgment, burning_since),
        burning_since=burning_since,
        last_held_minute=last_held,
        unjudged_since=_unjudged_since(target, readings, judgment, settled),
    )


def _readings(
    target: Target,
    *,
    minutes: Mapping[datetime, MinuteCounts] | None,
    runs: Mapping[datetime, CheckMinute] | None,
    lost: frozenset[datetime],
    excluded: frozenset[datetime],
    end: datetime,
    since: datetime | None,
    probe_locations: int,
) -> dict[slo.TierName, burn.TierReading]:
    readings = {}
    interval = target.check_interval_seconds or slo.CHECK_FASTEST_INTERVAL_SECONDS
    for tier in slo.TIERS:
        if target.is_check:
            short = burn.short_window(tier, interval_seconds=interval)
            windows = [
                burn.check_window_counts(
                    runs or {},
                    end=end,
                    length=length,
                    interval_seconds=interval,
                    locations=len(target.check_locations) or probe_locations,
                    excluded=excluded,
                    since=since,
                )
                for length in (tier.long, short)
            ]
        else:
            windows = [
                burn.window_counts(
                    minutes or {},
                    end=end,
                    length=length,
                    lost=lost,
                    excluded=excluded,
                    since=since,
                )
                for length in (tier.long, tier.short)
            ]
        readings[tier.name] = burn.TierReading(long=windows[0], short=windows[1])
    return readings


def _burning(
    target: Target, judgment: burn.BurnJudgment, settled: datetime, source_stopped: bool
) -> tuple[datetime | None, datetime | None]:
    """When the burn began and the last minute a tier held. It ends as `step()` resolves:
    10 minutes after the first minute that did not hold (FR18). A minute no tier can
    judge changes neither (FR17), until the source has been silent a measured day."""
    if judgment.breached:
        return target.burning_since or settled, settled
    since, last_held = target.burning_since, target.last_held_minute
    if source_stopped:
        return None, last_held
    if (
        judgment.breached is False
        and since is not None
        and (last_held is None or settled - (last_held + _MINUTE) >= alerting.RESOLVE_AFTER)
    ):
        return None, last_held
    return since, last_held


def _stored(
    target: Target, judgment: burn.BurnJudgment, burning_since: datetime | None
) -> dict[str, Any]:
    """`slo_status.tiers`: this minute's tiers, and the burn's last held figures, kept
    while it lasts so a resolving or unjudged burn still says how bad it was (FR10)."""
    stored = judgment.stored()
    previous = target.tiers.get("burn") if burning_since is not None else None
    if judgment.holding is not None:
        stored["burn"] = {
            "severity": judgment.severity,
            "rate": judgment.rate,
            "hours_left": judgment.hours_left,
        }
    else:
        stored["burn"] = previous
    return stored


def _unjudged_since(
    target: Target,
    readings: Mapping[slo.TierName, burn.TierReading],
    judgment: burn.BurnJudgment,
    settled: datetime,
) -> datetime | None:
    """Since when every tier has been unable to judge because too little of its long
    window was measured (FR13b). Thin traffic never starts it."""
    blind = judgment.breached is None and all(
        reading.long.coverage is not None and reading.long.coverage < slo.COVERAGE_TO_JUDGE
        for reading in readings.values()
    )
    if not blind:
        return None
    return target.unjudged_since or settled


def _overlapping(
    exclusions: Sequence[Exclusion], since: datetime | None, until: datetime
) -> list[dict[str, Any]]:
    if since is None:
        return []
    return [
        {
            "id": str(each.id),
            "starts_at": each.starts_at.isoformat(),
            "ends_at": each.ends_at.isoformat(),
        }
        for each in exclusions
        if each.starts_at <= until and each.ends_at > since
    ]


def building_history(target: Target, totals: WindowTotals) -> bool:
    """FR10's Building history: fewer than a quarter of the window's days held."""
    return totals.days_held < math.ceil(target.window_days / slo.BUILDING_SHARE_OF_WINDOW)


# ----------------------------------------------------------------- the rules


async def judge_rules(connection: AsyncConnection, found: Inputs, state: TargetBurn) -> int:
    """Every enabled rule covering the target judges its burn. Returns notifications."""
    written = 0
    for rule in found.rules:
        condition = alerting.covers(rule, state.target.id)
        if condition is None:
            continue
        written += await incidents.judged_alone(
            connection,
            rule,
            partial(alerting.judge_under, connection, rule, condition, state.minute),
        )
    return written
