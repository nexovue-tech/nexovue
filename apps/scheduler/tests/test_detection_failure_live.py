"""Safe under nexovue's own failures, and under a hostile tenant (plan T6.2; spec
AC23 to AC25; design revision 2.1, item 1), against the development stores.

The coordinator's decision (`holds.decide`) runs against real Postgres, and each
project job against real Postgres and ClickHouse, minute by minute as the sweep
would run them. What the pipeline's progress and Kafka say is given to them: the
settling rule and the read of Kafka are proved live on their own, in
`test_detection_settle_live.py` and below.

These suites judge around 14:00, two hours after the other detection suites'
onset, so the organization votes those cast never fall in this suite's minutes.
This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from alert_fixtures import AlertTenant, add_rule, anomaly_rule, drop_tenant, make_tenant
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.client import Client
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic
from detection_history import (
    CLICKHOUSE_DSN,
    ENDPOINT_ROLLUP,
    MINUTE,
    ONSET,
    POSTGRES_DSN,
    ROLLUP,
    SERVICE,
    TIME_TO_A_FINDING,
)
from detection_history import baselines_for as _baselines
from detection_history import findings as _findings
from detection_history import seed as _seed
from detection_history import seed_four_weeks as _seed_four_weeks
from detection_history import span as _span
from detection_history import state as _state
from live import require_stack
from prometheus_client import REGISTRY
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts.sweeps import KafkaWatermarks
from nexovue_scheduler.detection import holds, project_job
from nexovue_scheduler.detection.baselines import project_exclusions
from nexovue_scheduler.detection.project_job import judge_project
from nexovue_scheduler.detection.settle import (
    EXPORT_ALLOWANCE,
    HOLD_LIMIT,
    REFERENCE_SPAN,
    Intake,
    Mark,
    Settled,
    fallen,
    read_intake,
    settled_minute,
)
from nexovue_scheduler.detection.store import READINGS_TABLE, DetectionStore, bind_project

KAFKA_BROKERS: Final = "localhost:19092"
KAFKA_TIMEOUT_SECONDS: Final = 10


def _reachable(url: str, default_port: int) -> bool:
    parts = urlsplit(url if "//" in url else f"//{url}")
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or default_port), 2
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(CLICKHOUSE_DSN, 8123)
    and _reachable(POSTGRES_DSN, 5432)
    and _reachable(KAFKA_BROKERS, 9092),
    suite="apps/scheduler/tests/test_detection_failure_live.py",
    remedy="ClickHouse, Postgres or Kafka is not reachable — run `just up`",
)

# This suite's onset: clear of every other detection suite's votes (module doc).
STOP: Final = ONSET + timedelta(hours=2)
# The history reaches far enough past STOP for the daily pool's ± 3 hours.
HISTORY_UNTIL: Final = timedelta(hours=6)
# How long the traffic stops, and how long the receiver's count stays fallen.
OUTAGE: Final = timedelta(minutes=10)
# The API's own rule (`nexovue_api.detection.service.DELAYED_AFTER`): a judged minute
# further behind than this reads *data delayed*.
DELAYED_AFTER: Final = timedelta(minutes=6)
# Each sweep reads "now" this long after the minute it settles.
SETTLING_DELAY: Final = MINUTE + EXPORT_ALLOWANCE + timedelta(seconds=30)
RECEIVER: Final = f"failure-live-{uuid.uuid4().hex[:8]}"
EVENTS_METRIC: Final = "nexovue_scheduler_detection_intake_events_total"


# ------------------------------------------------------------------ fixtures


async def _clear_events(engine: AsyncEngine) -> None:
    """This suite's install-wide rows: its receiver's refusals, and events about the
    minutes it holds."""
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM intake_refusals WHERE receiver_id = :receiver"),
            {"receiver": RECEIVER},
        )
        await connection.execute(
            text(
                """
                DELETE FROM detection_events
                WHERE kind = ANY(CAST(:kinds AS text[]))
                  AND CAST(detail ->> 'from' AS timestamptz) BETWEEN :first AND :last
                """
            ),
            {
                "kinds": list(holds.EVENT_KINDS),
                "first": STOP - timedelta(hours=1),
                "last": STOP + timedelta(hours=1),
            },
        )


@pytest.fixture
async def clean(engine: AsyncEngine) -> AsyncIterator[None]:
    await _clear_events(engine)
    try:
        yield
    finally:
        await _clear_events(engine)


@pytest.fixture
async def tenants(engine: AsyncEngine) -> AsyncIterator[list[AlertTenant]]:
    """Organizations made by a test; each is dropped, its telemetry with it."""
    made: list[AlertTenant] = []
    try:
        yield made
    finally:
        for tenant in made:
            await drop_tenant(engine, tenant)


async def _organization(engine: AsyncEngine, made: list[AlertTenant]) -> AlertTenant:
    tenant = await make_tenant(engine, with_check=False)
    made.append(tenant)
    for table in (ROLLUP, ENDPOINT_ROLLUP, READINGS_TABLE):
        delete_after_run(table, str(tenant.org_id))
    return tenant


async def _another_project(engine: AsyncEngine, tenant: AlertTenant) -> AlertTenant:
    """A second project of the same organization."""
    project_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO projects (id, org_id, slug, name) "
                "VALUES (:id, :org_id, :slug, 'Another')"
            ),
            {"id": project_id, "org_id": tenant.org_id, "slug": f"p-{project_id.hex[:12]}"},
        )
    return replace(tenant, project_id=project_id)


def _stops(administrator: Client, tenant: AlertTenant, *, missing: Sequence[datetime]) -> None:
    """Four weeks of history, and today's traffic but for the minutes `missing`."""
    _seed_four_weeks(administrator, tenant, until=HISTORY_UNTIL)
    today = _span(STOP - timedelta(minutes=30), STOP + timedelta(minutes=40))
    _seed(administrator, tenant, [minute for minute in today if minute not in set(missing)])


