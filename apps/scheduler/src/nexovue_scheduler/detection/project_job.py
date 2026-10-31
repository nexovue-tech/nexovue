"""One project's judging: each service's settled minutes, its readings and its
findings (design, "Where it runs", "Findings and their state", "Failure handling").

A run, all inside one Postgres transaction that holds the project's lock:

1. **The lock.** A second job for the same project finds it held and does
   nothing; the lock ends with the transaction, a killed job's included.
2. **Which minutes.** Each service resumes after its `last_judged_minute`, at most
   2 minutes per run; more than 10 behind, it restarts at the settled minute and
   judges nothing older. A service seen for the first time gets its state row now
   (C24) and starts at the settled minute.
3. **Judging**, against the hourly baseline (`baselines.py`). A missing baseline
   judges nothing for that minute and asks for one.
4. **Readings** are frozen in ClickHouse before the commit. A crash after them
   judges the minutes again, and the newer reading wins (`store.READINGS_SQL`).
5. **The commit**: each service's `last_judged_minute` by compare-and-set, its
   finding opened, raised or closed, and the project's completed run, which is
   what "detection not running" is measured from (C17).

*Slower* only (plan T2c.3). Notifications, the 6-hour close and the project
finding come in Steps 3 and 4.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from nexovue_platform.detection import FindingSignal, finding_signal
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.detection import JUDGE_PROJECT_TASK
from nexovue_scheduler.detection.baselines import (
    SLOWER,
    baseline_from_json,
    compute_request,
)
from nexovue_scheduler.detection.method import (
    JUDGED,
    RECOVERY_MINUTES,
    Minute,
    Sensitivity,
    SlowerBaseline,
    SlowerJudgement,
    Window,
    judge_slower,
)
from nexovue_scheduler.detection.projects import project_settings, try_lock
from nexovue_scheduler.detection.store import (
    DetectionStore,
    ProjectBinding,
    Reading,
    bind_project,
    store_for_process,
)
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import enqueue, with_engine

logger = get_logger(__name__)

MINUTE: Final = timedelta(minutes=1)
# At most this many minutes per service per run (design, "Where it runs").
CATCH_UP_MINUTES: Final = 2
# Further behind than this, judging restarts at the settled minute (spec, *Backfill*).
RESTART_AFTER: Final = timedelta(minutes=10)
# A project job's budget (design): it stops where it is, and the next run continues.
PROJECT_SOFT_LIMIT_SECONDS: Final = 20
PROJECT_HARD_LIMIT_SECONDS: Final = 30
_LOCK_PURPOSE: Final = "judging"

# The two signals Step 3 adds are written as not judged until then.
NOT_JUDGED_YET: Final = "not_judged"
NO_MODE: Final = "none"

_WEEKDAY_NAMES: Final = (
    "Mondays",
    "Tuesdays",
    "Wednesdays",
    "Thursdays",
    "Fridays",
    "Saturdays",
    "Sundays",
)
_WEEKEND_FROM: Final = 5
_TENS: Final = 10.0
# The sign in "4 times slower" as a title writes it, escaped so it cannot pass for an x.
TIMES_SIGN: Final = "\u00d7"

_STATES: Final = text(
    """
    SELECT service_name, last_judged_minute, signals FROM detection_state
    WHERE org_id = :org_id AND project_id = :project_id
    """
)
_FIRST_SIGHT: Final = text(
    """
    INSERT INTO detection_state (org_id, project_id, service_name)
    VALUES (:org_id, :project_id, :service_name)
    ON CONFLICT (org_id, project_id, service_name) DO NOTHING
    """
)
_BASELINES: Final = text(
    """
    SELECT service_name, minute, baseline FROM detection_baselines
    WHERE org_id = :org_id AND project_id = :project_id AND signal = :signal
      AND minute >= :first AND minute <= :last
    """
)
_MUTED: Final = text(
    """
    SELECT service_name FROM service_detection_settings
    WHERE org_id = :org_id AND project_id = :project_id AND muted_until > :now
    """
)
# The compare-and-set: moved only from where this job found it (design).
_ADVANCE: Final = text(
    """
    UPDATE detection_state
    SET last_judged_minute = :judged, signals = CAST(:signals AS jsonb)
    WHERE org_id = :org_id AND project_id = :project_id AND service_name = :service_name
      AND last_judged_minute IS NOT DISTINCT FROM :expected
    """
)
_OPEN_FINDING: Final = text(
    """
    SELECT id, severity, signals, started_at FROM findings
    WHERE org_id = :org_id AND project_id = :project_id AND scope = 'service'
      AND service_name = :service_name AND state = 'open'
    FOR UPDATE
    """
)
_OPEN: Final = text(
    """
    INSERT INTO findings
        (org_id, project_id, scope, service_name, severity, muted, title, signals, started_at)
    VALUES (:org_id, :project_id, 'service', :service_name, :severity, :muted, :title,
            CAST(:signals AS jsonb), :started_at)
    RETURNING id
    """
)
_UPDATE: Final = text(
    """
    UPDATE findings
    SET severity = :severity, title = :title, signals = CAST(:signals AS jsonb), updated_at = now()
    WHERE org_id = :org_id AND id = :id
    """
)
_CLOSE: Final = text(
    """
    UPDATE findings
    SET state = 'closed', closed_reason = 'recovered', closed_at = :closed_at,
        signals = CAST(:signals AS jsonb), updated_at = now()
    WHERE org_id = :org_id AND id = :id
    """
)
# When the run ended, not when its transaction began: freshness (C17).
_COMPLETED: Final = text(
    """
    INSERT INTO detection_project_runs (org_id, project_id, last_completed_at)
    VALUES (:org_id, :project_id, clock_timestamp())
    ON CONFLICT (org_id, project_id) DO UPDATE
    SET last_completed_at = EXCLUDED.last_completed_at
    """
)


class StaleJudgementError(RuntimeError):
    """A service's state moved under this job: another judged it. Nothing commits."""


