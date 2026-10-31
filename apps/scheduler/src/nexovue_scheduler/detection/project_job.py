"""One project's judging: each service's settled minutes, its readings and its
findings (design, "Where it runs", "Findings and their state", "Failure handling").

A run, all inside one Postgres transaction that holds the project's lock:

1. **The lock.** A second job for the same project finds it held and does
   nothing; the lock ends with the transaction, a killed job's included.
2. **Which minutes.** Each service resumes after its `last_judged_minute`, at most
   2 minutes per run; more than 10 behind, it restarts at the settled minute and
   judges nothing older. A service seen for the first time gets its state row now
   (C24) and starts at the settled minute.
3. **Judging** all three signals and each endpoint, against the hourly baseline
   (`baselines.py`). A missing baseline judges nothing for that minute and asks
   for one.
4. **Readings** are frozen in ClickHouse before the commit. A crash after them
   judges the minutes again, and the newer reading wins (`store.READINGS_SQL`).
5. **The project** (FR15): whether most of its services stopped sending together,
   which is one project finding rather than one per service.
6. **The commit**: each service's state by compare-and-set, its one finding
   opened, raised, joined or closed (`lifecycle.py`), the project's finding, the
   notifications each of those writes (`notify.py`), each open finding's links to
   the threshold and *went silent* incidents on its service (BR2, AC20), and the
   project's completed run, which is what "detection not running" is measured
   from (C17). A run that ends a stop the owners were told of tells them it runs
   again (D24).

The service's state also records what its normal must leave out that no other
table holds (BR3): its snoozed minutes (D25) and, before 3 weeks, the minutes
the traffic shadow test found far below normal (FR6).

**What people said** (FR20, FR21). A finding a person closed as a false alarm
holds its signals: they open nothing until each reads normal for *recovery*, and
while a member's hold lasts its minutes stay out of the normal too (D28). A new
normal moves the normal by a `baseline_shifts` row; a normal built before the
shift is never judged by, so the minute waits for the rebuilt one (AC15). A
finding open a week with the same signals, nobody having answered, is adopted the
same way, unless D22 forbids it, and the owners are told (AC16, AC44).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from nexovue_platform.alerts.model import service_subject
from nexovue_platform.alerts.tasks import DELIVER_TASK, DELIVERIES_QUEUE
from nexovue_platform.detection import (
    ADOPTED_AUDIT_ACTION,
    ADOPTION_AFTER_DAYS,
    FINDING_AUDIT_TARGET,
    FindingSignal,
    compute_baselines_request,
    finding_signal,
    shift_ratio,
)
from nexovue_platform.jobs import JobRequest
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.alerts.incidents import SERVICE_KINDS
from nexovue_scheduler.detection import JUDGE_PROJECT_TASK, notify
from nexovue_scheduler.detection.baselines import (
    EXCLUDED_KEY,
    FAILING,
    HISTORY,
    SHADOW,
    SLOWER,
    SNOOZED,
    TRAFFIC,
    failing_from_json,
    merge_ranges,
    slower_from_json,
    traffic_from_json,
)
from nexovue_scheduler.detection.lifecycle import (
    Change,
    Context,
    KeyRecord,
    MinuteJudged,
    Normal,
    OpenFinding,
    TrafficSnapshot,
    adoptable,
    advance_holds,
    endpoint_key,
    key_signal,
    project_rule,
    project_title,
    step,
    title,
    traffic_waits,
    weight,
)
from nexovue_scheduler.detection.method import (
    FailingBaseline,
    FailingJudgement,
    Minute,
    Sensitivity,
    SlowerBaseline,
    TrafficBaseline,
    Window,
    judge_failing,
    judge_slower,
    judge_traffic,
)
from nexovue_scheduler.detection.projects import project_settings, try_lock
from nexovue_scheduler.detection.store import (
    DetectionStore,
    ProjectBinding,
    ProjectDetection,
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
NO_MODE: Final = "none"
# Under `detection_state.signals`: the open finding's keys and counts, and the last
# minute the service had requests (FR32).
FINDING_KEY: Final = "finding"
LAST_REQUEST_KEY: Final = "last_request_minute"
# Under the failing signal's state: each endpoint's runs above the base test.
ENDPOINTS_KEY: Final = "endpoints"
# What the project finding's one entry compares with.
PROJECT_COMPARISON: Final = "Each service against its own normal"
# Under `detection_state.signals`: the keys a false alarm holds, each with its clear
# minutes (FR20), and whether the hold's minutes stay out of the normal (D28).
HELD_KEY: Final = "held"
HELD_OUT_KEY: Final = "held_out"
# Why minutes under a member's hold are kept out, beside snoozed and shadowed ones.
HELD_OUT: Final = "held"
# Under the open finding's record: when its keys last changed (AC16's "the same signals").
SINCE_KEY: Final = "since"
ADOPTION_AFTER: Final = timedelta(days=ADOPTION_AFTER_DAYS)

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
# A normal built before its signal's latest shift is never judged by: it is the old
# level, and would reopen what a person just accepted (FR21, AC15). The minute
# waits for the rebuilt normal, which the job then asks for.
_BASELINES: Final = text(
    """
    SELECT baseline.service_name, baseline.signal, baseline.minute, baseline.baseline
    FROM detection_baselines AS baseline
    WHERE baseline.org_id = :org_id AND baseline.project_id = :project_id
      AND baseline.minute >= :first AND baseline.minute <= :last
      AND NOT EXISTS (
          SELECT 1 FROM baseline_shifts AS shift
          WHERE shift.org_id = baseline.org_id AND shift.project_id = baseline.project_id
            AND shift.service_name = baseline.service_name AND shift.signal = baseline.signal
            AND shift.created_at > baseline.computed_at
      )
    """
)
# The findings this project's state still names that a person has closed as a false
# alarm since the last run, and whether it was an admin's (D28).
_FALSE_ALARMS: Final = text(
    """
    SELECT id, false_alarm_returns_minutes FROM findings
    WHERE org_id = :org_id AND project_id = :project_id AND id = ANY(CAST(:ids AS uuid[]))
      AND state = 'closed' AND closed_reason = 'false_alarm'
    """
)
_ADOPTED_SHIFT: Final = text(
    """
    INSERT INTO baseline_shifts
        (org_id, project_id, service_name, signal, from_minute, ratio, reason)
    VALUES (:org_id, :project_id, :service_name, :signal, :from_minute, :ratio, 'adopted')
    """
)
# The system acted, so no person is named (FR21), as the erasure's own record does.
_AUDIT_ADOPTED: Final = text(
    """
    INSERT INTO audit_log (org_id, actor_user_id, action, target, detail)
    VALUES (:org_id, NULL, :action, :target, CAST(:detail AS jsonb))
    """
)
_SERVICE_SETTINGS: Final = text(
    """
    SELECT service_name, muted_until, snoozed_until FROM service_detection_settings
    WHERE org_id = :org_id AND project_id = :project_id
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
    SELECT id, severity, muted, signals, started_at FROM findings
    WHERE org_id = :org_id AND project_id = :project_id AND scope = 'service'
      AND service_name = :service_name AND state = 'open'
    FOR UPDATE
    """
)
_OPEN_PROJECT_FINDING: Final = text(
    """
    SELECT id, signals, started_at FROM findings
    WHERE org_id = :org_id AND project_id = :project_id AND scope = 'project'
      AND state = 'open'
    FOR UPDATE
    """
)
_OPEN: Final = text(
    """
    INSERT INTO findings
        (org_id, project_id, scope, service_name, severity, muted, title, signals, services,
         started_at)
    VALUES (:org_id, :project_id, :scope, :service_name, :severity, :muted, :title,
            CAST(:signals AS jsonb), CAST(:services AS jsonb), :started_at)
    RETURNING id
    """
)
_UPDATE: Final = text(
    """
    UPDATE findings
    SET severity = :severity, title = :title, signals = CAST(:signals AS jsonb),
        services = CAST(:services AS jsonb), updated_at = now()
    WHERE org_id = :org_id AND id = :id
    """
)
_CLOSE: Final = text(
    """
    UPDATE findings
    SET state = 'closed', closed_reason = :reason, closed_at = :closed_at,
        signals = CAST(:signals AS jsonb), updated_at = now()
    WHERE org_id = :org_id AND id = :id
    """
)
# Whether the owners were told this project's detection stopped (D24), held until
# the commit so the stop sweep cannot tell them again in between.
_STOP_REPORTED: Final = text(
    """
    SELECT stopped_reported_at FROM detection_project_runs
    WHERE org_id = :org_id AND project_id = :project_id
    FOR UPDATE
    """
)
# When the run ended, not when its transaction began: freshness (C17).
_COMPLETED: Final = text(
    """
    INSERT INTO detection_project_runs (org_id, project_id, last_completed_at)
    VALUES (:org_id, :project_id, clock_timestamp())
    ON CONFLICT (org_id, project_id) DO UPDATE
    SET last_completed_at = EXCLUDED.last_completed_at, stopped_reported_at = NULL
    """
)
# BR2, AC20: an open finding is linked to every open threshold or *went silent*
# incident on its service. Each run, so an incident that opened while this job was
# deciding is linked by the next run if its own sweep could not see the finding.
_LINK_INCIDENTS: Final = text(
    """
    INSERT INTO finding_incidents (org_id, finding_id, incident_id)
    SELECT :org_id, :finding_id, incidents.id
    FROM incidents
    WHERE incidents.org_id = :org_id AND incidents.project_id = :project_id
      AND incidents.subject_key = :subject_key AND incidents.resolved_at IS NULL
      AND incidents.condition_kind = ANY(CAST(:kinds AS text[]))
    ON CONFLICT DO NOTHING
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
    notified: int = 0
    baselines_missing: bool = False


