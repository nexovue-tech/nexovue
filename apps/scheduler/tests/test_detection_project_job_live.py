"""One project's judging against real Postgres and ClickHouse (plan T2c.3, T3.3).

History is written straight into `service_stats_1m` with the store's own account,
the hourly baseline job builds the normal from it, and the project job is run
minute by minute, as the coordinator would hand it settled minutes. This suite
fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import asyncio
import socket
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from alert_fixtures import AlertTenant, add_channel, add_rule, anomaly_rule
from clickhouse_connect.driver.client import Client
from detection_history import (
    CLICKHOUSE_DSN,
    MINUTE,
    ONSET,
    PAST_CRITICAL,
    PAST_THE_MINIMUM,
    POSTGRES_DSN,
    REQUESTS,
    SERVICE,
    SUSTAIN,
    TIME_TO_A_FINDING,
)
from detection_history import baselines_for as _baselines
from detection_history import findings as _findings
from detection_history import judge_through as _judge_through
from detection_history import seed as _seed
from detection_history import seed_endpoint_latency as _seed_endpoint_latency
from detection_history import seed_endpoints as _seed_endpoints
from detection_history import seed_four_weeks as _seed_four_weeks
from detection_history import span as _span
from detection_history import state as _state
from live import require_stack
from nexovue_platform.alerts.model import service_subject
from nexovue_platform.detection import TOO_MANY_SERVICES_KEY, WINDOW_REQUESTS_KEY
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexovue_scheduler.alerts import incidents
from nexovue_scheduler.alerts.rules import Rule
from nexovue_scheduler.detection import project_job
from nexovue_scheduler.detection.baselines import TRAFFIC
from nexovue_scheduler.detection.coordinator import project_requests
from nexovue_scheduler.detection.method import RECOVERY_MINUTES
from nexovue_scheduler.detection.project_job import (
    ServiceState,
    StaleJudgementError,
    judge_project,
)
from nexovue_scheduler.detection.projects import ActiveProject, active_projects
from nexovue_scheduler.detection.store import (
    DetectionStore,
    bind_project,
)
from nexovue_scheduler.jobs.registry import SoftTimeLimit


def _reachable(url: str, default_port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or default_port), 2
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(CLICKHOUSE_DSN, 8123) and _reachable(POSTGRES_DSN, 5432),
    suite="apps/scheduler/tests/test_detection_project_job_live.py",
    remedy="ClickHouse or Postgres is not reachable — run `just up`",
)


def _rise(client: Client, tenant: AlertTenant, *, minutes: int, slower_by: int) -> list[datetime]:
    """Normal from 30 minutes before the onset, `minutes` slower from it, then
    normal for 40 minutes. The minutes to judge: 10 before the onset to 30 after."""
    span = _span(ONSET - timedelta(minutes=30), ONSET + timedelta(minutes=40))
    slow = {ONSET + step * MINUTE: slower_by for step in range(minutes)}
    _seed(client, tenant, span, slower_by=slow)
    return _span(ONSET - timedelta(minutes=10), ONSET + timedelta(minutes=30))


# ------------------------------------------------------------------- tests


async def test_a_sustained_slowdown_opens_exactly_one_finding_and_closes_it(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC8: a rise past the minimum for *sustain* opens one finding, and it closes
    as recovered after *recovery* normal minutes (FR13)."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    minutes = _rise(administrator, project, minutes=SUSTAIN, slower_by=PAST_THE_MINIMUM)

    outcomes = await _judge_through(engine, store, project, minutes)

    assert sum(outcome.opened for outcome in outcomes) == 1
    assert sum(outcome.closed for outcome in outcomes) == 1
    (finding,) = await _findings(engine, project)
    assert finding.service_name == SERVICE
    assert finding.state == "closed" and finding.closed_reason == "recovered"
    assert finding.severity == "warning"
    assert finding.started_at == ONSET
    assert finding.title.startswith(f"{SERVICE} is 2.")
    assert finding.title.endswith(f"slower than normal since {ONSET:%H:%M} UTC")
    (signal,) = finding.signals
    assert signal["signal"] == "slower"
    assert set(signal) == {
        "signal",
        "key",
        "normal",
        "band_low",
        "band_high",
        "current",
        "worst",
        "comparison",
        "endpoint",
        "endpoint_current",
        "endpoint_last_week",
    }
    assert signal["worst"] > 2 * signal["normal"]
    # FR16: the normal's band, from the normal to normal sensitivity's minimum change.
    assert signal["band_low"] == signal["normal"]
    assert signal["band_high"] == pytest.approx(1.5 * signal["normal"])
    weekday = f"{ONSET:%A}s"
    assert signal["comparison"].startswith(f"{weekday} around 12:0")
    assert signal["comparison"].endswith(", last 4 weeks")
    state = await _state(engine, project)
    assert state.last_judged_minute == minutes[-1]
    # A closed finding leaves no keys to count; the signal's own clear run continues.
    assert "finding" not in state.signals
    assert state.signals["slower"]["clear_minutes"] >= RECOVERY_MINUTES
    readings = store.for_project(await _bound(engine, project)).readings(
        start=minutes[0], end=minutes[-1] + MINUTE
    )
    assert [reading.minute for reading in readings] == list(minutes)
    assert {reading.slower_mode for reading in readings} == {"weekly"}
    assert "unusual" in {reading.slower_verdict for reading in readings}


async def test_a_rise_too_short_to_pass_the_80_percent_rule_opens_nothing(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC8's second half, under the 80% rule the founder kept: 3 slow minutes of a
    5-minute window, one short of the 4 that open (test_detection_method.py)."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    minutes = _rise(administrator, project, minutes=SUSTAIN - 2, slower_by=PAST_THE_MINIMUM)

    outcomes = await _judge_through(engine, store, project, minutes)

    assert sum(outcome.opened for outcome in outcomes) == 0
    assert await _findings(engine, project) == []


async def test_a_slowdown_beyond_critical_is_a_critical_finding_in_time(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC12, *slower*: within *time to a finding* of the onset, and critical."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    minutes = _rise(administrator, project, minutes=30, slower_by=PAST_CRITICAL)

    outcomes = await _judge_through(engine, store, project, minutes)

    opened_at = next(m for m, o in zip(minutes, outcomes, strict=True) if o.opened)
    assert opened_at - ONSET <= TIME_TO_A_FINDING
    (finding,) = await _findings(engine, project)
    assert finding.severity == "critical"
    assert finding.state == "open"


async def test_a_service_with_under_three_days_opens_nothing_and_says_until_when(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC14: learning opens nothing, whatever its values, and names its date."""
    _seed(administrator, project, [ONSET - timedelta(days=2)])
    await _baselines(engine, store, project)
    minutes = _rise(administrator, project, minutes=30, slower_by=PAST_CRITICAL)

    outcomes = await _judge_through(engine, store, project, minutes)

    assert sum(outcome.opened for outcome in outcomes) == 0
    assert await _findings(engine, project) == []
    slower = (await _state(engine, project)).signals["slower"]
    assert slower["verdict"] == "learning"
    assert slower["learning_until"] is not None
    assert slower["learning_until"] > ONSET.date().isoformat()