@dataclass(frozen=True, slots=True)
class ServiceState:
    service_name: str
    last_judged_minute: datetime | None
    signals: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Outcome:
    """What one run did, for its log line."""

    org_id: str | None = None
    skipped: str | None = None
    services: int = 0
    minutes_judged: int = 0
    opened: int = 0
    closed: int = 0
    baselines_missing: bool = False


@dataclass(frozen=True, slots=True)
class Judged:
    minute: datetime
    slower: SlowerJudgement
    baseline: SlowerBaseline | None


def minutes_to_judge(last_judged: datetime | None, settled: datetime) -> list[datetime]:
    """The minutes one run judges for a service."""
    if last_judged is None or settled - last_judged > RESTART_AFTER:
        return [settled]
    behind = (settled - last_judged) // MINUTE
    return [last_judged + step * MINUTE for step in range(1, min(behind, CATCH_UP_MINUTES) + 1)]


async def judge_project(
    engine: AsyncEngine,
    store: DetectionStore,
    project_id: uuid.UUID,
    *,
    settled: datetime,
    now: datetime,
) -> Outcome:
    """Judge one project's services up to the settled minute."""
    async with engine.connect() as connection, connection.begin():
        if not await try_lock(connection, _LOCK_PURPOSE, project_id):
            return Outcome(skipped="another job is judging this project")
        binding = await bind_project(connection, project_id)
        if binding is None:
            return Outcome(skipped="the project is gone")
        outcome = Outcome(org_id=binding.org_id)
        settings = await project_settings(connection, binding)
        width = settings.sensitivity.window_minutes
        states = await _states(connection, binding)
        plan = {
            state.service_name: minutes_to_judge(state.last_judged_minute, settled)
            for state in states.values()
        }
        earliest = min((m for minutes in plan.values() for m in minutes), default=settled)
        detection = store.for_project(binding)
        counted = detection.window_minutes(
            start=min(earliest, settled) - (width - 1) * MINUTE, end=settled + MINUTE
        )
        for service in sorted(set(counted) - set(states)):
            await connection.execute(_FIRST_SIGHT, _service_key(binding, service))
            states[service] = ServiceState(service, None)
            plan[service] = [settled]
        plan = {service: minutes for service, minutes in plan.items() if minutes}
        judged_minutes = [m for minutes in plan.values() for m in minutes]
        if not judged_minutes:
            await _record_run(connection, binding)
            return outcome
        baselines = await _baselines(connection, binding, min(judged_minutes), max(judged_minutes))
        judged: dict[str, list[Judged]] = {}
        for service, minutes in sorted(plan.items()):
            history = counted.get(service, {})
            judged[service] = [
                _judge(history, baselines.get((service, minute)), minute, settings.sensitivity)
                for minute in minutes
            ]
        outcome.baselines_missing = any(
            each.slower.verdict == "no_baseline" for runs in judged.values() for each in runs
        )
        detection.write(
            [
                _reading(service, each, width)
                for service, runs in sorted(judged.items())
                for each in runs
            ]
        )
        muted = await _muted(connection, binding, now)
        for service, runs in sorted(judged.items()):
            state = states[service]
            signals = await _keep_finding(
                connection,
                binding,
                state,
                runs,
                zone=settings.zone,
                width=width,
                muted=service in muted,
                outcome=outcome,
            )
            await _advance(connection, binding, state, runs[-1].minute, signals)
            outcome.minutes_judged += len(runs)
        outcome.services = len(judged)
        await _record_run(connection, binding)
    return outcome