def _caught_up(minute: datetime) -> Settled:
    """The pipeline's view when every partition has delivered `minute`."""
    return settled_minute(
        now=minute + SETTLING_DELAY, partition_count=0, marks={}, newest_offsets={}
    )


def _outage() -> list[datetime]:
    return _span(STOP, STOP + OUTAGE - MINUTE)


def _intake_fell_during(outage: Sequence[datetime]) -> Intake:
    """The receiver's count: a tenth of its reference in the outage's minutes."""
    first = STOP - HOLD_LIMIT
    minutes = _span(first, STOP + timedelta(minutes=40))
    return Intake(
        reference=1000.0,
        minutes={minute: 100 if minute in set(outage) else 1000 for minute in minutes},
    )


def _as_of(intake: Intake, now: datetime) -> Intake:
    """What Kafka could say at `now`: whole minutes only."""
    current = now.replace(second=0, microsecond=0)
    return replace(intake, minutes={m: c for m, c in intake.minutes.items() if m < current})


async def _sweep(
    engine: AsyncEngine,
    store: DetectionStore,
    projects: Sequence[AlertTenant],
    minute: datetime,
    intake: Intake | None,
    progress: Settled | None = None,
) -> holds.Decision:
    """One coordinator sweep with the pipeline caught up to `minute`, and each
    project's job for the minute it hands out."""
    found = progress or _caught_up(minute)
    decision = await holds.decide(
        engine, found, None if intake is None else _as_of(intake, found.now)
    )
    assert decision.minute is not None
    for project in projects:
        await judge_project(
            engine,
            store,
            project.project_id,
            settled=decision.minute,
            now=minute,
            trouble=decision.trouble,
        )
    return decision


async def _events(engine: AsyncEngine, kind: str) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            await connection.execute(
                text(
                    """
                    SELECT detail FROM detection_events
                    WHERE kind = :kind
                      AND CAST(detail ->> 'from' AS timestamptz) BETWEEN :first AND :last
                    """
                ),
                {
                    "kind": kind,
                    "first": STOP - timedelta(hours=1),
                    "last": STOP + timedelta(hours=1),
                },
            )
        )


def _events_counted(kind: str) -> float:
    return REGISTRY.get_sample_value(EVENTS_METRIC, {"kind": kind}) or 0.0


async def _refuse(engine: AsyncEngine, minutes: Sequence[datetime]) -> None:
    async with engine.begin() as connection:
        for minute in minutes:
            await connection.execute(
                text(
                    "INSERT INTO intake_refusals (minute, receiver_id, reason, count) "
                    "VALUES (:minute, :receiver, 'broker_unavailable', 9)"
                ),
                {"minute": minute, "receiver": RECEIVER},
            )


# ------------------------------------------------------- AC23: a stopped worker