async def test_a_job_killed_before_its_commit_leaves_one_finding_once_rerun(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    project: AlertTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC25: the run that would open the finding dies after freezing its readings
    and before its service's commit. Nothing it decided survives; the next run decides
    again, and there is one finding, opening at the same minute and with the same start
    as an uninterrupted run's, and one reading per minute."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    minutes = _rise(administrator, project, minutes=SUSTAIN, slower_by=PAST_THE_MINIMUM)
    probe = await _judge_through(engine, store, project, minutes)
    opening = next(m for m, o in zip(minutes, probe, strict=True) if o.opened)
    (uninterrupted,) = await _findings(engine, project)
    # Start again from nothing, with the same readings already frozen once.
    async with engine.begin() as connection:
        for table in ("findings", "detection_state"):
            await connection.execute(
                text(f"DELETE FROM {table} WHERE org_id = :org_id"), {"org_id": project.org_id}
            )
    before = [m for m in minutes if m < opening]
    await _judge_through(engine, store, project, before)

    async def killed(*_: object, **__: object) -> None:
        raise RuntimeError("killed between the readings and the commit")

    monkeypatch.setattr(project_job, "_advance", killed)
    with pytest.raises(RuntimeError, match="killed"):
        await judge_project(engine, store, project.project_id, settled=opening, now=opening)
    assert await _findings(engine, project) == []
    assert (await _state(engine, project)).last_judged_minute == before[-1]
    monkeypatch.undo()

    rerun = await _judge_through(engine, store, project, [m for m in minutes if m >= opening])

    assert rerun[0].opened == 1
    (finding,) = await _findings(engine, project)
    assert finding.started_at == uninterrupted.started_at
    assert finding.closed_at == uninterrupted.closed_at
    readings = store.for_project(await _bound(engine, project)).readings(
        start=opening, end=opening + MINUTE
    )
    assert len(readings) == 1


async def test_two_jobs_at_once_judge_each_minute_once(
    store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """The lock: a second job for the project finds it held and does nothing, so
    each minute is judged once however many are queued (design, "Failure handling")."""
    first_engine = create_async_engine(POSTGRES_DSN.replace("://", "+asyncpg://", 1))
    second_engine = create_async_engine(POSTGRES_DSN.replace("://", "+asyncpg://", 1))
    try:
        _seed_four_weeks(administrator, project)
        await _baselines(first_engine, store, project)
        minutes = _rise(administrator, project, minutes=SUSTAIN, slower_by=PAST_THE_MINIMUM)
        judged = 0
        for minute in minutes:
            pair = await asyncio.gather(
                judge_project(first_engine, store, project.project_id, settled=minute, now=minute),
                judge_project(second_engine, store, project.project_id, settled=minute, now=minute),
            )
            judged += sum(outcome.minutes_judged for outcome in pair)
            # A job that lost the race judged nothing, rather than judging again.
            assert sum(outcome.skipped is None for outcome in pair) >= 1
        # Every minute judged, none twice; a skipped minute is caught up by the next run.
        assert judged == len(minutes)
        assert len(await _findings(first_engine, project)) == 1
    finally:
        await first_engine.dispose()
        await second_engine.dispose()


async def test_a_service_moved_by_another_job_refuses_the_whole_commit(
    engine: AsyncEngine, project: AlertTenant
) -> None:
    """The compare-and-set, should two jobs ever both hold a project: the second to
    commit finds the minute already moved, and its transaction is rolled back."""
    minute = ONSET
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO detection_state "
                "(org_id, project_id, service_name, last_judged_minute) "
                "VALUES (:org_id, :project_id, :service, :minute)"
            ),
            {
                "org_id": project.org_id,
                "project_id": project.project_id,
                "service": SERVICE,
                "minute": minute,
            },
        )
    binding = await _bound(engine, project)
    stale = ServiceState(SERVICE, minute - MINUTE)

    with pytest.raises(StaleJudgementError):
        async with engine.begin() as connection:
            await project_job._advance(connection, binding, stale, minute + MINUTE, {})

    assert (await _state(engine, project)).last_judged_minute == minute