def _judge(
    history: Mapping[datetime, Minute],
    baseline: SlowerBaseline | None,
    minute: datetime,
    sensitivity: Sensitivity,
) -> Judged:
    width = sensitivity.window_minutes
    window = Window(
        tuple(
            history.get(minute - back * MINUTE, Minute.empty()) for back in range(width - 1, -1, -1)
        )
    )
    return Judged(minute, judge_slower(window, baseline, sensitivity), baseline)


# ------------------------------------------------------------------ findings


async def _keep_finding(
    connection: AsyncConnection,
    binding: ProjectBinding,
    state: ServiceState,
    runs: Sequence[Judged],
    *,
    zone: ZoneInfo,
    width: int,
    muted: bool,
    outcome: Outcome,
) -> dict[str, Any]:
    """Open, raise or close the service's one finding over these minutes, in order,
    and return the service's signal state to store (FR12 to FR14)."""
    slower_state = dict(state.signals.get(SLOWER, {}))
    clear = int(slower_state.get("clear_minutes", 0))
    key = _service_key(binding, state.service_name)
    row = (await connection.execute(_OPEN_FINDING, key)).one_or_none()
    finding = None if row is None else _OpenFinding.from_row(row)
    for each in runs:
        verdict = each.slower.verdict
        if verdict == "unusual":
            clear = 0
            if finding is None:
                finding = await _open(
                    connection, binding, state.service_name, each, zone, width, muted
                )
                outcome.opened += 1
            else:
                await _raise(connection, binding, state.service_name, finding, each, zone)
        elif finding is not None and verdict in JUDGED:
            finding.current = each.slower.window_p95_ms
            finding.dirty = True
            clear = clear + 1 if verdict == "normal" else 0
            if clear >= RECOVERY_MINUTES:
                await connection.execute(
                    _CLOSE,
                    {
                        "org_id": key["org_id"],
                        "id": finding.id,
                        "closed_at": each.minute,
                        "signals": json.dumps([finding.signal()]),
                    },
                )
                outcome.closed += 1
                finding, clear = None, 0
        # Too little traffic, learning or no baseline pause the count (FR11, FR13).
    if finding is not None and finding.dirty:
        await _store(connection, binding, state.service_name, finding, zone)
    last = runs[-1]
    learning = last.baseline.learning_until if last.baseline is not None else None
    slower_state.update(
        verdict=last.slower.verdict,
        clear_minutes=clear,
        learning_until=None if learning is None else learning.isoformat(),
    )
    return {**state.signals, SLOWER: slower_state}


@dataclass(slots=True)
class _OpenFinding:
    id: uuid.UUID
    severity: str
    started_at: datetime
    normal: float | None
    current: float | None
    worst: float | None
    comparison: str
    dirty: bool = False

    @classmethod
    def from_row(cls, row: Any) -> _OpenFinding:
        stored: dict[str, Any] = next(
            (each for each in row.signals or [] if each.get("signal") == SLOWER), {}
        )
        return cls(
            id=row.id,
            severity=row.severity,
            started_at=row.started_at,
            normal=stored.get("normal"),
            current=stored.get("current"),
            worst=stored.get("worst"),
            comparison=stored.get("comparison", ""),
        )

    @property
    def worst_ratio(self) -> float:
        if not self.worst or not self.normal:
            return 0.0
        return self.worst / self.normal

    def signal(self) -> FindingSignal:
        return finding_signal(
            SLOWER,
            normal=self.normal,
            current=self.current,
            worst=self.worst,
            comparison=self.comparison,
        )