@dataclass(frozen=True, slots=True)
class Normals:
    """One service-minute's three baselines, any of them missing."""

    slower: SlowerBaseline | None = None
    failing: FailingBaseline | None = None
    traffic: TrafficBaseline | None = None


@dataclass(frozen=True, slots=True)
class Judged:
    """One judged minute of one service, and the normals it was judged by."""

    minute: MinuteJudged
    normals: Normals
    # The window's requests per endpoint over the floor, for its reading.
    endpoint_counts: dict[str, tuple[int, int]]
    shadow: bool
    # Each endpoint's run and excursions after this minute.
    endpoint_runs: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def at(self) -> datetime:
        return self.minute.minute

    @property
    def missing(self) -> bool:
        return "no_baseline" in (
            self.minute.slower.verdict,
            self.minute.failing.verdict,
            self.minute.traffic.verdict,
        )


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
        sensitivity, zone = settings.sensitivity, settings.zone
        width = sensitivity.window_minutes
        states = await _states(connection, binding)
        plan = {
            state.service_name: minutes_to_judge(state.last_judged_minute, settled)
            for state in states.values()
        }
        earliest = min((m for minutes in plan.values() for m in minutes), default=settled)
        detection = store.for_project(binding)
        start, end = min(earliest, settled) - (width - 1) * MINUTE, settled + MINUTE
        counted = _with_endpoints(
            detection.window_minutes(start=start, end=end),
            detection.endpoint_minutes(None, start=start, end=end),
        )
        for service in sorted(set(counted) - set(states)):
            await connection.execute(_FIRST_SIGHT, _service_key(binding, service))
            states[service] = ServiceState(service, None)
            plan[service] = [settled]
        plan = {service: minutes for service, minutes in plan.items() if minutes}
        judged_minutes = [m for minutes in plan.values() for m in minutes]
        if not judged_minutes:
            outcome.notified += await _record_run(connection, binding, now=now)
            return outcome
        normals = await _normals(connection, binding, min(judged_minutes), max(judged_minutes))
        judged = {
            service: _judge_service(
                counted.get(service, {}), normals, service, minutes, states[service], sensitivity
            )
            for service, minutes in sorted(plan.items())
        }
        outcome.baselines_missing = any(each.missing for runs in judged.values() for each in runs)
        detection.write(
            [_reading(service, each, width) for service, runs in judged.items() for each in runs]
        )
        signal_states = {
            service: _signal_states(states[service].signals, runs)
            for service, runs in judged.items()
        }
        muted, snoozed = await _muted_and_snoozed(connection, binding, now)
        false_alarms = await _false_alarms(connection, binding, states.values())
        project = await _keep_project_finding(
            connection, binding, states, judged, signal_states, zone=zone, now=now, outcome=outcome
        )
        for service, runs in judged.items():
            state = states[service]
            stored, held_out = await _keep_finding(
                connection,
                detection,
                binding,
                state,
                runs,
                signal_states[service],
                project,
                false_alarms,
                zone=zone,
                muted=service in muted,
                now=now,
                outcome=outcome,
            )
            stored = _record_exclusions(
                stored, runs, snoozed=service in snoozed, held_out=held_out, now=now
            )
            await _advance(connection, binding, state, runs[-1].at, stored)
            outcome.minutes_judged += len(runs)
        outcome.services = len(judged)
        outcome.notified += await _record_run(connection, binding, now=now)
    return outcome


