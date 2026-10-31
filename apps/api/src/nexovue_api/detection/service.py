"""Detection's state for a project's headline and for one service (F6, FR25).

Two facts decide it, both written by the scheduler:

- **Whether detection runs** is the project job's last completed run
  (`detection_project_runs`), never `last_judged_minute`, which trails the clock
  by at least 3 minutes by design (design 2.1, item 2). It is judged only while
  the scheduler expects the project judged (`expected_since`, kept by its stop
  sweep): one with traffic in the last 24 hours or a finding open. A quiet
  project's old run is not a stopped detector. The 3 minutes count from the later
  of the last run and `expected_since`, the same clock as the owners' 15-minute
  email (D24, D34): a project never judged is *not running* once it has had its
  chance, and one that has just started sending is not.
- **Each signal's phase** is `detection_state.signals`, keyed by signal, whose
  `verdict` and `learning_until` the project job writes after each run.

- **Whether data is delayed** is derived: the run is fresh, but judging has not
  moved past an old minute, because the pipeline has not settled a newer one
  (BR5). A pipeline that settles nothing at all queues no job, and reads *not
  running*.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, get_args

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import DetectionState, ServiceDetectionSettings
from nexovue_api.detection import queries
from nexovue_api.detection.schemas import (
    FindingMarker,
    ProjectDetectionState,
    ProjectDetectionStatus,
    ServiceDetection,
    ServiceDetectionState,
    SignalStatus,
)
from nexovue_api.findings.schemas import SignalName

# Older than this, the project's job is not running (FR25, AC26).
NOT_RUNNING_AFTER: Final = timedelta(minutes=3)
# A judged minute trails now by the 3-minute settling lag; 3 more is a stalled pipeline.
DELAYED_AFTER: Final = timedelta(minutes=6)
# The normal settling lag: data counts as delayed from when its newest minute settled.
SETTLING_LAG: Final = timedelta(minutes=3)

# Leading signal first, as a finding lists them.
_SIGNAL_ORDER: Final[tuple[SignalName, ...]] = get_args(SignalName)

# The scheduler's verdicts (`detection/method.py`, `Verdict`) and what each tells a reader.
_JUDGED_VERDICTS: Final = frozenset({"unusual", "elevated", "normal"})
_LEARNING: Final = "learning"
_TOO_LITTLE_TRAFFIC: Final = "too_little_traffic"
_NO_BASELINE: Final = "no_baseline"
_PAST_LEARNING: Final = frozenset({"judged", "too_little_traffic"})


@dataclass(frozen=True, slots=True)
class _Running:
    """Whether the project's job runs, since when it has not (None when it never
    ran), and whether its last run is fresh: only a fresh run can tell delayed data
    from a stopped job."""

    running: bool
    stopped_since: datetime | None
    fresh: bool

    def delayed(self, judged: datetime | None, now: datetime) -> bool:
        """Whether judging stands at a minute the pipeline should long have passed."""
        return self.fresh and judged is not None and now - judged > DELAYED_AFTER


async def project_status(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, now: datetime
) -> ProjectDetectionStatus:
    running = await _running(session, org_id=org_id, project_id=project_id, now=now)
    rows = await queries.service_states(session, org_id=org_id, project_id=project_id)
    learning_dates: list[date] = []
    judged = 0
    for row in rows:
        signals = signal_statuses(row.signals)
        if service_is_judged(row, signals):
            judged += 1
        learning_dates.extend(s.learning_until for s in signals if s.learning_until is not None)
    newest = max((r.last_judged_minute for r in rows if r.last_judged_minute), default=None)
    delayed = running.delayed(newest, now)

    if not running.running:
        state: ProjectDetectionState = "not_running"
    elif delayed:
        state = "data_delayed"
    elif not rows:
        state = "no_services"
    elif judged == 0:
        state = "learning"
    else:
        state = "judging"
    return ProjectDetectionStatus(
        state=state,
        services_total=len(rows),
        services_learning=len(rows) - judged,
        services_judged=judged,
        services_delayed=sum(1 for r in rows if running.delayed(r.last_judged_minute, now)),
        services_muted=await queries.muted_service_count(
            session, org_id=org_id, project_id=project_id, now=now
        ),
        earliest_judging_date=min(learning_dates, default=None),
        not_running_since=running.stopped_since,
        delayed_since=newest + SETTLING_LAG if delayed and newest is not None else None,
        open_findings=await queries.open_finding_count(
            session, org_id=org_id, project_id=project_id
        ),
    )


async def service_detection(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service_name: str,
    start: datetime,
    end: datetime,
    now: datetime,
) -> ServiceDetection:
    """One service's state, each signal's phase, and its findings in `[start, end]`."""
    running = await _running(session, org_id=org_id, project_id=project_id, now=now)
    state_row = await queries.service_state(
        session, org_id=org_id, project_id=project_id, service_name=service_name
    )
    settings = await queries.service_settings(
        session, org_id=org_id, project_id=project_id, service_name=service_name
    )
    rows = await queries.service_findings(
        session,
        org_id=org_id,
        project_id=project_id,
        service_name=service_name,
        start=start,
        end=end,
        limit=queries.MARKER_PAGE + 1,
    )
    signals = signal_statuses({} if state_row is None else state_row.signals)
    muted_until = _still_ahead(None if settings is None else settings.muted_until, now)
    return ServiceDetection(
        state=service_state(
            running=running.running,
            delayed=running.delayed(
                None if state_row is None else state_row.last_judged_minute, now
            ),
            row=state_row,
            signals=signals,
            muted=muted_until is not None,
        ),
        signals=signals,
        muted_until=muted_until,
        snoozed_until=_still_ahead(_snoozed(settings), now),
        findings=[
            FindingMarker(
                id=row.id, started_at=row.started_at, closed_at=row.closed_at, severity=row.severity
            )
            for row in rows[: queries.MARKER_PAGE]
        ],
        truncated=len(rows) > queries.MARKER_PAGE,
    )


# -------------------------------------------------------------- pure mapping


def signal_statuses(stored: Mapping[str, Any]) -> list[SignalStatus]:
    """Each signal's phase from `detection_state.signals`, leading signal first.

    A signal the scheduler records nothing for is *not judged*, never *judged*.
    """
    return [_signal_status(signal, stored.get(signal)) for signal in _SIGNAL_ORDER]


def _signal_status(signal: SignalName, stored: Mapping[str, Any] | None) -> SignalStatus:
    if stored is None:
        return SignalStatus(
            signal=signal, state="not_judged", learning_until=None, reason="not_recorded"
        )
    verdict = stored.get("verdict")
    if verdict in _JUDGED_VERDICTS:
        return SignalStatus(signal=signal, state="judged", learning_until=None, reason=None)
    if verdict == _LEARNING:
        until = stored.get("learning_until")
        return SignalStatus(
            signal=signal,
            state="learning",
            learning_until=None if until is None else date.fromisoformat(until),
            reason=None,
        )
    if verdict == _TOO_LITTLE_TRAFFIC:
        return SignalStatus(
            signal=signal, state="too_little_traffic", learning_until=None, reason=None
        )
    if verdict == _NO_BASELINE:
        return SignalStatus(
            signal=signal, state="not_judged", learning_until=None, reason="no_baseline"
        )
    # A verdict this API does not know is a writer's defect: say so, never guess.
    raise ValueError(f"detection_state holds an unknown verdict for {signal}: {verdict!r}")


def service_is_judged(row: DetectionState | None, signals: Iterable[SignalStatus]) -> bool:
    """Past learning: a signal was judged, or was too quiet to judge. A service
    waiting for its first normal, or never judged a minute, is still learning."""
    if row is None or row.last_judged_minute is None:
        return False
    return any(each.state in _PAST_LEARNING for each in signals)


def service_state(
    *,
    running: bool,
    delayed: bool,
    row: DetectionState | None,
    signals: Iterable[SignalStatus],
    muted: bool,
) -> ServiceDetectionState:
    """The badge's one word: a stopped detector, then delayed data, outrank the rest."""
    if not running:
        return "not_running"
    if delayed:
        return "data_delayed"
    if muted:
        return "muted"
    if row is None or row.last_judged_minute is None:
        return "no_detection_yet"
    return "judged" if service_is_judged(row, signals) else "learning"


def _still_ahead(moment: datetime | None, now: datetime) -> datetime | None:
    return moment if moment is not None and moment > now else None


def _snoozed(settings: ServiceDetectionSettings | None) -> datetime | None:
    return None if settings is None else settings.snoozed_until


async def _running(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, now: datetime
) -> _Running:
    run = await queries.project_run(session, org_id=org_id, project_id=project_id)
    completed = None if run is None else run.last_completed_at
    expected = None if run is None else run.expected_since
    fresh = completed is not None and now - completed <= NOT_RUNNING_AFTER
    if expected is None or fresh or now - expected <= NOT_RUNNING_AFTER:
        return _Running(running=True, stopped_since=None, fresh=fresh)
    return _Running(running=False, stopped_since=completed, fresh=False)
