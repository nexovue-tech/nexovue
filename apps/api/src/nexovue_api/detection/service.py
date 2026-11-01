"""Detection's state for a project's headline and for one service (F6, FR25), and
the user's controls: a service's snooze and mute (FR22), a project's sensitivity
and time zone (FR18, FR24).

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
- **Too many services**: a project judges at most `MAX_JUDGED_SERVICES`, its
  busiest. The job marks the rest in their state, and their old judged minute is
  the cap's doing, never delayed data.
- **No incoming requests** (design, *Inputs*): a judged service whose last window
  had no requests, against a normal that expects too few to judge one empty.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, get_args

from nexovue_platform.detection import (
    MAX_JUDGED_SERVICES,
    MAX_MUTE,
    MAX_SNOOZE,
    NOT_RUNNING_AFTER,
    TOO_MANY_SERVICES_KEY,
    WINDOW_REQUESTS_KEY,
)
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import DetectionState, Project, ServiceDetectionSettings, User
from nexovue_api.detection import queries
from nexovue_api.detection.schemas import (
    DetectionSettings,
    DetectionSettingsWrite,
    FindingMarker,
    ProjectDetectionState,
    ProjectDetectionStatus,
    ServiceControls,
    ServiceDetection,
    ServiceDetectionState,
    SignalStatus,
)
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
from nexovue_api.findings import queries as findings_queries
from nexovue_api.findings.schemas import SignalName
from nexovue_api.profile.timezones import stored_timezone
from nexovue_api.projects import queries as projects_queries
from nexovue_api.projects.service import NO_SUCH_PROJECT, require_project

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
# Traffic verdicts that mean its normal expects too few for an empty window to be a
# stop: before 3 weeks only a stop is judged, so a quiet one reads *learning* (FR6).
_EXPECTS_TOO_FEW: Final = frozenset({_TOO_LITTLE_TRAFFIC, _LEARNING})

NO_SUCH_SERVICE: Final = "Detection has not seen a service by that name in this project."
NOT_AHEAD: Final = "Choose a time still to come, or end it now."
SNOOZE_TOO_LONG: Final = "A snooze lasts at most 4 hours. An admin can mute the service for longer."
MUTE_TOO_LONG: Final = "A mute lasts at most 30 days."
ZONE_NEEDED: Final = "Choose the time zone this project's days follow, such as Asia/Kolkata."


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
    capped = sum(1 for row in rows if past_the_cap(row))
    counted = [row for row in rows if not past_the_cap(row)]
    learning_dates: list[date] = []
    judged = 0
    for row in counted:
        signals = signal_statuses(row.signals)
        if service_is_judged(row, signals):
            judged += 1
        learning_dates.extend(s.learning_until for s in signals if s.learning_until is not None)
    newest = max((r.last_judged_minute for r in counted if r.last_judged_minute), default=None)
    delayed = running.delayed(newest, now)

    if not running.running:
        state: ProjectDetectionState = "not_running"
    elif delayed:
        state = "data_delayed"
    elif not rows:
        state = "no_services"
    elif capped:
        state = "too_many_services"
    elif judged == 0:
        state = "learning"
    else:
        state = "judging"
    return ProjectDetectionStatus(
        state=state,
        services_total=len(rows),
        services_learning=len(counted) - judged,
        services_judged=judged,
        services_delayed=sum(1 for r in counted if running.delayed(r.last_judged_minute, now)),
        services_muted=await queries.muted_service_count(
            session, org_id=org_id, project_id=project_id, now=now
        ),
        earliest_judging_date=min(learning_dates, default=None),
        not_running_since=running.stopped_since,
        delayed_since=newest + SETTLING_LAG if delayed and newest is not None else None,
        open_findings=await queries.open_finding_count(
            session, org_id=org_id, project_id=project_id
        ),
        services_too_many=capped,
        judged_service_limit=MAX_JUDGED_SERVICES,
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


# ------------------------------------------------------------------- controls


async def snooze_service(
    session: AsyncSession,
    *,
    actor: User,
    project_id: uuid.UUID,
    service_name: str,
    until: datetime | None,
    now: datetime,
) -> ServiceControls:
    """D25: nothing is sent for the service, and its minutes stay out of the normal,
    for at most 4 hours. `until` None ends the snooze now."""
    return await _quieten(
        session,
        actor=actor,
        project_id=project_id,
        service_name=service_name,
        until=until,
        longest=MAX_SNOOZE,
        too_long=SNOOZE_TOO_LONG,
        columns=("snoozed_until", "snoozed_by"),
        action=audit.SERVICE_SNOOZED,
        now=now,
    )


async def mute_service(
    session: AsyncSession,
    *,
    actor: User,
    project_id: uuid.UUID,
    service_name: str,
    until: datetime | None,
    now: datetime,
) -> ServiceControls:
    """BR9: nothing is sent for the service, during the mute or after it, for at most
    30 days. Its findings are still recorded, marked muted. `until` None ends it now."""
    return await _quieten(
        session,
        actor=actor,
        project_id=project_id,
        service_name=service_name,
        until=until,
        longest=MAX_MUTE,
        too_long=MUTE_TOO_LONG,
        columns=("muted_until", "muted_by"),
        action=audit.SERVICE_MUTED,
        now=now,
    )


async def _quieten(
    session: AsyncSession,
    *,
    actor: User,
    project_id: uuid.UUID,
    service_name: str,
    until: datetime | None,
    longest: timedelta,
    too_long: str,
    columns: tuple[str, str],
    action: str,
    now: datetime,
) -> ServiceControls:
    project = await require_project(session, org_id=actor.org_id, project_id=project_id)
    if not await queries.service_known(
        session, org_id=actor.org_id, project_id=project.id, service_name=service_name
    ):
        raise NotFoundError(NO_SUCH_SERVICE)
    if until is not None and until <= now:
        raise InvalidRequestError(NOT_AHEAD)
    if until is not None and until - now > longest:
        raise InvalidRequestError(too_long)
    until_column, by_column = columns
    settings = await queries.quieten(
        session,
        org_id=actor.org_id,
        project_id=project.id,
        service_name=service_name,
        values={until_column: until, by_column: None if until is None else actor.id},
    )
    audit.record_service_quietened(
        session,
        action=action,
        org_id=actor.org_id,
        project_id=project.id,
        service_name=service_name,
        until=until,
        actor_user_id=actor.id,
    )
    return ServiceControls(
        muted_until=_still_ahead(settings.muted_until, now),
        snoozed_until=_still_ahead(settings.snoozed_until, now),
    )


def project_settings(project: Project) -> DetectionSettings:
    return DetectionSettings(sensitivity=project.detection_sensitivity, timezone=project.timezone)


async def change_project_settings(
    session: AsyncSession,
    *,
    actor: User,
    project_id: uuid.UUID,
    request: DetectionSettingsWrite,
) -> DetectionSettings:
    """FR18, FR24: from the next judged minute. The normal is rebuilt now for the new
    window or the new local clock, from the same history, so no learning starts over
    (AC35); an open finding is judged under the new sensitivity and may close (AC30)."""
    timezone = stored_timezone(request.timezone)
    if timezone is None:
        raise InvalidRequestError(ZONE_NEEDED)
    project = await projects_queries.lock_project(
        session, org_id=actor.org_id, project_id=project_id
    )
    if project is None:
        raise NotFoundError(NO_SUCH_PROJECT)
    changes = {
        name: {"from": before, "to": after}
        for name, before, after in (
            ("sensitivity", project.detection_sensitivity, request.sensitivity),
            ("timezone", project.timezone, timezone),
        )
        if before != after
    }
    if changes:
        project.detection_sensitivity, project.timezone = request.sensitivity, timezone
        audit.record_detection_changed(
            session, project=project, actor_user_id=actor.id, changes=changes
        )
        await findings_queries.queue_baselines(
            session, org_id=project.org_id, project_id=project.id
        )
        await session.flush()
    return project_settings(project)


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


def past_the_cap(row: DetectionState | None) -> bool:
    """Whether the project job left the service unjudged: the project has too many."""
    return row is not None and TOO_MANY_SERVICES_KEY in (row.signals or {})


def has_no_incoming_requests(stored: Mapping[str, Any]) -> bool:
    """The last judged window had no requests, and its normal expects too few for an
    empty window to be a stop. A run that recorded no count says nothing."""
    traffic = stored.get("traffic") or {}
    return traffic.get(WINDOW_REQUESTS_KEY) == 0 and traffic.get("verdict") in _EXPECTS_TOO_FEW


def service_state(
    *,
    running: bool,
    delayed: bool,
    row: DetectionState | None,
    signals: Iterable[SignalStatus],
    muted: bool,
) -> ServiceDetectionState:
    """The badge's one word: a stopped detector, then the cap, then delayed data,
    outrank the rest. Past the cap, an old judged minute is not delayed data."""
    if not running:
        return "not_running"
    if past_the_cap(row):
        return "too_many_services"
    if delayed:
        return "data_delayed"
    if muted:
        return "muted"
    if row is None or row.last_judged_minute is None:
        return "no_detection_yet"
    if not service_is_judged(row, signals):
        return "learning"
    return "no_incoming_requests" if has_no_incoming_requests(row.signals or {}) else "judged"


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