@pytest.mark.parametrize("behind", [timedelta(minutes=10), timedelta(minutes=3)])
async def test_a_worker_behind_opens_no_finding_and_its_project_reads_data_delayed(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    tenants: list[AlertTenant],
    clean: None,
    behind: timedelta,
) -> None:
    """AC23: a worker stopped for 10 minutes, or one partition held 3 minutes
    behind. The rollup has nothing for the minutes not yet counted, which judged
    would be a stop; the progress holds them unsettled instead."""
    tenant = await _organization(engine, tenants)
    _stops(administrator, tenant, missing=_span(STOP, STOP + behind - MINUTE))
    await _baselines(engine, store, tenant, now=STOP - timedelta(minutes=5))
    for minute in _span(STOP - timedelta(minutes=5), STOP - MINUTE):
        await _sweep(engine, store, [tenant], minute, intake=None)

    for minute in _span(STOP, STOP + behind - MINUTE):
        # The worker counted nothing received since the stop; Kafka holds more.
        stuck = settled_minute(
            now=minute + SETTLING_DELAY,
            partition_count=2,
            marks={0: Mark(5, STOP - timedelta(seconds=1)), 1: Mark(7, minute)},
            newest_offsets={0: 9, 1: 7},
        )
        decision = await _sweep(engine, store, [tenant], minute, intake=None, progress=stuck)
        assert decision.minute is not None and decision.minute < STOP

    assert await _findings(engine, tenant) == []
    judged = (await _state(engine, tenant)).last_judged_minute
    if behind > DELAYED_AFTER:
        assert STOP + behind - judged > DELAYED_AFTER, "the project must read data delayed"
    # The worker catches up and writes what it held; judging resumes on complete data.
    _seed(administrator, tenant, _span(STOP, STOP + behind - MINUTE))
    for minute in _span(STOP + behind, STOP + behind + timedelta(minutes=15)):
        await _sweep(engine, store, [tenant], minute, intake=None)
    assert await _findings(engine, tenant) == []


# --------------------------------------------------- refusals (C5) and the bound


async def test_refused_minutes_are_held_then_judged_with_what_senders_sent_again(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    tenants: list[AlertTenant],
    clean: None,
) -> None:
    """A Kafka outage: the receiver refused exports for 5 minutes, so the rollup
    has nothing for them yet. No project judges them until the refusals stop and
    the pipeline has delivered past them; then they are judged with the batches
    the senders sent again, and nothing opens."""
    tenant = await _organization(engine, tenants)
    refused = _span(STOP, STOP + timedelta(minutes=4))
    _stops(administrator, tenant, missing=refused)
    await _baselines(engine, store, tenant, now=STOP - timedelta(minutes=5))
    await _refuse(engine, refused)

    for minute in _span(STOP - timedelta(minutes=3), refused[-1] - MINUTE):
        decision = await _sweep(engine, store, [tenant], minute, intake=None)
        assert decision.minute == min(minute, STOP - MINUTE)
        assert [hold.reason for hold in decision.holding] == ["refused"]
    assert (await _state(engine, tenant)).last_judged_minute == STOP - MINUTE
    _seed(administrator, tenant, refused)  # the senders' retries, counted late

    for minute in _span(refused[-1], refused[-1] + timedelta(minutes=10)):
        decision = await _sweep(engine, store, [tenant], minute, intake=None)
        assert decision.minute == minute

    assert await _findings(engine, tenant) == []
    readings = store.for_project(await _bound(engine, tenant)).readings(
        start=STOP, end=refused[-1] + MINUTE
    )
    assert [reading.minute for reading in readings] == refused, "every refused minute judged"


async def test_a_hold_lasts_at_most_thirty_minutes_and_its_release_is_told_once(
    engine: AsyncEngine, clean: None
) -> None:
    """Design 2.1, item 1: nothing can hold detection for long. After 30 minutes the
    minutes are judged, one `detection_events` row is written, and the operator's
    counter rises once."""
    refused = _span(STOP, STOP + timedelta(minutes=40))
    await _refuse(engine, refused)
    counted = _events_counted(holds.HOLD_EXPIRED)

    released_at = None
    for minute in _span(STOP + timedelta(minutes=20), STOP + timedelta(minutes=35)):
        found = _caught_up(minute)
        decision = await holds.decide(engine, found, None)
        if decision.minute == STOP - MINUTE:
            assert found.now - STOP <= HOLD_LIMIT
        else:
            assert found.now - STOP > HOLD_LIMIT
            assert decision.minute == minute
            released_at = released_at or minute

    assert released_at is not None
    (event,) = await _events(engine, holds.HOLD_EXPIRED)
    assert event.detail["from"] == STOP.isoformat()
    assert event.detail["reason"] == "refused"
    assert _events_counted(holds.HOLD_EXPIRED) == counted + 1