async def test_a_project_with_recent_traffic_is_handed_a_job(
    engine: AsyncEngine, project: AlertTenant
) -> None:
    """The coordinator's choice: traffic in the last 24 hours (the usage meter's
    `service_last_seen`), one job per project on the detection queue."""
    now = datetime.now(UTC)
    async with engine.connect() as connection:
        quiet = await active_projects(connection, now=now)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO service_last_seen (org_id, project_id, service_name, last_seen_at) "
                "VALUES (:org_id, :project_id, :service, :seen)"
            ),
            {
                "org_id": project.org_id,
                "project_id": project.project_id,
                "service": SERVICE,
                "seen": now - timedelta(hours=1),
            },
        )
    async with engine.connect() as connection:
        heard = await active_projects(connection, now=now)

    mine = ActiveProject(project.org_id, project.project_id)
    assert mine not in quiet
    assert mine in heard
    (request,) = [
        each
        for each in project_requests(heard, ONSET)
        if each.kwargs["project_id"] == str(project.project_id)
    ]
    assert request.queue == "detection"
    assert request.org_id == str(project.org_id)
    assert request.kwargs["settled_minute"] == ONSET.isoformat()


# ------------------------------------------------------------ Step 3 (T3.3)

ENDPOINTS: Final = {
    "GET /cart": (60, 0),
    "POST /pay": (20, 0),
    "GET /item": (15, 1),
    "GET /": (5, 0),
}