async def _open(
    connection: AsyncConnection,
    binding: ProjectBinding,
    service: str,
    each: Judged,
    zone: ZoneInfo,
    width: int,
    muted: bool,
) -> _OpenFinding:
    """A new finding, dated from its unusual window's first minute."""
    assert each.baseline is not None, "an unusual window was judged against a baseline"
    severity = "critical" if each.slower.critical else "warning"
    started_at = each.minute - (width - 1 - each.slower.calm_minutes) * MINUTE
    p95 = each.slower.window_p95_ms
    signal = finding_signal(
        SLOWER,
        normal=each.baseline.normal_p95_ms,
        current=p95,
        worst=p95,
        comparison=comparison(each.baseline, each.minute, zone),
    )
    finding_id = (
        await connection.execute(
            _OPEN,
            {
                **_service_key(binding, service),
                "severity": severity,
                "muted": muted,
                "title": slower_title(service, each.slower.ratio or 0.0, started_at, zone),
                "signals": json.dumps([signal]),
                "started_at": started_at,
            },
        )
    ).scalar_one()
    return _OpenFinding(
        id=finding_id,
        severity=severity,
        started_at=started_at,
        normal=signal["normal"],
        current=signal["current"],
        worst=signal["worst"],
        comparison=signal["comparison"],
    )


async def _raise(
    connection: AsyncConnection,
    binding: ProjectBinding,
    service: str,
    finding: _OpenFinding,
    each: Judged,
    zone: ZoneInfo,
) -> None:
    """An unusual minute on an open finding: severity only rises (FR14), and the
    worst value, its normal and the title follow the worst window (FR17)."""
    finding.current = each.slower.window_p95_ms
    finding.dirty = True
    if each.slower.critical:
        finding.severity = "critical"
    ratio = each.slower.ratio or 0.0
    if ratio > finding.worst_ratio and each.baseline is not None:
        finding.worst = each.slower.window_p95_ms
        finding.normal = each.baseline.normal_p95_ms
        finding.comparison = comparison(each.baseline, each.minute, zone)


async def _store(
    connection: AsyncConnection,
    binding: ProjectBinding,
    service: str,
    finding: _OpenFinding,
    zone: ZoneInfo,
) -> None:
    await connection.execute(
        _UPDATE,
        {
            "org_id": uuid.UUID(binding.org_id),
            "id": finding.id,
            "severity": finding.severity,
            "title": slower_title(service, finding.worst_ratio, finding.started_at, zone),
            "signals": json.dumps([finding.signal()]),
        },
    )


def slower_title(service: str, ratio: float, since: datetime, zone: ZoneInfo) -> str:
    """FR17: subject, what changed, magnitude, since when, in the project's zone."""
    return f"{service} is {times(ratio)} slower than normal since {local_clock(since, zone)}"


def times(ratio: float) -> str:
    """1.6, 4 and 12, each with the sign: one decimal below ten, none from ten."""
    if ratio >= _TENS:
        return f"{ratio:.0f}{TIMES_SIGN}"
    return f"{ratio:.1f}".removesuffix(".0") + TIMES_SIGN


def local_clock(moment: datetime, zone: ZoneInfo) -> str:
    """15:04 IST: the time in the project's zone, with the zone's abbreviation."""
    return moment.astimezone(zone).strftime("%H:%M %Z")


def comparison(baseline: SlowerBaseline, minute: datetime, zone: ZoneInfo) -> str:
    """What the minute was compared with: "Tuesdays around 15:00, last 3 weeks"."""
    local = minute.astimezone(zone)
    clock = local.strftime("%H:%M")
    if baseline.mode == "weekly":
        return f"{_WEEKDAY_NAMES[local.weekday()]} around {clock}, last {baseline.days} weeks"
    kind = "weekend days" if local.weekday() >= _WEEKEND_FROM else "weekdays"
    return f"{kind.capitalize()} around {clock}, last {baseline.days} {kind}"


# ------------------------------------------------------------------- stores


def _service_key(binding: ProjectBinding, service: str) -> dict[str, Any]:
    return {
        "org_id": uuid.UUID(binding.org_id),
        "project_id": uuid.UUID(binding.project_id),
        "service_name": service,
    }