# --------------------------------------------------- the install-wide guard


async def _organizations_lose_traffic(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    projects: Sequence[AlertTenant],
) -> tuple[list[holds.Decision], dict[uuid.UUID, datetime]]:
    """Every project stops for the outage while the receiver's count falls, and the
    sweep runs from 5 minutes before until 30 after. Returns each sweep's decision,
    and the sweep each project's first finding appeared in."""
    for project in projects:
        _stops(administrator, project, missing=_outage())
        await _baselines(engine, store, project, now=STOP - timedelta(minutes=5))
    intake = _intake_fell_during(_outage())
    assert fallen(intake) == _outage()
    decisions = []
    opened: dict[uuid.UUID, datetime] = {}
    for minute in _span(STOP - timedelta(minutes=5), STOP + timedelta(minutes=30)):
        decisions.append(await _sweep(engine, store, projects, minute, intake))
        for project in projects:
            if project.project_id not in opened and await _findings(engine, project):
                opened[project.project_id] = minute
    return decisions, opened


async def test_five_organizations_losing_traffic_as_the_intake_falls_open_nothing(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    tenants: list[AlertTenant],
    clean: None,
) -> None:
    """The cause is upstream of all of them: no finding, every project held (data
    delayed) while it lasts, one event for the operator, then judging resumes."""
    projects = [await _organization(engine, tenants) for _ in range(5)]
    counted = _events_counted(holds.GUARD_ENGAGED)

    decisions, opened = await _organizations_lose_traffic(engine, store, administrator, projects)

    assert opened == {}
    assert any(d.minute is not None and d.minute < STOP for d in decisions), "held"
    (event,) = await _events(engine, holds.GUARD_ENGAGED)
    assert event.detail["organizations"] >= 5
    assert datetime.fromisoformat(event.detail["from"]) <= STOP
    assert datetime.fromisoformat(event.detail["through"]) == _outage()[-1]
    assert _events_counted(holds.GUARD_ENGAGED) == counted + 1
    assert decisions[-1].minute == STOP + timedelta(minutes=30), "judging resumed"
    for project in projects:
        assert (await _state(engine, project)).last_judged_minute == decisions[-1].minute
    # The held minutes are nexovue's, so no normal learns the drop (design, *The normal*).
    async with engine.connect() as connection:
        binding = await bind_project(connection, projects[0].project_id)
        assert binding is not None
        kept = await project_exclusions(
            connection, binding, [SERVICE], now=STOP + timedelta(hours=1), width=5
        )
    assert all(kept[SERVICE].normal.kept_out(minute) for minute in _outage())


async def test_four_organizations_are_one_short_and_each_is_told(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    tenants: list[AlertTenant],
    clean: None,
) -> None:
    """Below the count the drops are the customers': each opens its critical
    finding, after the short wait for the count, within *time to a finding*."""
    projects = [await _organization(engine, tenants) for _ in range(4)]

    decisions, opened = await _organizations_lose_traffic(engine, store, administrator, projects)

    assert all(d.holding == () for d in decisions), "nothing held"
    assert await _events(engine, holds.GUARD_ENGAGED) == []
    for project in projects:
        (finding,) = await _findings(engine, project)
        assert finding.severity == "critical"
        assert finding.signals[0]["signal"] == "traffic"
        assert opened[project.project_id] - STOP <= TIME_TO_A_FINDING


async def test_one_organization_with_many_projects_is_one_vote(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    tenants: list[AlertTenant],
    clean: None,
) -> None:
    """A hostile tenant cannot switch detection off for everyone by making projects:
    six projects of one organization losing traffic are one vote, and each is told."""
    first = await _organization(engine, tenants)
    projects = [first, *[await _another_project(engine, first) for _ in range(5)]]

    _, opened = await _organizations_lose_traffic(engine, store, administrator, projects)

    assert await _events(engine, holds.GUARD_ENGAGED) == []
    assert set(opened) == {project.project_id for project in projects}


# ------------------------------------------------------- AC24: late spans