async def test_two_hours_on_two_signals_and_three_endpoints_are_one_finding(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC18, BR2: slower and failing more, with three endpoints failing, for two hours:
    one finding, whose worst values and title follow the deviation, closing once
    every key has recovered."""
    _seed_four_weeks(administrator, project, endpoints=ENDPOINTS, until=timedelta(minutes=150))
    await _baselines(engine, store, project)
    deviation = timedelta(hours=2)
    failing = {"GET /cart": (60, 12), "POST /pay": (20, 15), "GET /item": (15, 12), "GET /": (5, 0)}
    span = _span(ONSET - timedelta(minutes=30), ONSET + deviation + timedelta(minutes=30))
    _seed(
        administrator,
        project,
        span,
        slower_by={m: 3 for m in span if ONSET <= m < ONSET + deviation},
        endpoints={m: failing if ONSET <= m < ONSET + deviation else ENDPOINTS for m in span},
    )
    minutes = _span(ONSET - timedelta(minutes=10), ONSET + deviation + timedelta(minutes=20))

    outcomes = []
    for minute in minutes:
        if minute == ONSET + timedelta(minutes=100):
            await _baselines(engine, store, project, now=minute)
        outcomes.append(
            await judge_project(engine, store, project.project_id, settled=minute, now=minute)
        )

    assert sum(o.opened for o in outcomes) == 1
    assert sum(o.closed for o in outcomes) == 1
    (finding,) = await _findings(engine, project)
    assert finding.closed_reason == "recovered"
    assert finding.closed_at >= ONSET + deviation
    assert finding.started_at <= ONSET + timedelta(minutes=1)
    assert finding.title.startswith(f"{SERVICE} is failing ")
    assert finding.title.endswith(", and is slower")
    signals = {(s["signal"], s["endpoint"]) for s in finding.signals}
    assert ("slower", None) in signals
    endpoints = {endpoint for signal, endpoint in signals if signal == "failing"}
    assert {"POST /pay", "GET /item"} <= endpoints
    assert finding.signals[0]["signal"] == "failing"


async def test_nine_of_eleven_services_stopping_is_one_project_finding(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC21, FR15: one project finding, critical, and no finding for any service."""
    services = [f"svc-{index:02}" for index in range(11)]
    for service in services:
        _seed_four_weeks(administrator, project, service=service)
        stops = service < services[9]
        today = _span(
            ONSET - timedelta(minutes=30),
            ONSET + (-timedelta(minutes=1) if stops else timedelta(minutes=30)),
        )
        _seed(administrator, project, today, service=service)
    await _baselines(engine, store, project)
    minutes = _span(ONSET - timedelta(minutes=5), ONSET + timedelta(minutes=15))

    outcomes = await _judge_through(engine, store, project, minutes)

    (finding,) = await _findings(engine, project)
    assert finding.scope == "project" and finding.service_name is None
    assert finding.severity == "critical" and finding.state == "open"
    assert finding.title == (
        f"Traffic stopped arriving from 9 of 11 services at {ONSET:%H:%M} UTC. "
        "Check your collector, your network or nexovue's intake"
    )
    # Its page lists them, and still does once it closes (migration 0046).
    assert finding.services == services[:9]
    assert sum(o.opened for o in outcomes) == 1
    opened_at = next(m for m, o in zip(minutes, outcomes, strict=True) if o.opened)
    assert opened_at - ONSET <= TIME_TO_A_FINDING


async def test_a_lower_sensitivity_closes_a_finding_its_minimum_no_longer_holds(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC30, FR18: about 1.7 times slower opens a finding at normal. Changed to low,
    whose minimum is 2 times, it closes after *recovery* with the rise unchanged, and
    the same rise opens nothing more."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    span = _span(ONSET - timedelta(minutes=30), ONSET + timedelta(minutes=60))
    _seed(administrator, project, span, slower_by={m: 3 for m in span if m >= ONSET})
    at_normal = _span(ONSET - timedelta(minutes=5), ONSET + timedelta(minutes=10))
    opened = await _judge_through(engine, store, project, at_normal)
    assert sum(o.opened for o in opened) == 1
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE projects SET detection_sensitivity = 'low' WHERE id = :id"),
            {"id": project.project_id},
        )
    switched = ONSET + timedelta(minutes=11)
    await _baselines(engine, store, project, now=switched)

    at_low = await _judge_through(
        engine, store, project, _span(switched, ONSET + timedelta(minutes=45))
    )

    assert sum(o.closed for o in at_low) == 1
    assert sum(o.opened for o in at_low) == 0
    (finding,) = await _findings(engine, project)
    assert finding.closed_reason == "recovered"
    assert finding.closed_at - switched <= timedelta(minutes=RECOVERY_MINUTES + 1)


def _last_thursday_noon() -> datetime:
    """A recent Thursday: three weekdays of history before it make it day 3 (D30)."""
    day = ONSET - timedelta(days=1)
    while day.weekday() != 3:
        day -= timedelta(days=1)
    return day


@pytest.mark.parametrize("sensitivity", ["low", "normal"])
async def test_a_small_service_that_stops_on_day_three_is_critical_then_gone(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    project: AlertTenant,
    sensitivity: str,
) -> None:
    """AC41, FR32, D30: 2 requests a minute, history since Monday, stops at noon on
    Thursday: a critical finding within *time to a finding*, at low sensitivity as at
    normal. A day of silence later it closes as *service gone*, said once."""
    await _set_sensitivity(engine, project, sensitivity)
    slack = await _slack_rule(engine, project)
    onset = _last_thursday_noon()
    monday = onset.replace(hour=0) - timedelta(days=3)
    _seed(administrator, project, _span(monday, onset - MINUTE), requests=2)
    await _baselines(engine, store, project, now=onset - timedelta(minutes=5))
    minutes = _span(onset - timedelta(minutes=5), onset + timedelta(minutes=12))

    outcomes = await _judge_through(engine, store, project, minutes)

    opened_at = next(m for m, o in zip(minutes, outcomes, strict=True) if o.opened)
    assert opened_at - onset <= TIME_TO_A_FINDING
    (finding,) = await _findings(engine, project)
    assert finding.severity == "critical" and finding.state == "open"
    assert finding.signals[0]["signal"] == "traffic"
    assert finding.title == f"{SERVICE} stopped receiving requests at {onset:%H:%M} UTC"
    a_day_later = onset + timedelta(days=1, minutes=1)

    gone = await _judge_through(engine, store, project, [a_day_later])

    assert gone[0].closed == 1
    (finding,) = await _findings(engine, project)
    assert finding.closed_reason == "service_gone"
    assert await _told(engine, project, slack) == ["opened", "resolved"]


async def test_a_finding_that_cannot_be_judged_for_six_hours_closes_as_such(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """D20: a critical slowdown opens a finding; its service is then judged against no
    normal at all, and 6 hours later the finding closes as *could no longer be
    judged*, not as recovered."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    _seed(
        administrator,
        project,
        _span(ONSET - timedelta(minutes=30), ONSET + timedelta(minutes=10)),
        slower_by={ONSET + step * MINUTE: PAST_CRITICAL for step in range(11)},
    )
    opened = await _judge_through(
        engine, store, project, _span(ONSET, ONSET + timedelta(minutes=10))
    )
    assert sum(o.opened for o in opened) == 1
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM detection_baselines WHERE org_id = :org_id"),
            {"org_id": project.org_id},
        )
    last = ONSET + timedelta(minutes=10)
    closed_at = None
    # Two minutes a run, as a job catching up judges them.
    while closed_at is None and last < ONSET + timedelta(hours=7):
        last += 2 * MINUTE
        outcome = await judge_project(engine, store, project.project_id, settled=last, now=last)
        if outcome.closed:
            closed_at = last

    (finding,) = await _findings(engine, project)
    assert finding.closed_reason == "could_not_be_judged"
    assert closed_at is not None
    assert timedelta(hours=6) <= closed_at - ONSET <= timedelta(hours=6, minutes=15)


@pytest.mark.parametrize("scope", ["service", "project"])
async def test_a_second_open_finding_for_one_scope_is_refused_whichever_job_commits(
    project: AlertTenant, scope: str
) -> None:
    """The partial unique indexes: two jobs that both believe none is open race to
    insert; the second waits for the first, then is refused."""
    first_engine = create_async_engine(POSTGRES_DSN.replace("://", "+asyncpg://", 1))
    second_engine = create_async_engine(POSTGRES_DSN.replace("://", "+asyncpg://", 1))
    row = {
        "org_id": project.org_id,
        "project_id": project.project_id,
        "scope": scope,
        "service_name": SERVICE if scope == "service" else None,
        "severity": "warning",
        "muted": False,
        "title": "a race",
        "services": "[]",
        "signals": "[]",
        "started_at": ONSET,
    }
    try:
        async with first_engine.connect() as first, second_engine.connect() as second:
            await first.begin()
            await second.begin()
            await first.execute(project_job._OPEN, row)
            racing = asyncio.create_task(second.execute(project_job._OPEN, row))
            await asyncio.sleep(0.2)
            assert not racing.done(), "the second insert waits on the first's transaction"
            await first.commit()
            with pytest.raises(IntegrityError, match="uq_findings_project_id"):
                await racing
            await second.rollback()
        assert len(await _findings(first_engine, project)) == 1
    finally:
        await first_engine.dispose()
        await second_engine.dispose()


async def _bound(engine: AsyncEngine, tenant: AlertTenant) -> Any:
    async with engine.connect() as connection:
        binding = await bind_project(connection, tenant.project_id)
    assert binding is not None
    return binding


# ------------------------------------------------- notifications and links (Step 4)


async def _threshold_incident(engine: AsyncEngine, tenant: AlertTenant, at: datetime) -> Any:
    """An error-rate incident open on the service before detection sees anything."""
    condition = {"kind": "service_error_rate", "threshold_percent": 5}
    async with engine.begin() as connection:
        rule_id = await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Checkout errors",
            condition=condition,
            channel_ids=[],
        )
        rule = Rule(
            id=rule_id,
            org_id=tenant.org_id,
            project_id=tenant.project_id,
            name="Checkout errors",
            condition_kind="service_error_rate",
            condition=condition,
            muted_until=None,
            channel_ids=(),
        )
        await incidents.fire(
            connection,
            rule,
            incidents.Subject(key=service_subject(SERVICE), label=SERVICE),
            severity="critical",
            at=at,
            detail={},
        )
        await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Critical problems",
            condition=anomaly_rule("critical"),
            channel_ids=[tenant.owners_channel_id],
        )
    return rule


async def test_a_finding_opening_beside_a_threshold_incident_is_linked_and_told_once(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC20, AC22, from the job: one finding linked to the one threshold incident, and
    the owners told it opened and that it closed, by the *Critical problems* rule."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    await _threshold_incident(engine, project, ONSET)
    minutes = _rise(administrator, project, minutes=8, slower_by=PAST_CRITICAL)

    outcomes = await _judge_through(engine, store, project, minutes)

    (finding,) = await _findings(engine, project)
    assert (finding.severity, finding.closed_reason) == ("critical", "recovered")
    async with engine.connect() as connection:
        links = list(
            await connection.execute(
                text(
                    "SELECT finding_incidents.finding_id, incidents.subject_key "
                    "FROM finding_incidents JOIN incidents "
                    "ON incidents.id = finding_incidents.incident_id "
                    "WHERE finding_incidents.org_id = :org_id"
                ),
                {"org_id": project.org_id},
            )
        )
        told = list(
            await connection.execute(
                text(
                    "SELECT kind, channel_id FROM notifications "
                    "WHERE org_id = :org_id AND finding_id = :finding_id ORDER BY created_at"
                ),
                {"org_id": project.org_id, "finding_id": finding.id},
            )
        )
    assert [(link.finding_id, link.subject_key) for link in links] == [
        (finding.id, service_subject(SERVICE))
    ]
    assert [(row.kind, row.channel_id) for row in told] == [
        ("opened", project.owners_channel_id),
        ("resolved", project.owners_channel_id),
    ]
    assert sum(outcome.notified for outcome in outcomes) == 2


# ------------------------------------------- the cap and the quiet (design, *Inputs*)


async def test_past_the_cap_the_busiest_are_judged_and_the_rest_are_marked(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    project: AlertTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The design's cap of 500 judged services, at 2: the two busiest are judged, and
    the third is not, read nothing for, and says since when it is past the cap."""
    window = _span(ONSET - timedelta(minutes=10), ONSET)
    for service, requests in (("busy", 300), ("mid", 100), ("quiet", 10)):
        _seed(administrator, project, window, service=service, requests=requests)
    monkeypatch.setattr(project_job, "MAX_JUDGED_SERVICES", 2)

    outcome = await judge_project(engine, store, project.project_id, settled=ONSET, now=ONSET)

    assert (outcome.services, outcome.over_cap) == (2, 1)
    for service in ("busy", "mid"):
        judged = await _state(engine, project, service)
        assert judged.last_judged_minute == ONSET
        assert TOO_MANY_SERVICES_KEY not in judged.signals
    quiet = await _state(engine, project, "quiet")
    assert quiet.last_judged_minute is None
    assert quiet.signals[TOO_MANY_SERVICES_KEY] == ONSET.isoformat()
    readings = store.for_project(await _bound(engine, project)).readings(
        start=ONSET, end=ONSET + MINUTE
    )
    assert {reading.service_name for reading in readings} == {"busy", "mid"}


async def test_a_service_back_under_the_cap_is_judged_and_loses_its_mark(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    project: AlertTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _span(ONSET - timedelta(minutes=10), ONSET + MINUTE)
    for service, requests in (("busy", 300), ("quiet", 10)):
        _seed(administrator, project, window, service=service, requests=requests)
    monkeypatch.setattr(project_job, "MAX_JUDGED_SERVICES", 1)
    await judge_project(engine, store, project.project_id, settled=ONSET, now=ONSET)
    first = (await _state(engine, project, "quiet")).signals[TOO_MANY_SERVICES_KEY]
    # Still past it a minute later: the mark keeps the minute it began.
    later = ONSET + MINUTE
    await judge_project(engine, store, project.project_id, settled=later, now=later)
    assert (await _state(engine, project, "quiet")).signals[TOO_MANY_SERVICES_KEY] == first

    monkeypatch.setattr(project_job, "MAX_JUDGED_SERVICES", 2)
    again = ONSET + 2 * MINUTE
    outcome = await judge_project(engine, store, project.project_id, settled=again, now=again)

    assert (outcome.services, outcome.over_cap) == (2, 0)
    quiet = await _state(engine, project, "quiet")
    assert quiet.last_judged_minute == again
    assert TOO_MANY_SERVICES_KEY not in quiet.signals


async def test_each_run_records_the_judged_window_s_requests_for_no_incoming_requests(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """*Inputs*: a service with no requests, against a normal that expects too few to
    judge, reads *Not judged: no incoming requests*. The API tells it from the
    traffic signal's verdict and the window's requests, which the job records."""
    _seed(administrator, project, _span(ONSET - timedelta(minutes=10), ONSET))
    await judge_project(engine, store, project.project_id, settled=ONSET, now=ONSET)
    sending = (await _state(engine, project)).signals[TRAFFIC]
    assert sending[WINDOW_REQUESTS_KEY] == SUSTAIN * REQUESTS

    quiet = ONSET + timedelta(minutes=15)
    await judge_project(engine, store, project.project_id, settled=quiet, now=quiet)

    assert (await _state(engine, project)).signals[TRAFFIC][WINDOW_REQUESTS_KEY] == 0


# ------------------------------------------- reading versions (design, *Readings*)


async def test_a_re_judged_minute_wins_over_a_crashed_run_from_a_clock_ahead(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    project: AlertTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run on a host whose clock is a day ahead freezes its reading and dies before
    its commit. Spans land late for the minute, and the re-run judges it again: the
    reading read back must be the re-run's, whatever either host's clock said."""
    window = _span(ONSET - timedelta(minutes=10), ONSET)
    _seed(administrator, project, window)

    async def killed(*_: object, **__: object) -> None:
        raise RuntimeError("killed between the readings and the commit")

    real_time_ns = time.time_ns
    monkeypatch.setattr(time, "time_ns", lambda: real_time_ns() + 86_400 * 10**9)
    monkeypatch.setattr(project_job, "_advance", killed)
    with pytest.raises(RuntimeError, match="killed"):
        await judge_project(engine, store, project.project_id, settled=ONSET, now=ONSET)
    monkeypatch.undo()
    _seed(administrator, project, [ONSET])

    await judge_project(engine, store, project.project_id, settled=ONSET, now=ONSET)

    (reading,) = store.for_project(await _bound(engine, project)).readings(
        start=ONSET, end=ONSET + MINUTE
    )
    assert reading.window_requests == (SUSTAIN + 1) * REQUESTS


# ------------------------------------------------------- the evidence endpoint (FR3)


async def test_a_slower_finding_names_the_endpoint_whose_p95_rose_most_since_last_week(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """FR3: *slower* names the endpoint whose p95 rose most in milliseconds against the
    same window last week, with both values, as evidence. A fast endpoint that rose
    five times, and one too rarely called to have a p95, are not it."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    minutes = _rise(administrator, project, minutes=SUSTAIN, slower_by=PAST_THE_MINIMUM)
    judged_span = _span(minutes[0] - SUSTAIN * MINUTE, minutes[-1])
    week = timedelta(days=7)
    last_week = {"GET /cart": (50, 50.0), "POST /pay": (30, 100.0), "GET /fast": (40, 1.0)}
    today = {"GET /cart": (50, 60.0), "POST /pay": (30, 400.0), "GET /fast": (40, 5.0)}
    _seed_endpoint_latency(
        administrator, project, SERVICE, [m - week for m in judged_span], last_week
    )
    _seed_endpoint_latency(administrator, project, SERVICE, judged_span, today)
    _seed_endpoint_latency(administrator, project, SERVICE, judged_span, {"GET /rare": (2, 5000.0)})

    await _judge_through(engine, store, project, minutes)

    (finding,) = await _findings(engine, project)
    (signal,) = finding.signals
    assert signal["signal"] == "slower"
    assert signal["endpoint"] == "POST /pay"
    assert signal["endpoint_current"] == pytest.approx(400.0)
    assert signal["endpoint_last_week"] == pytest.approx(100.0)


async def test_a_failing_finding_names_the_endpoint_with_most_extra_failures_since_last_week(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """FR3: *failing more* names the endpoint with the largest share of the failures
    beyond the same window last week, with both shares failing. One that failed as
    much then has no extra failures, however far it is above its normal."""
    endpoints = {"GET /cart": (60, 0), "POST /pay": (40, 0)}
    _seed_four_weeks(administrator, project, endpoints=endpoints)
    await _baselines(engine, store, project)
    week = timedelta(days=7)
    failing_span = _span(ONSET, ONSET + timedelta(minutes=SUSTAIN + 4))
    around = _span(ONSET - timedelta(minutes=10), ONSET + timedelta(minutes=20))
    # Last week's same minutes: GET /cart already failed half its requests then.
    _seed_endpoints(
        administrator,
        project,
        SERVICE,
        {minute - week: {"GET /cart": (0, 30)} for minute in around},
    )
    today = {"GET /cart": (60, 30), "POST /pay": (40, 20)}
    seeded = _span(ONSET - timedelta(minutes=30), ONSET + timedelta(minutes=40))
    _seed(
        administrator,
        project,
        seeded,
        endpoints={minute: today if minute in failing_span else endpoints for minute in seeded},
    )

    await _judge_through(engine, store, project, failing_span)

    (finding,) = await _findings(engine, project)
    service, *endpoint_entries = [s for s in finding.signals if s["signal"] == "failing"]
    assert service["endpoint"] == "POST /pay"
    assert service["endpoint_current"] == pytest.approx(50.0)
    assert service["endpoint_last_week"] == pytest.approx(0.0)
    # Each endpoint's own entry compares itself with the same window last week.
    compared = {
        e["endpoint"]: (e["endpoint_current"], e["endpoint_last_week"]) for e in endpoint_entries
    }
    assert compared == {"GET /cart": (50.0, 50.0), "POST /pay": (50.0, 0.0)}


# ------------------------------------ an overrunning run (design, *Failure handling*)


def _limit_after(monkeypatch: pytest.MonkeyPatch, advances: int) -> None:
    """The soft limit arrives as the run commits its next service after `advances`, one
    service to a commit, as it would deep inside a project too big for its budget."""
    advance = project_job._advance
    done = 0

    async def advance_until_the_limit(*args: Any, **kwargs: Any) -> None:
        nonlocal done
        if done == advances:
            raise SoftTimeLimit
        done += 1
        await advance(*args, **kwargs)

    monkeypatch.setattr(project_job, "SERVICES_PER_COMMIT", 1)
    monkeypatch.setattr(project_job, "_advance", advance_until_the_limit)


async def test_a_run_stopped_by_its_limit_keeps_what_it_finished_and_the_next_continues(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    project: AlertTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A project whose run passes its soft limit commits the services it finished; the
    next run judges only the rest, so a project always over budget still moves."""
    services = ["svc-a", "svc-b", "svc-c"]
    for service in services:
        _seed(administrator, project, _span(ONSET - timedelta(minutes=10), ONSET), service=service)
    _limit_after(monkeypatch, advances=1)

    with pytest.raises(SoftTimeLimit):
        await judge_project(engine, store, project.project_id, settled=ONSET, now=ONSET)
    monkeypatch.undo()
    judged = [(await _state(engine, project, name)).last_judged_minute for name in services]
    assert judged.count(ONSET) == 1 and judged.count(None) == 2

    outcome = await judge_project(engine, store, project.project_id, settled=ONSET, now=ONSET)

    assert outcome.services == 2
    for service in services:
        assert (await _state(engine, project, service)).last_judged_minute == ONSET


async def test_a_project_finding_survives_runs_stopped_by_their_limit(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    project: AlertTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC21 with every run stopped halfway through its services and continued: the run
    that continues decides the project's finding on every service, those already
    judged included, so it is opened once and not closed by the half it sees."""
    services = [f"svc-{index:02}" for index in range(11)]
    for service in services:
        _seed_four_weeks(administrator, project, service=service)
        stops = service < services[9]
        today = _span(
            ONSET - timedelta(minutes=30),
            ONSET + (-timedelta(minutes=1) if stops else timedelta(minutes=30)),
        )
        _seed(administrator, project, today, service=service)
    await _baselines(engine, store, project)
    for minute in _span(ONSET - timedelta(minutes=5), ONSET + timedelta(minutes=15)):
        _limit_after(monkeypatch, advances=5)
        with pytest.raises(SoftTimeLimit):
            await judge_project(engine, store, project.project_id, settled=minute, now=minute)
        monkeypatch.undo()
        await judge_project(engine, store, project.project_id, settled=minute, now=minute)

    (finding,) = await _findings(engine, project)
    assert finding.scope == "project" and finding.state == "open"
    assert finding.services == services[:9]


# ------------------------------- what the job tells a channel (AC5, AC19, AC31)


async def _slack_rule(engine: AsyncEngine, tenant: AlertTenant) -> Any:
    """A Slack channel told of every finding, warnings included."""
    async with engine.begin() as connection:
        slack = await add_channel(connection, tenant.org_id, kind="slack", name="Slack")
        await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Findings",
            condition=anomaly_rule("warning"),
            channel_ids=[slack],
        )
    return slack


async def _told(engine: AsyncEngine, tenant: AlertTenant, channel: Any) -> list[str]:
    """What the channel was told, in the order the job wrote it."""
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT kind FROM notifications WHERE org_id = :org_id AND channel_id = :channel "
                "ORDER BY created_at, id"
            ),
            {"org_id": tenant.org_id, "channel": channel},
        )
        return [row.kind for row in rows]


async def _set_sensitivity(engine: AsyncEngine, tenant: AlertTenant, level: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE projects SET detection_sensitivity = :level WHERE id = :id"),
            {"level": level, "id": tenant.project_id},
        )


async def test_a_gap_too_quiet_to_judge_slower_keeps_the_finding_open_and_silent(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC5, FR11: a slower finding, then 20 minutes with too few requests to judge
    *slower*, then slow again. The gap reads *too little traffic*, the finding stays
    open through it, and nothing is said after the opening: no *raised*, no *joined*."""
    usual, too_few = 45, 35
    _seed_four_weeks(administrator, project, requests=usual)
    await _baselines(engine, store, project)
    slack = await _slack_rule(engine, project)
    gap_from, gap_to = ONSET + timedelta(minutes=10), ONSET + timedelta(minutes=30)
    today = _span(ONSET - timedelta(minutes=30), ONSET + timedelta(minutes=40))
    slow = {minute: PAST_THE_MINIMUM for minute in today if minute >= ONSET}
    quiet = [minute for minute in today if gap_from <= minute < gap_to]
    _seed(
        administrator,
        project,
        [minute for minute in today if minute not in quiet],
        slower_by=slow,
        requests=usual,
    )
    _seed(administrator, project, quiet, slower_by=slow, requests=too_few)
    minutes = _span(ONSET - timedelta(minutes=5), ONSET + timedelta(minutes=40))

    await _judge_through(engine, store, project, minutes)

    (finding,) = await _findings(engine, project)
    assert finding.state == "open"
    readings = store.for_project(await _bound(engine, project)).readings(
        start=gap_from + SUSTAIN * MINUTE, end=gap_to
    )
    assert {reading.slower_verdict for reading in readings} == {"too_little_traffic"}
    assert await _told(engine, project, slack) == ["opened"]


async def test_a_two_hour_deviation_tells_slack_opened_joined_escalated_resolved_in_turn(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC19 through the job, on AC18's two hours: slower at first, then failing more on
    the service and an endpoint, then slower past critical, then over. Slack hears
    *opened*, then each join, then *escalated*, then *resolved*, and nothing else."""
    _seed_four_weeks(administrator, project, endpoints=ENDPOINTS, until=timedelta(minutes=150))
    await _baselines(engine, store, project)
    slack = await _slack_rule(engine, project)
    deviation = timedelta(hours=2)
    joins, critical = ONSET + timedelta(minutes=20), ONSET + timedelta(minutes=60)
    failing = {"GET /cart": (60, 0), "POST /pay": (20, 3), "GET /item": (15, 1), "GET /": (5, 0)}
    span = _span(ONSET - timedelta(minutes=30), ONSET + deviation + timedelta(minutes=30))
    during = [minute for minute in span if ONSET <= minute < ONSET + deviation]
    _seed(
        administrator,
        project,
        span,
        slower_by={minute: PAST_CRITICAL if minute >= critical else 3 for minute in during},
        endpoints={
            minute: failing if joins <= minute < ONSET + deviation else ENDPOINTS for minute in span
        },
    )
    minutes = _span(ONSET - timedelta(minutes=10), ONSET + deviation + timedelta(minutes=20))

    for minute in minutes:
        if minute == ONSET + timedelta(minutes=100):
            await _baselines(engine, store, project, now=minute)
        await judge_project(engine, store, project.project_id, settled=minute, now=minute)

    (finding,) = await _findings(engine, project)
    assert (finding.severity, finding.closed_reason) == ("critical", "recovered")
    told = await _told(engine, project, slack)
    assert told[0] == "opened"
    assert told[-2:] == ["escalated", "resolved"]
    assert told[1:-2] and set(told[1:-2]) == {"joined"}


async def test_a_muted_service_s_rise_is_one_muted_finding_that_tells_nobody(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC31, BR9: AC8's rise on a muted service records one finding marked muted, and
    nothing is sent while the mute lasts, nor once it has ended with the finding open."""
    _seed_four_weeks(administrator, project)
    await _baselines(engine, store, project)
    slack = await _slack_rule(engine, project)
    mute_ends = ONSET + timedelta(minutes=8)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO service_detection_settings "
                "(org_id, project_id, service_name, muted_until) "
                "VALUES (:org_id, :project_id, :service, :until)"
            ),
            {
                "org_id": project.org_id,
                "project_id": project.project_id,
                "service": SERVICE,
                "until": mute_ends,
            },
        )
    minutes = _rise(administrator, project, minutes=SUSTAIN, slower_by=PAST_THE_MINIMUM)

    outcomes = await _judge_through(engine, store, project, minutes)

    (finding,) = await _findings(engine, project)
    assert finding.muted is True
    assert finding.state == "closed" and finding.closed_at > mute_ends
    assert sum(outcome.notified for outcome in outcomes) == 0
    assert await _told(engine, project, slack) == []


async def test_after_a_time_zone_change_and_a_rebuild_services_are_judged_not_learning(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC35, FR24: a project moved from UTC to another zone keeps its history. Once the
    normal is rebuilt in the new zone, every signal is judged against it, weekly or
    daily, and none starts learning again."""
    _seed_four_weeks(administrator, project)
    _seed(administrator, project, _span(ONSET - timedelta(minutes=30), ONSET))
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE projects SET timezone = 'Asia/Kolkata' WHERE id = :id"),
            {"id": project.project_id},
        )
    await _baselines(engine, store, project)

    await _judge_through(engine, store, project, _span(ONSET - timedelta(minutes=5), ONSET))

    signals = (await _state(engine, project)).signals
    assert {signals[signal]["verdict"] for signal in ("slower", "failing", "traffic")} <= {
        "normal",
        "elevated",
    }
    (reading,) = store.for_project(await _bound(engine, project)).readings(
        start=ONSET, end=ONSET + MINUTE
    )
    modes = {reading.slower_mode, reading.failing_mode, reading.traffic_mode}
    assert modes <= {"weekly", "daily"}