def _project_key(binding: ProjectBinding) -> dict[str, Any]:
    return {"org_id": uuid.UUID(binding.org_id), "project_id": uuid.UUID(binding.project_id)}


async def _states(connection: AsyncConnection, binding: ProjectBinding) -> dict[str, ServiceState]:
    rows = await connection.execute(_STATES, _project_key(binding))
    return {
        row.service_name: ServiceState(row.service_name, row.last_judged_minute, row.signals or {})
        for row in rows
    }


async def _baselines(
    connection: AsyncConnection, binding: ProjectBinding, first: datetime, last: datetime
) -> dict[tuple[str, datetime], SlowerBaseline]:
    rows = await connection.execute(
        _BASELINES, {**_project_key(binding), "signal": SLOWER, "first": first, "last": last}
    )
    return {
        (row.service_name, row.minute.astimezone(UTC)): baseline_from_json(row.baseline)
        for row in rows
    }


async def _muted(connection: AsyncConnection, binding: ProjectBinding, now: datetime) -> set[str]:
    rows = await connection.execute(_MUTED, {**_project_key(binding), "now": now})
    return {row.service_name for row in rows}


async def _advance(
    connection: AsyncConnection,
    binding: ProjectBinding,
    state: ServiceState,
    judged: datetime,
    signals: dict[str, Any],
) -> None:
    moved = await connection.execute(
        _ADVANCE,
        {
            **_service_key(binding, state.service_name),
            "judged": judged,
            "expected": state.last_judged_minute,
            "signals": json.dumps(signals),
        },
    )
    if moved.rowcount != 1:
        raise StaleJudgementError(
            f"{state.service_name} was judged past {state.last_judged_minute} by another job"
        )


async def _record_run(connection: AsyncConnection, binding: ProjectBinding) -> None:
    await connection.execute(_COMPLETED, _project_key(binding))


def _reading(service: str, each: Judged, width: int) -> Reading:
    slower, baseline = each.slower, each.baseline
    window_errors = 0  # *failing more* fills this in Step 3
    return Reading(
        service_name=service,
        minute=each.minute,
        window_minutes=width,
        window_requests=slower.window_requests,
        window_errors=window_errors,
        window_p95_ms=slower.window_p95_ms,
        window_above_threshold=slower.above_threshold,
        endpoint_failures={},
        traffic_mode=NO_MODE,
        traffic_days=0,
        traffic_median=None,
        traffic_spread=None,
        traffic_verdict=NOT_JUDGED_YET,
        failing_mode=NO_MODE,
        failing_days=0,
        failing_median=None,
        failing_spread=None,
        failing_burst_height=None,
        failing_burst_minutes=0,
        failing_verdict=NOT_JUDGED_YET,
        slower_mode=NO_MODE if baseline is None else baseline.mode,
        slower_days=0 if baseline is None else baseline.days,
        slower_median=None if baseline is None else baseline.normal_p95_ms,
        slower_spread=None if baseline is None else baseline.wander,
        slower_threshold_ms=None if baseline is None else baseline.threshold_ms,
        slower_verdict=slower.verdict,
    )


# -------------------------------------------------------------------- task


def judge_request_kwargs(project_id: uuid.UUID, settled: datetime) -> dict[str, str]:
    return {"project_id": str(project_id), "settled_minute": settled.isoformat()}


@task(JUDGE_PROJECT_TASK)
def judge(*, project_id: str, settled_minute: str) -> None:
    """One project's minutes up to the settled minute the coordinator read."""
    store = store_for_process()
    if store is None:
        return
    project = uuid.UUID(project_id)
    settled = datetime.fromisoformat(settled_minute)
    outcome = with_engine(
        lambda engine: judge_project(engine, store, project, settled=settled, now=datetime.now(UTC))
    )
    logger.info(
        "detection project judged",
        project_id=project_id,
        settled_minute=settled_minute,
        skipped=outcome.skipped,
        services=outcome.services,
        minutes=outcome.minutes_judged,
        opened=outcome.opened,
        closed=outcome.closed,
    )
    if outcome.baselines_missing and outcome.org_id is not None:
        enqueue(compute_request(project, uuid.UUID(outcome.org_id)))