# ------------------------------------------------------------------- judging


def _judge_service(
    history: Mapping[datetime, Minute],
    normals: Mapping[tuple[str, datetime], Normals],
    service: str,
    minutes: Sequence[datetime],
    state: ServiceState,
    sensitivity: Sensitivity,
) -> list[Judged]:
    run = int(state.signals.get(FAILING, {}).get("above_run", 0))
    endpoint_runs = endpoint_records(state.signals)
    found = []
    for minute in minutes:
        each = _judge(
            history,
            normals.get((service, minute), Normals()),
            minute,
            sensitivity,
            run,
            {name: int(record["run"]) for name, record in endpoint_runs.items()},
        )
        run = run + 1 if each.minute.failing.above else 0
        endpoint_runs = advance_endpoint_runs(endpoint_runs, each.minute.failing, minute)
        found.append(replace(each, endpoint_runs=endpoint_runs))
    return found


def endpoint_records(stored: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Each endpoint's current run above the base test, and its finished excursions."""
    return {
        name: {"run": int(record.get("run", 0)), "runs": list(record.get("runs", []))}
        for name, record in stored.get(FAILING, {}).get(ENDPOINTS_KEY, {}).items()
    }


def advance_endpoint_runs(
    records: Mapping[str, Mapping[str, Any]], failing: FailingJudgement, minute: datetime
) -> dict[str, dict[str, Any]]:
    """One minute of each endpoint's record (D18 per endpoint). A run ends when its
    endpoint's window no longer passes the base test, or is not judged; it is then
    kept as its first and last minutes, which the baseline job profiles."""
    above = {each.name for each in failing.endpoints if each.above}
    found: dict[str, dict[str, Any]] = {}
    for name in sorted(set(records) | above):
        record = records.get(name, {"run": 0, "runs": []})
        run, runs = int(record["run"]), list(record["runs"])
        if name in above:
            run += 1
        elif run:
            runs.append([_iso(minute - run * MINUTE), _iso(minute - MINUTE)])
            run = 0
        found[name] = {"run": run, "runs": runs}
    return found


def _forget_old_runs(
    records: Mapping[str, Mapping[str, Any]], now: datetime
) -> dict[str, dict[str, Any]]:
    """Excursions no 28-day profile reaches any more are dropped, and with them an
    endpoint that has nothing left to say."""
    horizon = now - HISTORY
    found = {}
    for name, record in records.items():
        runs = [run for run in record["runs"] if datetime.fromisoformat(run[1]) >= horizon]
        if record["run"] or runs:
            found[name] = {"run": record["run"], "runs": runs}
    return found


def _judge(
    history: Mapping[datetime, Minute],
    normals: Normals,
    minute: datetime,
    sensitivity: Sensitivity,
    run_before: int,
    endpoint_runs: Mapping[str, int],
) -> Judged:
    width = sensitivity.window_minutes
    window = Window(
        tuple(
            history.get(minute - back * MINUTE, Minute.empty()) for back in range(width - 1, -1, -1)
        )
    )
    failing = judge_failing(
        window, normals.failing, sensitivity, run_before=run_before, endpoint_runs=endpoint_runs
    )
    traffic = judge_traffic(window, normals.traffic, sensitivity)
    slower = judge_slower(window, normals.slower, sensitivity)
    return Judged(
        minute=MinuteJudged(
            minute=minute,
            width=width,
            requests=window.minutes[-1].requests,
            traffic=traffic,
            failing=failing,
            slower=slower,
            traffic_normal=_normal_of(normals.traffic),
            failing_normal=_normal_of(normals.failing),
            slower_normal=_normal_of(normals.slower),
            slower_normal_ms=None if normals.slower is None else normals.slower.normal_p95_ms,
        ),
        normals=normals,
        endpoint_counts={e.name: (e.requests, e.failures) for e in failing.endpoints if e.judged},
        shadow=traffic.shadow,
    )


def _normal_of(
    baseline: SlowerBaseline | FailingBaseline | TrafficBaseline | None,
) -> Normal | None:
    if baseline is None or baseline.mode == "learning":
        return None
    return Normal(baseline.mode, baseline.days)


def _signal_states(stored: Mapping[str, Any], runs: Sequence[Judged]) -> dict[str, dict[str, Any]]:
    """Each signal's phase after these minutes: what the API shows (FR25), its count of
    clear minutes, and for traffic when its run of unusual minutes began (FR15)."""
    found = {signal: dict(stored.get(signal, {})) for signal in (TRAFFIC, FAILING, SLOWER)}
    for each in runs:
        judged = each.minute
        for signal, status in (
            (TRAFFIC, judged.status(TRAFFIC)),
            (FAILING, judged.status(FAILING)),
            (SLOWER, judged.status(SLOWER)),
        ):
            phase = found[signal]
            clear = int(phase.get("clear_minutes", 0))
            if status == "clear":
                clear += 1
            elif status in ("unusual", "held"):
                clear = 0
            phase["clear_minutes"] = clear
        traffic = found[TRAFFIC]
        if judged.traffic.verdict == "unusual":
            traffic.setdefault("unusual_since", judged.onset(TRAFFIC).isoformat())
        else:
            traffic.pop("unusual_since", None)
        found[FAILING]["above_run"] = (
            int(found[FAILING].get("above_run", 0)) + 1 if judged.failing.above else 0
        )
    endpoints = _forget_old_runs(runs[-1].endpoint_runs, runs[-1].at)
    if endpoints:
        found[FAILING][ENDPOINTS_KEY] = endpoints
    else:
        found[FAILING].pop(ENDPOINTS_KEY, None)
    last = runs[-1]
    for signal, verdict, baseline in (
        (TRAFFIC, last.minute.traffic.verdict, last.normals.traffic),
        (FAILING, last.minute.failing.verdict, last.normals.failing),
        (SLOWER, last.minute.slower.verdict, last.normals.slower),
    ):
        until = None if baseline is None else baseline.learning_until
        found[signal].update(
            verdict=verdict, learning_until=None if until is None else until.isoformat()
        )
    return found


# ------------------------------------------------------------------ findings


@dataclass(frozen=True, slots=True)
class ProjectContext:
    """What the project's finding says to each service this run (FR15)."""

    absorbed: frozenset[str] = frozenset()
    pending: int = 0
    finding_id: uuid.UUID | None = None


async def _keep_project_finding(
    connection: AsyncConnection,
    binding: ProjectBinding,
    states: Mapping[str, ServiceState],
    judged: Mapping[str, Sequence[Judged]],
    signal_states: Mapping[str, Mapping[str, Mapping[str, Any]]],
    *,
    zone: ZoneInfo,
    now: datetime,
    outcome: Outcome,
) -> ProjectContext:
    """Open, keep or close the project's one finding from each service's latest
    traffic (FR15), and say which services its traffic holds."""
    key = _project_key(binding)
    row = (await connection.execute(_OPEN_PROJECT_FINDING, key)).one_or_none()
    finding_id = None if row is None else row.id
    involved = (
        None
        if row is None
        else frozenset(
            name
            for name, state in states.items()
            if state.signals.get(TRAFFIC, {}).get("project_finding") == str(row.id)
        )
    )
    minute = max(runs[-1].at for runs in judged.values())
    snapshots = {
        service: _snapshot(runs[-1], states[service], signal_states[service])
        for service, runs in judged.items()
    }
    decision = project_rule(snapshots, involved=involved, minute=minute)
    members = [snapshots[name] for name in decision.involved if name in snapshots]
    signal = _project_signal(members, None if row is None else row.signals)
    if decision.open:
        started = min(
            datetime.fromisoformat(signal_states[name][TRAFFIC]["unusual_since"])
            for name in decision.involved
        )
        finding_id = (
            await connection.execute(
                _OPEN,
                {
                    **key,
                    "scope": "project",
                    "service_name": None,
                    "severity": "critical",
                    "muted": False,
                    "title": project_title(len(decision.involved), decision.judged, started, zone),
                    "signals": json.dumps([signal]),
                    "services": json.dumps(sorted(decision.involved)),
                    "started_at": started,
                },
            )
        ).scalar_one()
        outcome.opened += 1
        outcome.notified += await notify.finding_opened(
            connection,
            finding_id,
            org_id=key["org_id"],
            severity="critical",
            services=sorted(decision.involved),
            at=now,
        )
    elif decision.close is not None and row is not None:
        await connection.execute(
            _CLOSE,
            {
                "org_id": key["org_id"],
                "id": row.id,
                "reason": decision.close,
                "closed_at": max(minute, row.started_at),
                "signals": json.dumps([signal]),
            },
        )
        outcome.closed += 1
        outcome.notified += await notify.finding_closed(
            connection, row.id, org_id=key["org_id"], severity="critical", at=now
        )
        return ProjectContext(pending=decision.pending)
    elif row is not None:
        await connection.execute(
            _UPDATE,
            {
                "org_id": key["org_id"],
                "id": row.id,
                "severity": "critical",
                "title": project_title(
                    len(decision.involved), decision.judged, row.started_at, zone
                ),
                "signals": json.dumps([signal]),
                "services": json.dumps(sorted(decision.involved)),
            },
        )
    if finding_id is None:
        return ProjectContext(pending=decision.pending)
    return ProjectContext(
        absorbed=decision.involved, pending=decision.pending, finding_id=finding_id
    )


def _snapshot(
    last: Judged, state: ServiceState, phases: Mapping[str, Mapping[str, Any]]
) -> TrafficSnapshot:
    traffic = last.minute.traffic
    since = phases[TRAFFIC].get("unusual_since")
    own = state.signals.get(FINDING_KEY, {}).get("keys", [])
    width = last.minute.width
    return TrafficSnapshot(
        judged=traffic.judged,
        unusual=traffic.verdict == "unusual",
        unusual_since=None if since is None else datetime.fromisoformat(since),
        clear_minutes=int(phases[TRAFFIC].get("clear_minutes", 0)),
        in_own_finding=any(entry[0] == TRAFFIC for entry in own),
        last_request=_last_request(state.signals, [last]),
        expected_rpm=None if traffic.expected is None else traffic.expected / width,
        current_rpm=traffic.window_requests / width,
    )


def _project_signal(
    members: Sequence[TrafficSnapshot], stored: Sequence[Mapping[str, Any]] | None
) -> FindingSignal:
    """The project finding's *losing traffic* entry: its services' requests a minute."""
    normal = sum(each.expected_rpm or 0.0 for each in members) if members else None
    current = sum(each.current_rpm or 0.0 for each in members) if members else None
    worst = current
    if stored:
        before = stored[0].get("worst")
        if before is not None and (worst is None or before < worst):
            worst = before
    return finding_signal(
        "traffic", normal=normal, current=current, worst=worst, comparison=PROJECT_COMPARISON
    )


async def _keep_finding(
    connection: AsyncConnection,
    detection: ProjectDetection,
    binding: ProjectBinding,
    state: ServiceState,
    runs: Sequence[Judged],
    phases: dict[str, dict[str, Any]],
    project: ProjectContext,
    false_alarms: Mapping[str, bool],
    *,
    zone: ZoneInfo,
    muted: bool,
    now: datetime,
    outcome: Outcome,
) -> tuple[dict[str, Any], list[datetime]]:
    """Open, raise, join, adopt or close the service's one finding over these minutes,
    in order. Returns the service's state to store, and the minutes a member's false
    alarm keeps out of the normal (FR12 to FR14, FR20, FR21, FR32, D20, D28)."""
    service = state.service_name
    key = _service_key(binding, service)
    row = (await connection.execute(_OPEN_FINDING, key)).one_or_none()
    finding_id = None if row is None else row.id
    tracked = state.signals.get(FINDING_KEY, {})
    finding = None if row is None else _restore(row, tracked)
    since = None if row is None or finding is None else _since(tracked, row.id, finding)
    holds, keep_out = _holds(state, row is None, project, false_alarms)
    kept_out: list[datetime] = []
    traffic = phases[TRAFFIC]
    absorbed = service in project.absorbed
    if project.finding_id is not None and absorbed:
        traffic["project_finding"] = str(project.finding_id)
    else:
        traffic.pop("project_finding", None)
    last_request = _last_request(state.signals, [])
    for each in runs:
        judged = each.minute
        if judged.requests > 0:
            last_request = judged.minute
        waits = False
        holds_traffic = finding is not None and "traffic" in finding.keys
        if judged.traffic.verdict == "unusual" and not absorbed and not holds_traffic:
            since_pending = traffic.setdefault("pending_since", _iso(judged.minute))
            waits = traffic_waits(
                datetime.fromisoformat(since_pending), judged.minute, project.pending
            )
        else:
            traffic.pop("pending_since", None)
        context = Context(
            traffic_absorbed=absorbed,
            traffic_waits=waits,
            last_request=last_request,
            held=frozenset(holds),
        )
        if keep_out and holds:
            kept_out.append(judged.minute)
        finding, change = step(finding, judged, context, zone=zone)
        holds = advance_holds(holds, judged)
        if finding is None:
            continue
        if change.opened:
            finding.muted = muted
            finding_id = await _open(connection, key, service, finding, zone)
            since = finding.started_at
            outcome.opened += 1
            outcome.notified += await notify.finding_opened(
                connection, finding_id, org_id=key["org_id"], severity=finding.severity, at=now
            )
        else:
            assert finding_id is not None, "a finding the lifecycle kept open has a row"
            outcome.notified += await _notify_change(
                connection, key, finding_id, finding, change, now=now
            )
        if change.joined:
            since = judged.minute
        if change.closed is not None:
            assert finding_id is not None, "only an open finding closes"
            await connection.execute(
                _CLOSE,
                {
                    "org_id": key["org_id"],
                    "id": finding_id,
                    "reason": change.closed,
                    "closed_at": max(judged.minute, finding.started_at),
                    "signals": json.dumps(finding.signals()),
                },
            )
            outcome.closed += 1
            outcome.notified += await notify.finding_closed(
                connection, finding_id, org_id=key["org_id"], severity=finding.severity, at=now
            )
            finding, finding_id = None, None
        if change.joined:
            traffic.pop("pending_since", None)
    if (
        finding is not None
        and finding_id is not None
        and since is not None
        and await _adopt(
            connection,
            detection,
            key,
            finding_id,
            finding,
            since=since,
            minute=runs[-1].at,
            now=now,
            outcome=outcome,
        )
    ):
        finding, finding_id = None, None
    if finding_id is not None:
        # Before the commit, so a note on the incident's PagerDuty alert (D29) can
        # use the link as soon as the opening is delivered.
        await _link_incidents(connection, key, finding_id)
    if finding is not None and finding_id is not None and finding.dirty:
        await connection.execute(
            _UPDATE,
            {
                "org_id": key["org_id"],
                "id": finding_id,
                "severity": finding.severity,
                "title": title(service, finding, zone),
                "signals": json.dumps(finding.signals()),
                "services": "[]",
            },
        )
    stored: dict[str, Any] = {**state.signals, **phases}
    stored[LAST_REQUEST_KEY] = None if last_request is None else _iso(last_request)
    if finding is None or finding_id is None:
        stored.pop(FINDING_KEY, None)
    else:
        stored[FINDING_KEY] = {
            "id": str(finding_id),
            "keys": [
                [name, finding.keys[name].clear, finding.keys[name].unjudged]
                for name in finding.ordered()
            ],
            SINCE_KEY: _iso(since or finding.started_at),
        }
    if holds:
        stored[HELD_KEY], stored[HELD_OUT_KEY] = holds, keep_out
    else:
        stored.pop(HELD_KEY, None)
        stored.pop(HELD_OUT_KEY, None)
    return stored, kept_out


def _since(tracked: Mapping[str, Any], finding_id: uuid.UUID, finding: OpenFinding) -> datetime:
    """When the open finding's keys last changed: recorded by this job, or its start
    for a finding recorded before that was (AC16)."""
    recorded = tracked.get(SINCE_KEY) if tracked.get("id") == str(finding_id) else None
    return finding.started_at if recorded is None else datetime.fromisoformat(recorded)


def _holds(
    state: ServiceState,
    finding_gone: bool,
    project: ProjectContext,
    false_alarms: Mapping[str, bool],
) -> tuple[dict[str, int], bool]:
    """The keys a false alarm holds (FR20), and whether their minutes stay out of the
    normal: they do for a member's, whose word cannot widen it (D28). A finding this
    job still tracked, and a person has closed as a false alarm since, starts a hold
    on its keys; a project finding's starts one on each of its services' traffic."""
    holds = {str(name): int(clear) for name, clear in state.signals.get(HELD_KEY, {}).items()}
    keep_out = bool(state.signals.get(HELD_OUT_KEY, False))
    tracked = state.signals.get(FINDING_KEY, {})
    returns = false_alarms.get(str(tracked.get("id"))) if finding_gone else None
    if returns is not None:
        holds.update({str(entry[0]): 0 for entry in tracked.get("keys", [])})
        keep_out = keep_out or not returns
    held_project = state.signals.get(TRAFFIC, {}).get("project_finding")
    returns = None if held_project is None else false_alarms.get(held_project)
    if returns is not None and held_project != str(project.finding_id):
        holds[TRAFFIC] = 0
        keep_out = keep_out or not returns
    return holds, keep_out


async def _adopt(
    connection: AsyncConnection,
    detection: ProjectDetection,
    key: Mapping[str, Any],
    finding_id: uuid.UUID,
    finding: OpenFinding,
    *,
    since: datetime,
    minute: datetime,
    now: datetime,
    outcome: Outcome,
) -> bool:
    """FR21, AC16: a finding open a week with the same signals, nobody having answered,
    becomes its service's normal, unless D22 or FR32 says it never may. Its ratio is
    taken from its readings, and it waits until enough of them say what the level is.
    The owners are told (D22), unless it is muted (D36); the system is the actor."""
    if not adoptable(finding, since=since, minute=minute, after=ADOPTION_AFTER):
        return False
    started = finding.started_at.replace(second=0, microsecond=0)
    readings = detection.shift_readings(key["service_name"], start=started, end=minute + MINUTE)
    signals = list(dict.fromkeys(key_signal(name) for name in finding.ordered()))
    ratios = {signal: shift_ratio(signal, readings) for signal in signals}
    if any(ratio is None for ratio in ratios.values()):
        return False
    shifts = [{"signal": signal, "ratio": ratio} for signal, ratio in ratios.items()]
    for shift in shifts:
        await connection.execute(_ADOPTED_SHIFT, {**key, **shift, "from_minute": started})
    await connection.execute(
        _CLOSE,
        {
            "org_id": key["org_id"],
            "id": finding_id,
            "reason": "adopted",
            "closed_at": max(minute, finding.started_at),
            "signals": json.dumps(finding.signals()),
        },
    )
    await connection.execute(
        _AUDIT_ADOPTED,
        {
            "org_id": key["org_id"],
            "action": ADOPTED_AUDIT_ACTION,
            "target": f"{FINDING_AUDIT_TARGET}:{finding_id}",
            "detail": json.dumps({"shifts": shifts}),
        },
    )
    outcome.closed += 1
    outcome.notified += await notify.finding_adopted(
        connection, finding_id, org_id=key["org_id"], severity=finding.severity, at=now
    )
    # The rebuilt normal is asked for by the next run, which finds this one stale.
    return True


async def _notify_change(
    connection: AsyncConnection,
    key: Mapping[str, Any],
    finding_id: uuid.UUID,
    finding: OpenFinding,
    change: Change,
    *,
    now: datetime,
) -> int:
    """What a minute did to an open finding, said once each (FR28, D29)."""
    written = 0
    if change.raised:
        written += await notify.finding_escalated(
            connection, finding_id, org_id=key["org_id"], at=now
        )
    if change.joined:
        written += await notify.finding_joined(
            connection,
            finding_id,
            org_id=key["org_id"],
            severity=finding.severity,
            keys=len(finding.keys),
            at=now,
        )
    return written


async def _link_incidents(
    connection: AsyncConnection, key: Mapping[str, Any], finding_id: uuid.UUID
) -> None:
    await connection.execute(
        _LINK_INCIDENTS,
        {
            "org_id": key["org_id"],
            "project_id": key["project_id"],
            "finding_id": finding_id,
            "subject_key": service_subject(key["service_name"]),
            "kinds": sorted(SERVICE_KINDS),
        },
    )


async def _open(
    connection: AsyncConnection,
    key: Mapping[str, Any],
    service: str,
    finding: OpenFinding,
    zone: ZoneInfo,
) -> uuid.UUID:
    finding_id: uuid.UUID = (
        await connection.execute(
            _OPEN,
            {
                **key,
                "scope": "service",
                "severity": finding.severity,
                "muted": finding.muted,
                "title": title(service, finding, zone),
                "signals": json.dumps(finding.signals()),
                "services": "[]",
                "started_at": finding.started_at,
            },
        )
    ).scalar_one()
    finding.dirty = False
    return finding_id


def _restore(row: Any, tracked: Mapping[str, Any]) -> OpenFinding:
    """An open finding from its row and the counts its service's state keeps. A row
    whose keys the state does not hold (closed and reopened elsewhere) is read by
    signal: its first *failing* entry is the service's, the rest its endpoints'."""
    entries: list[Mapping[str, Any]] = list(row.signals or [])
    counts = tracked.get("keys", []) if tracked.get("id") == str(row.id) else []
    if len(counts) == len(entries):
        names = [str(entry[0]) for entry in counts]
    else:
        names, counts, seen_failing = [], [], False
        for entry in entries:
            signal = entry.get("signal")
            if signal == "failing" and seen_failing and entry.get("endpoint"):
                names.append(endpoint_key(entry["endpoint"]))
            else:
                names.append(str(signal))
                seen_failing = seen_failing or signal == "failing"
    keys = {}
    for position, (name, entry) in enumerate(zip(names, entries, strict=True)):
        clear, unjudged = (int(counts[position][1]), int(counts[position][2])) if counts else (0, 0)
        normal, worst = entry.get("normal"), entry.get("worst")
        keys[name] = KeyRecord(
            normal=normal,
            current=entry.get("current"),
            worst=worst,
            comparison=entry.get("comparison", ""),
            endpoint=entry.get("endpoint"),
            clear=clear,
            unjudged=unjudged,
            weight=weight(name, normal, worst),
        )
    return OpenFinding(
        severity=row.severity, started_at=row.started_at, keys=keys, muted=bool(row.muted)
    )


def _last_request(stored: Mapping[str, Any], runs: Sequence[Judged]) -> datetime | None:
    found = stored.get(LAST_REQUEST_KEY)
    last = None if found is None else datetime.fromisoformat(found)
    for each in runs:
        if each.minute.requests > 0:
            last = each.at
    return last


def _record_exclusions(
    stored: dict[str, Any],
    runs: Sequence[Judged],
    *,
    snoozed: bool,
    now: datetime,
    held_out: Sequence[datetime] = (),
) -> dict[str, Any]:
    """Add this run's snoozed, shadowed and member-held minutes to what the normal
    leaves out, and forget what no pool reaches any more (BR3, FR6, D25, D28)."""
    recorded = {
        reason: [
            (datetime.fromisoformat(first), datetime.fromisoformat(last)) for first, last in spans
        ]
        for reason, spans in stored.get(EXCLUDED_KEY, {}).items()
    }
    for each in runs:
        if snoozed:
            recorded.setdefault(SNOOZED, []).append((each.at, each.at))
        if each.shadow:
            lead = (each.minute.width - 1) * MINUTE
            recorded.setdefault(SHADOW, []).append((each.at - lead, each.at))
    for at in held_out:
        recorded.setdefault(HELD_OUT, []).append((at, at))
    horizon = now - HISTORY
    kept = {
        reason: [[_iso(a), _iso(b)] for a, b in merge_ranges(spans) if b >= horizon]
        for reason, spans in recorded.items()
    }
    kept = {reason: spans for reason, spans in kept.items() if spans}
    if kept:
        return {**stored, EXCLUDED_KEY: kept}
    return {key: value for key, value in stored.items() if key != EXCLUDED_KEY}


# ------------------------------------------------------------------- stores


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _service_key(binding: ProjectBinding, service: str) -> dict[str, Any]:
    return {
        "org_id": uuid.UUID(binding.org_id),
        "project_id": uuid.UUID(binding.project_id),
        "service_name": service,
    }


def _project_key(binding: ProjectBinding) -> dict[str, Any]:
    return {"org_id": uuid.UUID(binding.org_id), "project_id": uuid.UUID(binding.project_id)}


def _with_endpoints(
    minutes: Mapping[str, Mapping[datetime, Minute]],
    endpoints: Mapping[str, Mapping[datetime, Mapping[str, tuple[int, int]]]],
) -> dict[str, dict[datetime, Minute]]:
    return {
        service: {
            minute: replace(each, endpoints=dict(endpoints.get(service, {}).get(minute, {})))
            for minute, each in counted.items()
        }
        for service, counted in minutes.items()
    }


async def _states(connection: AsyncConnection, binding: ProjectBinding) -> dict[str, ServiceState]:
    rows = await connection.execute(_STATES, _project_key(binding))
    return {
        row.service_name: ServiceState(row.service_name, row.last_judged_minute, row.signals or {})
        for row in rows
    }


async def _normals(
    connection: AsyncConnection, binding: ProjectBinding, first: datetime, last: datetime
) -> dict[tuple[str, datetime], Normals]:
    rows = await connection.execute(
        _BASELINES, {**_project_key(binding), "first": first, "last": last}
    )
    found: dict[tuple[str, datetime], Normals] = {}
    for row in rows:
        at = (row.service_name, row.minute.astimezone(UTC))
        known = found.get(at, Normals())
        if row.signal == SLOWER:
            found[at] = replace(known, slower=slower_from_json(row.baseline))
        elif row.signal == FAILING:
            found[at] = replace(known, failing=failing_from_json(row.baseline))
        elif row.signal == TRAFFIC:
            found[at] = replace(known, traffic=traffic_from_json(row.baseline))
    return found


async def _false_alarms(
    connection: AsyncConnection, binding: ProjectBinding, states: Iterable[ServiceState]
) -> dict[str, bool]:
    """Of the findings the services' states still name, those a person closed as a
    false alarm, each with whether it returned its minutes to the normal."""
    named = set()
    for each in states:
        tracked = each.signals.get(FINDING_KEY, {}).get("id")
        held_project = each.signals.get(TRAFFIC, {}).get("project_finding")
        named |= {found for found in (tracked, held_project) if found is not None}
    if not named:
        return {}
    rows = await connection.execute(_FALSE_ALARMS, {**_project_key(binding), "ids": sorted(named)})
    return {str(row.id): bool(row.false_alarm_returns_minutes) for row in rows}


async def _muted_and_snoozed(
    connection: AsyncConnection, binding: ProjectBinding, now: datetime
) -> tuple[set[str], set[str]]:
    rows = list(await connection.execute(_SERVICE_SETTINGS, _project_key(binding)))
    muted = {row.service_name for row in rows if row.muted_until and row.muted_until > now}
    snoozed = {row.service_name for row in rows if row.snoozed_until and row.snoozed_until > now}
    return muted, snoozed


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


async def _record_run(
    connection: AsyncConnection, binding: ProjectBinding, *, now: datetime
) -> int:
    """The run completed. Returns the notifications written: one *running again* to
    the owners if they had been told it stopped (D24)."""
    key = _project_key(binding)
    reported = (await connection.execute(_STOP_REPORTED, key)).scalar_one_or_none()
    await connection.execute(_COMPLETED, key)
    if reported is None:
        return 0
    return await notify.running_again(
        connection, org_id=key["org_id"], project_id=key["project_id"], at=now
    )


def _reading(service: str, each: Judged, width: int) -> Reading:
    judged, normals = each.minute, each.normals
    slower, failing, traffic = judged.slower, judged.failing, judged.traffic
    slower_normal, failing_normal, traffic_normal = normals.slower, normals.failing, normals.traffic
    profile = None if failing_normal is None else failing_normal.profile
    height = None if profile is None else profile.height(failing.window_requests)
    judged_failing = failing_normal is not None and failing_normal.mode != "learning"
    return Reading(
        service_name=service,
        minute=judged.minute,
        window_minutes=width,
        window_requests=failing.window_requests,
        window_errors=failing.window_failures,
        window_p95_ms=slower.window_p95_ms,
        window_above_threshold=slower.above_threshold,
        endpoint_failures=each.endpoint_counts,
        traffic_mode=NO_MODE if traffic_normal is None else traffic_normal.mode,
        traffic_days=0 if traffic_normal is None else traffic_normal.days,
        traffic_median=None if traffic_normal is None else traffic_normal.median,
        traffic_spread=None if traffic_normal is None else traffic_normal.spread,
        traffic_verdict=traffic.verdict,
        failing_mode=NO_MODE if failing_normal is None else failing_normal.mode,
        failing_days=0 if failing_normal is None else failing_normal.days,
        # The rate and wander the minute was judged by: the burst profile rebuilds
        # whether this window passed the base test from them (D17).
        failing_median=failing.normal_rate if judged_failing else None,
        failing_spread=failing.wander if judged_failing else None,
        failing_burst_height=None if height is None or height == float("-inf") else height,
        failing_burst_minutes=0 if profile is None else profile.minutes,
        failing_verdict=failing.verdict,
        slower_mode=NO_MODE if slower_normal is None else slower_normal.mode,
        slower_days=0 if slower_normal is None else slower_normal.days,
        slower_median=None if slower_normal is None else slower_normal.normal_p95_ms,
        slower_spread=None if slower_normal is None else slower_normal.wander,
        slower_threshold_ms=None if slower_normal is None else slower_normal.threshold_ms,
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
        notified=outcome.notified,
    )
    if outcome.baselines_missing and outcome.org_id is not None:
        enqueue(compute_baselines_request(project, uuid.UUID(outcome.org_id)))
    if outcome.notified:
        enqueue(JobRequest(task=DELIVER_TASK, queue=DELIVERIES_QUEUE))