async def test_spans_thirty_minutes_late_change_no_judged_reading_or_finding(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    tenants: list[AlertTenant],
    clean: None,
) -> None:
    """AC24, BR5: a judged minute's reading is frozen. A burst of slow requests
    for it arrives 30 minutes later; the rollup takes them, the reading and the
    findings do not move."""
    tenant = await _organization(engine, tenants)
    _stops(administrator, tenant, missing=[])
    await _baselines(engine, store, tenant, now=STOP - timedelta(minutes=5))
    for minute in _span(STOP - timedelta(minutes=5), STOP + timedelta(minutes=30)):
        await _sweep(engine, store, [tenant], minute, intake=None)
    detection = store.for_project(await _bound(engine, tenant))
    (before,) = detection.readings(start=STOP, end=STOP + MINUTE)

    _seed(administrator, tenant, [STOP], slower_by={STOP: 12}, requests=400)
    for minute in _span(STOP + timedelta(minutes=31), STOP + timedelta(minutes=35)):
        await _sweep(engine, store, [tenant], minute, intake=None)

    (after,) = detection.readings(start=STOP, end=STOP + MINUTE)
    assert after == before
    assert await _findings(engine, tenant) == []


# ------------------------------------- AC25: a killed run, one message per channel


async def test_a_run_killed_in_the_sustain_wait_sends_one_message_per_channel(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    tenants: list[AlertTenant],
    clean: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC25, NFR3: each run dies before its commit, the one in the sustain wait and
    the one that would open included, and another runs in its place. One finding,
    and one *opened* on the channel."""
    tenant = await _organization(engine, tenants)
    async with engine.begin() as connection:
        await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Anomalies",
            condition=anomaly_rule(),
            channel_ids=[tenant.owners_channel_id],
        )
    _stops(administrator, tenant, missing=_outage())
    await _baselines(engine, store, tenant, now=STOP - timedelta(minutes=5))

    for minute in _span(STOP - timedelta(minutes=5), STOP + timedelta(minutes=15)):
        # Every run is killed once, the sustain wait's and the opening's included,
        # then another runs in its place.
        monkeypatch.setattr(project_job, "_record_run", _killed)
        with pytest.raises(RuntimeError, match="killed"):
            await _sweep(engine, store, [tenant], minute, intake=None)
        monkeypatch.undo()
        await _sweep(engine, store, [tenant], minute, intake=None)

    (finding,) = await _findings(engine, tenant)
    async with engine.connect() as connection:
        told = (
            await connection.execute(
                text(
                    "SELECT channel_id, count(*) AS sent FROM notifications "
                    "WHERE finding_id = :id AND kind = 'opened' GROUP BY channel_id"
                ),
                {"id": finding.id},
            )
        ).all()
    assert [(row.channel_id, row.sent) for row in told] == [(tenant.owners_channel_id, 1)]


async def _killed(*_: object, **__: object) -> int:
    raise RuntimeError("killed between the readings and the commit")


# ------------------------------------------------- the receiver's count, from Kafka


@pytest.fixture
def intake_topic() -> Iterator[str]:
    topic = f"detection-intake-test-{uuid.uuid4().hex[:12]}"
    admin = AdminClient({"bootstrap.servers": KAFKA_BROKERS})
    admin.create_topics([NewTopic(topic, num_partitions=2, replication_factor=1)])[topic].result(
        KAFKA_TIMEOUT_SECONDS
    )
    try:
        yield topic
    finally:
        admin.delete_topics([topic])[topic].result(KAFKA_TIMEOUT_SECONDS)


def test_the_receiver_s_count_is_read_per_minute_by_receive_time(intake_topic: str) -> None:
    """The pipeline signal: records a minute, by the time the receiver produced them,
    whatever any worker has counted. Here 10 a minute, then 2 for 5 minutes."""
    now = datetime.now(UTC)
    current = now.replace(second=0, microsecond=0)
    horizon = current - HOLD_LIMIT
    quiet = _span(horizon + timedelta(minutes=10), horizon + timedelta(minutes=14))
    producer = Producer({"bootstrap.servers": KAFKA_BROKERS})
    for minute in _span(horizon - REFERENCE_SPAN, current - MINUTE):
        for record in range(2 if minute in quiet else 10):
            produced = minute + timedelta(seconds=5 * record)
            producer.produce(
                intake_topic,
                value=b"spans",
                partition=record % 2,
                timestamp=int(produced.timestamp() * 1000),
            )
    assert producer.flush(KAFKA_TIMEOUT_SECONDS) == 0, "the records were not delivered"

    intake = read_intake(KafkaWatermarks([KAFKA_BROKERS]), intake_topic, now)

    assert intake.reference == 10.0
    assert intake.minutes == {
        minute: 2 if minute in quiet else 10 for minute in _span(horizon, current - MINUTE)
    }
    assert fallen(intake) == quiet


async def _bound(engine: AsyncEngine, tenant: AlertTenant) -> Any:
    async with engine.connect() as connection:
        return await bind_project(connection, tenant.project_id)
