"""The batch read against real Postgres and real ClickHouse (F20, T2.8, T2.12).

AC14 for Step 2's kinds: over a fixed window, a `project_trend` panel's counts equal
`/services/overview`'s points and a `service_trend` panel's equal `/services/trend`'s, to
the digit, and latency within the tolerance T2.8's measurement recorded. T2.8 itself:
a window holding more than 8,192 samples read twice through one route. AC8's read half
(a support view reads and moves no "last opened"). AC21's rule over the rows the usage
meter and the rollup write (the path through ingest is not run here: see
`status.md`). AC84's two cases, with real slots held in the real limiter behind the
real middleware: slots that free just before each 5 s wait ends give a 200 with panels
inside the console's 25 s; a bucket that stays full gives a 429 at the end of its wait,
and the handler never runs.

Step 5 (T5.1a to T5.3): each new kind against the screen it came from, over a fixed
window (AC14): an endpoint's chart sums to its row and equals a one-endpoint service's
trend; a top-nodes chart chooses in the store from the displayed project, each line that
node's own (AC26); a check's chart is `/synthetics/checks/trend`'s for that check, read by
one statement bound to the id its name resolved to, with another project's check
`not_in_project` and a shared name `ambiguous` (AC22, AC95); a statement's chart is
`/dbstats/statements/trend`'s; numbers and tables are their screens' rows, and a compared
number's previous value is the route's over the previous window (T5.2, T5.3).

Step 4 (T4.1, T4.2, T4.9): a `top_services_trend` panel chooses its lines in the store
and from the displayed project alone, each line equal to that service's trend (AC26,
AC14); a `node_trend` panel for a host late in the alphabet, on a fleet past the fleet
read's row cap, draws from one statement bound to that node (AC58); and a live window's
newest bucket, still filling, is marked so a steady line has no dip at its right edge at
1 and 24 hours (AC114).

The fixture writes its own rows to `endpoint_stats_1m` and `metrics_gauge`, under its own
tenant, as the workers do, so nothing depends on demo traffic.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Final
from urllib.parse import urlsplit

import clickhouse_connect
import httpx
import pytest
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_findings_cases_live import Team, _client, _reachable, _team, db  # noqa: F401

from nexovue_api.auth.dependencies import get_tenant_repository
from nexovue_api.auth.sessions import Session
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import Check, Dashboard, ServiceLastSeen, User
from nexovue_api.middleware.ratelimit import SCOPE_ORGANIZATION, SCOPE_SESSION
from nexovue_api.ratelimit.limiter import RateLimiter
from nexovue_api.ratelimit.policy import (
    PANEL_READ_CLIENT_TIMEOUT_SECONDS,
    TELEMETRY_TIER,
    FixedPlanLimits,
)
from nexovue_api.settings import API_PREFIX

READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
WRITER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
# The latency tolerance AC14 uses past 8,192 samples, set by T2.8 (below and `status.md`).
# `quantilesMerge` keeps a reservoir of 8,192; merging more samples than that draws from
# each state, and a read whose stored parts are not yet merged merges them in parallel,
# in whatever order its threads finish, so two reads of one window can disagree. The
# bound is that sampling error: for this fixture's p50 (30,000 samples, 0 to 996 ms) one
# read's standard error is 0.95%, and 20 reads, taken as independent draws (which
# overstates it), span more than 6.65% once in 10,000 runs. Measured: 2.6% over 450
# parallel reads with merges held, 0.6% in CI run 42650457940. p95 and p99 sit tighter,
# so one figure covers all three. At or under 8,192 samples none is dropped, and AC14
# compares to the digit.
LATENCY_TOLERANCE: Final = 0.07
SLOT_WAIT: Final = 5.0
# Every table this suite inserts into, by tenant.
SEEDED_TABLES: Final = (
    "endpoint_stats_1m",
    "metrics_gauge",
    "service_edges_1m",
    "web_vitals_1m",
    "logs",
)
T2_8_READS: Final = 20

require_stack(
    reachable=_reachable(READER_DSN.replace("clickhouse://", "http://"), 8123),
    suite="apps/api/tests/test_dashboard_panels_live.py",
    remedy="ClickHouse is not reachable — run `just dev`",
)

FIXTURE_INSERT: Final = """
INSERT INTO endpoint_stats_1m
    (tenant_id, project_id, service_name, span_kind, span_name, minute,
     request_count, error_count, latency_quantiles)
SELECT
    {tenant:String}, {project:String}, {service:String}, 'SERVER', 'GET /orders',
    toDateTime({start:DateTime}) + toIntervalMinute(intDiv(number, {per_minute:UInt32})) AS minute,
    count(), countIf(number % 20 = 0),
    quantilesState(0.5, 0.95, 0.99)(toFloat64(number % 997))
FROM numbers({total:UInt32})
GROUP BY minute
"""


async def _connect(dsn: str) -> AsyncClient:
    parts = urlsplit(dsn)
    return await clickhouse_connect.get_async_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "",
        password=parts.password or "",
        database=parts.path.lstrip("/"),
    )


@pytest.fixture
async def reader() -> AsyncIterator[AsyncClient]:
    client = await _connect(READER_DSN)
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
async def acme(db: AsyncSession) -> Team:  # noqa: F811
    return _cleaned_after_run(await _team(db, f"Panels{uuid.uuid4().hex[:8]}"))


def _cleaned_after_run(team: Team) -> Team:
    """The team's ClickHouse rows go when the run ends. They were left behind, and a
    reader choosing "the busiest tenant" (`test_dbstats_live`) picked them up."""
    for table in SEEDED_TABLES:
        delete_after_run(table, str(team.org_id))
    return team


async def _seed(
    team: Team,
    service: str,
    start: datetime,
    *,
    minutes: int,
    per_minute: int,
    project: str | None = None,
) -> None:
    writer = await _connect(WRITER_DSN)
    try:
        await writer.command(
            FIXTURE_INSERT,
            parameters={
                "tenant": str(team.org_id),
                "project": project or str(team.project_id),
                "service": service,
                "start": start.replace(tzinfo=None),
                "per_minute": per_minute,
                "total": minutes * per_minute,
            },
        )
    finally:
        await writer.close()


def _hour_ago() -> datetime:
    """A window that ended before the fixture's newest minute: settled, so comparable."""
    return (datetime.now(UTC) - timedelta(hours=3)).replace(second=0, microsecond=0)


def _definition(*panels: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "refresh_seconds": 60,
        "default_range": None,
        "service_picker": False,
        "links": [],
        "panels": list(panels),
    }


def _panel(panel_id: str, kind: str, **settings: Any) -> dict[str, Any]:
    return {
        "id": panel_id,
        "kind": kind,
        "title": None,
        "width": 4,
        "height": "medium",
        "settings": settings,
    }


async def _dashboard(db: AsyncSession, team: Team, *panels: dict[str, Any]) -> Dashboard:  # noqa: F811
    row = Dashboard(
        org_id=team.org_id, title=f"Live {uuid.uuid4().hex[:6]}", definition=_definition(*panels)
    )
    db.add(row)
    await db.flush()
    return row


def _app_client(
    db: AsyncSession,  # noqa: F811
    team: Team,
    person: User,
    reader: AsyncClient,
    *,
    support_view: bool = False,
) -> httpx.AsyncClient:
    client, _ = _client(db, team, person, support_view=support_view)
    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_tenant_repository] = lambda: TenantScopedRepository(
        reader, tenant_id=str(team.org_id)
    )
    return client


# -------------------------------------------------------------------- T2.8


async def test_t2_8_a_merge_of_over_8192_samples_read_twice_differs_within_tolerance(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    """ClickHouse calls `quantile` non-deterministic past its 8,192-sample reservoir, so
    one route read twice can disagree. This records by how much (`status.md`)."""
    start = _hour_ago()
    # One insert a minute, so the window spans ten stored parts, read in parallel.
    for minute in range(10):
        await _seed(
            acme, "checkout", start + timedelta(minutes=minute), minutes=1, per_minute=3_000
        )
    window = f"from={start.isoformat()}&to={(start + timedelta(minutes=10)).isoformat()}"
    path = f"{API_PREFIX}/services/overview?project_id={acme.project_id}&{window}"

    readings = []
    for _ in range(T2_8_READS):
        async with _app_client(db, acme, acme.member, reader) as client:
            readings.append((await client.get(path.replace("+00:00", "Z"))).json()["totals"])

    assert {reading["request_count"] for reading in readings} == {30_000}
    for quantile in ("p50_ms", "p95_ms", "p99_ms"):
        values = [reading[quantile] for reading in readings]
        spread = (max(values) - min(values)) / max(values)
        print(f"T2.8 {quantile}: {values} spread {spread:.5f}")  # noqa: T201
        assert spread <= LATENCY_TOLERANCE


# ------------------------------------------------------------------- AC14


async def test_ac14_panels_equal_the_routes_they_came_from_over_a_fixed_window(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    start = _hour_ago()
    await _seed(acme, "checkout", start, minutes=40, per_minute=50)
    await _seed(acme, "payment", start, minutes=40, per_minute=30)
    dashboard = await _dashboard(
        db,
        acme,
        _panel("p_rate", "project_trend", signal="rate"),
        _panel("p_errors", "project_trend", signal="errors"),
        _panel("p_latency", "project_trend", signal="latency"),
        _panel("p_checkout", "service_trend", service="checkout", signal="rate"),
        _panel("p_checkout_errors", "service_trend", service="checkout", signal="errors"),
    )
    db.add(
        ServiceLastSeen(
            org_id=acme.org_id,
            project_id=acme.project_id,
            service_name="checkout",
            last_seen_at=start,
        )
    )
    await db.flush()
    window = {
        "from": start.isoformat(),
        "to": (start + timedelta(minutes=30)).isoformat(),
        "project_id": str(acme.project_id),
    }

    async with _app_client(db, acme, acme.member, reader) as client:
        batch = (
            await client.get(
                f"{API_PREFIX}/dashboard-panels/read",
                params=window | {"dashboard_id": str(dashboard.id)},
            )
        ).json()
        overview = (await client.get(f"{API_PREFIX}/services/overview", params=window)).json()
        trend = (
            await client.get(
                f"{API_PREFIX}/services/trend", params=window | {"service_name": "checkout"}
            )
        ).json()

    panels = batch["panels"]
    assert {panel["state"] for panel in panels.values()} == {"ok"}
    assert _line(panels["p_rate"], "requests_per_minute") == [
        point["requests_per_minute"] for point in overview["points"]
    ]
    assert _line(panels["p_errors"], "error_rate") == [
        point["error_rate"] for point in overview["points"]
    ]
    # 2,400 samples in the window: under the reservoir, so to the digit (AC14).
    for quantile in ("p50_ms", "p95_ms", "p99_ms"):
        assert _line(panels["p_latency"], quantile) == [
            point[quantile] for point in overview["points"]
        ]
    [checkout] = trend["services"]
    assert _line(panels["p_checkout"], "requests_per_minute") == [
        point["requests_per_minute"] for point in checkout["points"]
    ]
    assert _line(panels["p_checkout_errors"], "error_rate") == [
        point["error_rate"] for point in checkout["points"]
    ]
    assert batch["window_start"] == overview["window_start"]


def _line(panel: dict[str, Any], label: str) -> list[float]:
    [series] = [series for series in panel["series"] if series["label"] == label]
    return [value for _, value in series["points"]]


# ------------------------------------------------------------------ AC21


async def test_ac21_never_reported_reads_not_in_project_and_quiet_reads_empty(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    """The rule over what the meter writes (`service_last_seen`) and the rollup holds."""
    seen = datetime.now(UTC) - timedelta(hours=5)
    await _seed(acme, "quiet", seen.replace(second=0, microsecond=0), minutes=1, per_minute=5)
    db.add(
        ServiceLastSeen(
            org_id=acme.org_id, project_id=acme.project_id, service_name="quiet", last_seen_at=seen
        )
    )
    dashboard = await _dashboard(
        db,
        acme,
        _panel("p_quiet", "service_trend", service="quiet", signal="rate"),
        _panel("p_never", "service_trend", service="never-reported", signal="rate"),
    )

    async with _app_client(db, acme, acme.member, reader) as client:
        batch = (
            await client.get(
                f"{API_PREFIX}/dashboard-panels/read",
                params={
                    "dashboard_id": str(dashboard.id),
                    "project_id": str(acme.project_id),
                    "range": "1h",
                },
            )
        ).json()

    assert batch["panels"]["p_quiet"]["state"] == "empty"
    assert batch["panels"]["p_quiet"]["last_seen_at"] is not None
    assert batch["panels"]["p_never"] == {"state": "not_in_project", "kind": "service_trend"}


# ------------------------------------------------------------------- AC8


async def test_ac8_a_support_view_reads_the_panels_and_moves_no_last_opened(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    dashboard = await _dashboard(db, acme, _panel("p_rate", "project_trend", signal="rate"))
    params = {"dashboard_id": str(dashboard.id), "project_id": str(acme.project_id), "range": "1h"}

    async with _app_client(db, acme, acme.owner, reader, support_view=True) as client:
        viewed = await client.get(f"{API_PREFIX}/dashboard-panels/read", params=params)
    await db.refresh(dashboard)
    assert viewed.status_code == 200
    assert dashboard.last_viewed_at is None

    async with _app_client(db, acme, acme.member, reader) as client:
        assert (
            await client.get(f"{API_PREFIX}/dashboard-panels/read", params=params)
        ).status_code == 200
    await db.refresh(dashboard)
    assert dashboard.last_viewed_at is not None


# --------------------------------------------------------- T3.1, AC135


async def test_ac135_a_draft_reads_what_the_saved_dashboard_reads_and_a_viewer_has_none(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    """`POST /preview` shares the saved read's service: equal panels over one fixed
    window, and only for someone who may edit (FR10)."""
    start = _hour_ago()
    await _seed(acme, "checkout", start, minutes=40, per_minute=50)
    panels = (
        _panel("p_rate", "project_trend", signal="rate"),
        _panel("p_checkout", "service_trend", service="checkout", signal="latency"),
    )
    dashboard = await _dashboard(db, acme, *panels)
    db.add(
        ServiceLastSeen(
            org_id=acme.org_id,
            project_id=acme.project_id,
            service_name="checkout",
            last_seen_at=start,
        )
    )
    await db.flush()
    window = {
        "from": start.isoformat(),
        "to": (start + timedelta(minutes=30)).isoformat(),
        "project_id": str(acme.project_id),
    }
    draft = window | {"definition": _definition(*panels)}

    async with _app_client(db, acme, acme.member, reader) as client:
        saved = (
            await client.get(
                f"{API_PREFIX}/dashboard-panels/read",
                params=window | {"dashboard_id": str(dashboard.id)},
            )
        ).json()
        previewed = await client.post(f"{API_PREFIX}/dashboard-panels/preview", json=draft)
        one = await client.post(
            f"{API_PREFIX}/dashboard-panels/preview", json=draft | {"panels": ["p_checkout"]}
        )
        refused = await client.post(
            f"{API_PREFIX}/dashboard-panels/preview",
            json=draft | {"definition": {**_definition(*panels), "panels": [{"kind": "query"}]}},
        )
    async with _app_client(db, acme, acme.viewer, reader) as client:
        viewer = await client.post(f"{API_PREFIX}/dashboard-panels/preview", json=draft)

    assert previewed.status_code == 200, previewed.text
    assert {panel["state"] for panel in saved["panels"].values()} == {"ok"}
    for panel_id in ("p_rate", "p_checkout"):
        assert previewed.json()["panels"][panel_id]["series"] == saved["panels"][panel_id]["series"]
    assert list(one.json()["panels"]) == ["p_checkout"]
    assert refused.status_code == 422
    assert viewer.status_code == 403


# ------------------------------------------------------------------ AC84


class _Sessions:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def peek(self, token: str) -> Session | None:
        return self._session


def _limited(
    db: AsyncSession,  # noqa: F811
    team: Team,
    reader: AsyncClient,
) -> tuple[httpx.AsyncClient, RateLimiter]:
    """The app behind the real middleware, counting the member in the real limiter."""
    client = _app_client(db, team, team.member, reader)
    app = client._transport.app  # type: ignore[attr-defined]
    limiter = RateLimiter(None, instances=1)  # type: ignore[arg-type]
    app.state.rate_limiter = limiter
    app.state.plan_limits = FixedPlanLimits()
    app.state.resources = SimpleNamespace(
        sessions=_Sessions(
            Session(
                user_id=str(team.member.id), org_id=str(team.org_id), created_at=datetime.now(UTC)
            )
        )
    )
    client.headers["Authorization"] = "Bearer a-session-token"
    return client, limiter


async def _fill(limiter: RateLimiter, scope: str, identity: str, slots: int) -> None:
    for _ in range(slots):
        assert await limiter.acquire_slot(
            pool=TELEMETRY_TIER.quota.name, scope=scope, identity=identity, max_concurrent=slots
        )


def _free_at(limiter: RateLimiter, scope: str, identity: str, seconds: float) -> asyncio.Task[None]:
    async def free() -> None:
        await asyncio.sleep(seconds)
        await limiter.release_slot(pool=TELEMETRY_TIER.quota.name, scope=scope, identity=identity)

    return asyncio.create_task(free())


async def test_ac84a_slots_freeing_before_each_wait_ends_still_answer_with_panels(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    await _seed(acme, "checkout", _hour_ago(), minutes=5, per_minute=5)
    dashboard = await _dashboard(db, acme, _panel("p_rate", "project_trend", signal="rate"))
    client, limiter = _limited(db, acme, reader)
    person, organization = f"user:{acme.member.id}", str(acme.org_id)
    assert TELEMETRY_TIER.max_concurrent and TELEMETRY_TIER.org_max_concurrent
    await _fill(limiter, SCOPE_SESSION, person, TELEMETRY_TIER.max_concurrent)
    await _fill(limiter, SCOPE_ORGANIZATION, organization, TELEMETRY_TIER.org_max_concurrent)
    freeing = [
        _free_at(limiter, SCOPE_SESSION, person, SLOT_WAIT - 0.3),
        _free_at(limiter, SCOPE_ORGANIZATION, organization, 2 * SLOT_WAIT - 0.6),
    ]
    started = time.monotonic()

    async with client:
        answer = await client.get(
            f"{API_PREFIX}/dashboard-panels/read",
            params={
                "dashboard_id": str(dashboard.id),
                "project_id": str(acme.project_id),
                "range": "24h",
            },
            timeout=PANEL_READ_CLIENT_TIMEOUT_SECONDS,
        )
    took = time.monotonic() - started
    await asyncio.gather(*freeing)

    assert answer.status_code == 200, answer.text
    assert 2 * SLOT_WAIT - 1.5 < took < PANEL_READ_CLIENT_TIMEOUT_SECONDS
    assert answer.json()["panels"]["p_rate"]["state"] in {"ok", "empty"}


@pytest.mark.parametrize("full", [SCOPE_SESSION, SCOPE_ORGANIZATION])
async def test_ac84b_a_bucket_that_stays_full_answers_429_and_the_handler_never_runs(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
    full: str,
) -> None:
    dashboard = await _dashboard(db, acme, _panel("p_rate", "project_trend", signal="rate"))
    client, limiter = _limited(db, acme, reader)
    identity = f"user:{acme.member.id}" if full == SCOPE_SESSION else str(acme.org_id)
    slots = (
        TELEMETRY_TIER.max_concurrent
        if full == SCOPE_SESSION
        else TELEMETRY_TIER.org_max_concurrent
    )
    assert slots is not None
    await _fill(limiter, full, identity, slots)
    started = time.monotonic()

    async with client:
        answer = await client.get(
            f"{API_PREFIX}/dashboard-panels/read",
            params={
                "dashboard_id": str(dashboard.id),
                "project_id": str(acme.project_id),
                "range": "1h",
            },
            timeout=PANEL_READ_CLIENT_TIMEOUT_SECONDS,
        )
    took = time.monotonic() - started

    assert answer.status_code == 429
    assert SLOT_WAIT - 0.5 < took < SLOT_WAIT + 2
    stamped = await db.scalar(select(Dashboard.last_viewed_at).where(Dashboard.id == dashboard.id))
    assert stamped is None, "the handler ran"


# ------------------------------------------------------------ T4.1, AC26


async def test_ac26_top_services_chooses_in_the_store_from_the_displayed_project_only(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    """Another project's services fail far more, and none of them is chosen. Each line is
    that service's own trend, and the lines are the services list's first rows (AC14)."""
    start = _hour_ago()
    for rank, service in enumerate(("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")):
        await _seed(acme, service, start, minutes=30, per_minute=(rank + 1) * 20)
    elsewhere = str(uuid.uuid4())
    for noisy in ("noisy-1", "noisy-2", "noisy-3"):
        await _seed(acme, noisy, start, minutes=30, per_minute=2_000, project=elsewhere)
    dashboard = await _dashboard(
        db,
        acme,
        _panel("p_top_errors", "top_services_trend", signal="errors", order="worst", count=3),
        _panel("p_top_rate", "top_services_trend", signal="rate", order="worst", count=3),
        _panel("p_top_five", "top_services_trend", signal="rate", order="slowest", count=5),
    )
    window = {
        "from": start.isoformat(),
        "to": (start + timedelta(minutes=30)).isoformat(),
        "project_id": str(acme.project_id),
    }

    async with _app_client(db, acme, acme.member, reader) as client:
        batch = (
            await client.get(
                f"{API_PREFIX}/dashboard-panels/read",
                params=window | {"dashboard_id": str(dashboard.id)},
            )
        ).json()
        ranked = (await client.get(f"{API_PREFIX}/services", params=window | {"limit": 3})).json()
        trends = {
            name: (
                await client.get(
                    f"{API_PREFIX}/services/trend", params=window | {"service_name": name}
                )
            ).json()["services"][0]["points"]
            for name in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")
        }

    panels = batch["panels"]
    assert {panel["state"] for panel in panels.values()} == {"ok"}
    chosen = {series["service"] for series in panels["p_top_errors"]["series"]}
    assert chosen == {"foxtrot", "echo", "delta"}
    assert chosen == {row["service_name"] for row in ranked["services"]}
    assert len(panels["p_top_five"]["series"]) == 5
    every_line = [series for panel in panels.values() for series in panel["series"]]
    assert not {series["service"] for series in every_line} & {"noisy-1", "noisy-2", "noisy-3"}
    for series in panels["p_top_errors"]["series"]:
        assert series["label"] == "error_rate"
        assert [value for _, value in series["points"]] == [
            point["error_rate"] for point in trends[series["service"]]
        ]
    for series in panels["p_top_rate"]["series"]:
        assert [value for _, value in series["points"]] == [
            point["requests_per_minute"] for point in trends[series["service"]]
        ]


# ------------------------------------------------------------ T4.2, AC58

FLEET_INSERT: Final = """
INSERT INTO metrics_gauge
    (timestamp, start_timestamp, received_at, tenant_id, project_id, service_name,
     scope_name, scope_version, metric_name, metric_unit, metric_description,
     resource_attrs, attrs, flags, value)
SELECT
    toDateTime64({start:DateTime}, 3) + toIntervalMinute(number % {minutes:UInt32}) AS at,
    at, at, {tenant:String}, {project:String}, 'otelcol', '', '',
    'system.cpu.utilization', '1', '',
    CAST(
        concat(
            '{"host.name":"',
            {prefix:String},
            leftPad(toString(intDiv(number, {minutes:UInt32})), 4, '0'),
            '"}'
        ) AS JSON
    ),
    CAST('{"cpu":0,"state":"user"}' AS JSON), 0,
    {value:Float64}
FROM numbers({total:UInt32})
"""


async def _seed_fleet(
    team: Team, start: datetime, *, prefix: str, hosts: int, minutes: int, value: float
) -> None:
    writer = await _connect(WRITER_DSN)
    try:
        await writer.command(
            FLEET_INSERT,
            parameters={
                "start": start.replace(tzinfo=None),
                "minutes": minutes,
                "tenant": str(team.org_id),
                "project": str(team.project_id),
                "prefix": prefix,
                "total": hosts * minutes,
                "value": value,
            },
        )
    finally:
        await writer.close()


class _Recorded(TenantScopedRepository):
    """The tenant's repository, keeping every statement it is asked to run."""

    def __init__(self, client: AsyncClient, tenant_id: str, ran: list[tuple[str, dict[str, Any]]]):
        super().__init__(client, tenant_id=tenant_id)
        self._ran = ran

    async def query(
        self, sql: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self._ran.append((sql, dict(parameters or {})))
        return await super().query(sql, parameters)


async def test_ac58_a_node_late_in_the_alphabet_on_a_fleet_past_the_row_cap_draws(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    from nexovue_api.infrastructure.queries import NODE_TREND_SQL
    from nexovue_api.infrastructure.service import MAX_SERIES

    start = _hour_ago()
    minutes = 60
    fleet = MAX_SERIES // minutes + 10  # one row a host a minute: past the fleet read's cap
    await _seed_fleet(acme, start, prefix="host-", hosts=fleet, minutes=minutes, value=0.5)
    await _seed_fleet(acme, start, prefix="zz-late-", hosts=1, minutes=minutes, value=0.25)
    late = {"host": "zz-late-0000", "container": "", "reporter": "", "reporter_instance": ""}
    first = late | {"host": "host-0000"}
    dashboard = await _dashboard(
        db,
        acme,
        _panel("p_late", "node_trend", node=late, metric="system.cpu.utilization"),
        _panel("p_first", "node_trend", node=first, metric="system.cpu.utilization"),
    )
    window = {
        "from": start.isoformat(),
        "to": (start + timedelta(minutes=minutes)).isoformat(),
        "project_id": str(acme.project_id),
    }
    ran: list[tuple[str, dict[str, Any]]] = []

    async with _app_client(db, acme, acme.member, reader) as client:
        fleet_read = (await client.get(f"{API_PREFIX}/infrastructure/trend", params=window)).json()
        app = client._transport.app  # type: ignore[attr-defined]
        app.dependency_overrides[get_tenant_repository] = lambda: _Recorded(
            reader, str(acme.org_id), ran
        )
        batch = (
            await client.get(
                f"{API_PREFIX}/dashboard-panels/read",
                params=window | {"dashboard_id": str(dashboard.id)},
            )
        ).json()

    # The fleet read is cut alphabetically by host, and the late host is not in it.
    assert fleet_read["truncated"] is True
    assert "zz-late-0000" not in {trend["host"] for trend in fleet_read["trends"]}
    panel = batch["panels"]["p_late"]
    assert panel["state"] == "ok"
    [line] = panel["series"]
    assert line["label"] == "system.cpu.utilization"
    assert [value for _, value in line["points"]] == [0.25] * minutes
    # One statement for each node, bound to it: never the fleet read.
    statements = [parameters for sql, parameters in ran if sql == NODE_TREND_SQL]
    assert len(ran) == len(statements) == 2
    assert {parameters["host"] for parameters in statements} == {"zz-late-0000", "host-0000"}
    # The node the fleet read does reach reads the same line there as here (AC14).
    [route] = [
        trend["points"]
        for trend in fleet_read["trends"]
        if trend["host"] == "host-0000" and trend["metric_name"] == "system.cpu.utilization"
    ]
    assert [value for _, value in batch["panels"]["p_first"]["series"][0]["points"]] == [
        point["value"] for point in route
    ]


# ------------------------------------------------------------ T4.9, AC114

STEADY_PER_MINUTE: Final = 60
LIVE_RANGES: Final = ("1h", "24h")
MINUTE_ROLLOVER_ATTEMPTS: Final = 3


async def test_ac114_a_live_windows_filling_bucket_is_marked_and_no_whole_bucket_dips(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    """A steady service, its current minute half-written as one still arriving is. The
    newest bucket of a live window is marked `incomplete_from`, and every whole bucket
    before it reads the steady rate: drop the marked one, and the right edge is flat."""
    for attempt in range(MINUTE_ROLLOVER_ATTEMPTS):
        minute = datetime.now(UTC).replace(second=0, microsecond=0)
        service = f"steady-{attempt}"
        await _seed(
            acme,
            service,
            minute - timedelta(hours=25),
            minutes=25 * 60,
            per_minute=STEADY_PER_MINUTE,
        )
        await _seed(acme, service, minute, minutes=1, per_minute=STEADY_PER_MINUTE // 2)
        dashboard = await _dashboard(
            db, acme, _panel(f"p_{attempt}", "service_trend", service=service, signal="rate")
        )
        answers = {}
        async with _app_client(db, acme, acme.member, reader) as client:
            for range_id in LIVE_RANGES:
                answers[range_id] = (
                    await client.get(
                        f"{API_PREFIX}/dashboard-panels/read",
                        params={
                            "dashboard_id": str(dashboard.id),
                            "project_id": str(acme.project_id),
                            "range": range_id,
                        },
                    )
                ).json()["panels"][f"p_{attempt}"]
        # A minute that turned over mid-test makes the half-written one whole: try again.
        if datetime.now(UTC).replace(second=0, microsecond=0) == minute:
            break
    else:
        pytest.fail("the minute turned over during every attempt")

    for range_id, panel in answers.items():
        [line] = panel["series"]
        buckets = [datetime.fromisoformat(at) for at, _ in line["points"]]
        rates = [rate for _, rate in line["points"]]
        newest = buckets[-1]
        assert panel["incomplete_from"] is not None, range_id
        assert datetime.fromisoformat(panel["incomplete_from"]) == newest, range_id
        assert newest <= minute < newest + timedelta(seconds=panel["bucket_seconds"]), range_id
        # The marked bucket is the one that would dip; every whole one reads steady. At
        # 24 hours the first bucket starts before the window and is cut by it.
        whole = rates[1:-1] if range_id == "24h" else rates[:-1]
        assert whole and set(whole) == {STEADY_PER_MINUTE}, range_id
        assert rates[-1] < STEADY_PER_MINUTE, range_id


async def test_a_fixed_windows_newest_bucket_is_not_marked(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    start = _hour_ago()
    await _seed(acme, "settled", start, minutes=30, per_minute=STEADY_PER_MINUTE)
    dashboard = await _dashboard(
        db, acme, _panel("p_settled", "service_trend", service="settled", signal="rate")
    )
    async with _app_client(db, acme, acme.member, reader) as client:
        panel = (
            await client.get(
                f"{API_PREFIX}/dashboard-panels/read",
                params={
                    "dashboard_id": str(dashboard.id),
                    "project_id": str(acme.project_id),
                    "from": start.isoformat(),
                    "to": (start + timedelta(minutes=30)).isoformat(),
                },
            )
        ).json()["panels"]["p_settled"]
    assert panel["state"] == "ok"
    assert panel["incomplete_from"] is None


# ------------------------------------------------------------ Step 5 (T5.1, T5.2)

ENDPOINT_INSERT: Final = """
INSERT INTO endpoint_stats_1m
    (tenant_id, project_id, service_name, span_kind, span_name, minute,
     request_count, error_count, latency_quantiles)
SELECT
    {tenant:String}, {project:String}, {service:String}, 'SERVER', {endpoint:String},
    toDateTime({start:DateTime}) + toIntervalMinute(intDiv(number, {per_minute:UInt32})) AS minute,
    count(), countIf(number % 10 = 0),
    quantilesState(0.5, 0.95, 0.99)(toFloat64(number % 311))
FROM numbers({total:UInt32})
GROUP BY minute
"""

# One reading a minute of one metric, `odd` every `every`th minute and `value` otherwise.
GAUGE_INSERT: Final = """
INSERT INTO metrics_gauge
    (timestamp, start_timestamp, received_at, tenant_id, project_id, service_name,
     scope_name, scope_version, metric_name, metric_unit, metric_description,
     resource_attrs, attrs, flags, value)
SELECT
    toDateTime64({start:DateTime}, 3) + toIntervalMinute(number) AS at,
    at, at, {tenant:String}, {project:String}, {service:String}, '', '',
    {metric:String}, '', '',
    CAST({resource:String} AS JSON), CAST({attrs:String} AS JSON), 0,
    if(number % {every:UInt32} = {every:UInt32} - 1, {odd:Float64}, {value:Float64})
FROM numbers({minutes:UInt32})
"""


async def _seed_endpoint(
    team: Team, service: str, endpoint: str, start: datetime, *, minutes: int, per_minute: int
) -> None:
    writer = await _connect(WRITER_DSN)
    try:
        await writer.command(
            ENDPOINT_INSERT,
            parameters={
                "tenant": str(team.org_id),
                "project": str(team.project_id),
                "service": service,
                "endpoint": endpoint,
                "start": start.replace(tzinfo=None),
                "per_minute": per_minute,
                "total": minutes * per_minute,
            },
        )
    finally:
        await writer.close()


async def _seed_gauge(
    team: Team,
    start: datetime,
    *,
    metric: str,
    resource: str,
    attrs: str = "{}",
    minutes: int,
    value: float,
    odd: float | None = None,
    every: int = 1_000_000,
    project: str | None = None,
    service: str = "otelcol",
) -> None:
    writer = await _connect(WRITER_DSN)
    try:
        await writer.command(
            GAUGE_INSERT,
            parameters={
                "start": start.replace(tzinfo=None),
                "tenant": str(team.org_id),
                "project": project or str(team.project_id),
                "service": service,
                "metric": metric,
                "resource": resource,
                "attrs": attrs,
                "minutes": minutes,
                "value": value,
                "odd": value if odd is None else odd,
                "every": every,
            },
        )
    finally:
        await writer.close()


def _fixed(start: datetime, minutes: int, team: Team) -> dict[str, str]:
    return {
        "from": start.isoformat(),
        "to": (start + timedelta(minutes=minutes)).isoformat(),
        "project_id": str(team.project_id),
    }


async def _batch(
    client: httpx.AsyncClient, dashboard: Dashboard, window: dict[str, str]
) -> dict[str, Any]:
    answer = await client.get(
        f"{API_PREFIX}/dashboard-panels/read", params=window | {"dashboard_id": str(dashboard.id)}
    )
    assert answer.status_code == 200, answer.text
    panels: dict[str, Any] = answer.json()["panels"]
    return panels


async def test_ac14_an_endpoint_chart_sums_to_its_row_and_is_its_own_trend(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    """T5.1a. `checkout` serves two endpoints; the chart of one sums, bucket by bucket, to
    that endpoint's row on the services screen, never the other's. `solo` serves one, so
    its endpoint's chart is its service's trend, to the digit."""
    start = _hour_ago()
    await _seed_endpoint(acme, "checkout", "GET /orders", start, minutes=30, per_minute=40)
    await _seed_endpoint(acme, "checkout", "POST /pay", start, minutes=30, per_minute=7)
    await _seed_endpoint(acme, "solo", "GET /orders", start, minutes=30, per_minute=25)
    db.add_all(
        ServiceLastSeen(
            org_id=acme.org_id, project_id=acme.project_id, service_name=name, last_seen_at=start
        )
        for name in ("checkout", "solo")
    )
    endpoint = {"span_kind": "SERVER", "endpoint": "GET /orders"}
    dashboard = await _dashboard(
        db,
        acme,
        _panel("p_rate", "endpoint_trend", service="checkout", signal="rate", **endpoint),
        _panel("p_solo", "endpoint_trend", service="solo", signal="latency", **endpoint),
        _panel("p_solo_errors", "endpoint_trend", service="solo", signal="errors", **endpoint),
    )
    window = _fixed(start, 30, acme)

    async with _app_client(db, acme, acme.member, reader) as client:
        panels = await _batch(client, dashboard, window)
        rows = (
            await client.get(f"{API_PREFIX}/services/checkout/endpoints", params=window)
        ).json()["endpoints"]
        solo = (
            await client.get(
                f"{API_PREFIX}/services/trend", params=window | {"service_name": "solo"}
            )
        ).json()["services"][0]["points"]

    assert {panel["state"] for panel in panels.values()} == {"ok"}
    [orders] = [row for row in rows if row["endpoint"] == "GET /orders"]
    # One-minute buckets over 30 minutes: a bucket's rate is its count.
    assert sum(_line(panels["p_rate"], "requests_per_minute")) == orders["request_count"]
    for quantile in ("p50_ms", "p95_ms", "p99_ms"):
        assert _line(panels["p_solo"], quantile) == [point[quantile] for point in solo]
    assert _line(panels["p_solo_errors"], "error_rate") == [point["error_rate"] for point in solo]


def _host(name: str) -> str:
    return f'{{"host.name":"{name}"}}'


async def test_ac26_ac14_top_nodes_choose_in_the_store_from_this_project_each_its_own_line(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    """T5.1b. Six machines here, three busier ones in another project: the chart of the
    three highest is the three highest here, and each line equals that node's own chart."""
    start = _hour_ago()
    minutes = 20
    for index, value in enumerate((0.1, 0.2, 0.3, 0.4, 0.5, 0.6)):
        await _seed_gauge(
            acme,
            start,
            metric="system.cpu.utilization",
            resource=_host(f"web-{index}"),
            minutes=minutes,
            value=value,
            odd=value / 2,
            every=4,
        )
    elsewhere = str(uuid.uuid4())
    for index in range(3):
        await _seed_gauge(
            acme,
            start,
            metric="system.cpu.utilization",
            resource=_host(f"noisy-{index}"),
            minutes=minutes,
            value=0.99,
            project=elsewhere,
        )
    node = {"host": "web-5", "container": "", "reporter": "", "reporter_instance": ""}
    dashboard = await _dashboard(
        db,
        acme,
        _panel("p_top", "top_nodes_trend", metric="system.cpu.utilization", count=3),
        _panel("p_one", "node_trend", node=node, metric="system.cpu.utilization"),
    )

    async with _app_client(db, acme, acme.member, reader) as client:
        panels = await _batch(client, dashboard, _fixed(start, minutes, acme))

    top = panels["p_top"]
    assert top["state"] == "ok"
    assert [line["node"]["host"] for line in top["series"]] == ["web-3", "web-4", "web-5"]
    [web_5] = [line for line in top["series"] if line["node"]["host"] == "web-5"]
    assert web_5["points"] == panels["p_one"]["series"][0]["points"]


async def _check(db: AsyncSession, team: Team, name: str) -> Check:  # noqa: F811
    check = Check(
        org_id=team.org_id,
        project_id=team.project_id,
        name=name,
        type="api",
        interval_seconds=60,
        config={},
    )
    db.add(check)
    await db.flush()
    return check


async def _seed_check(team: Team, check: Check, start: datetime, *, minutes: int) -> None:
    resource = f'{{"synthetic.check.id":"{check.id}"}}'
    final = '{"synthetic.check.final":"true"}'
    # Down every fourth minute, and timed only when up.
    await _seed_gauge(
        team,
        start,
        metric="synthetic.check.status",
        resource=resource,
        attrs=final,
        minutes=minutes,
        value=1.0,
        odd=0.0,
        every=4,
        service="nexovue-prober",
    )
    await _seed_gauge(
        team,
        start,
        metric="synthetic.check.duration",
        resource=resource,
        attrs=final,
        minutes=minutes,
        value=0.25,
        service="nexovue-prober",
    )


async def test_ac14_ac22_ac95_a_check_chart_is_its_checks_trend_by_one_bound_statement(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    from nexovue_api.synthetics.queries import CHECK_TREND_SQL

    globex = _cleaned_after_run(await _team(db, f"Remote{uuid.uuid4().hex[:8]}"))
    start = _hour_ago()
    login = await _check(db, acme, "Login")
    other = await _check(db, acme, "Search")
    await _check(db, acme, "Twin")
    await _check(db, acme, "Twin")
    remote = await _check(db, globex, "Remote")
    for check in (login, other):
        await _seed_check(acme, check, start, minutes=30)
    await _seed_check(globex, remote, start, minutes=30)
    dashboard = await _dashboard(
        db,
        acme,
        _panel("p_up", "check_trend", check="Login", signal="uptime"),
        _panel("p_time", "check_trend", check="Login", signal="duration"),
        _panel("p_remote", "check_trend", check="Remote", signal="uptime"),
        _panel("p_twin", "check_trend", check="Twin", signal="uptime"),
    )
    window = _fixed(start, 30, acme)
    ran: list[tuple[str, dict[str, Any]]] = []

    async with _app_client(db, acme, acme.member, reader) as client:
        route = (await client.get(f"{API_PREFIX}/synthetics/checks/trend", params=window)).json()
        app = client._transport.app  # type: ignore[attr-defined]
        app.dependency_overrides[get_tenant_repository] = lambda: _Recorded(
            reader, str(acme.org_id), ran
        )
        panels = await _batch(client, dashboard, window)

    [points] = [check["points"] for check in route["checks"] if check["check_id"] == str(login.id)]
    assert _line(panels["p_up"], "uptime_percent") == [
        100.0 * point["successful_runs"] / point["runs"] for point in points if point["runs"]
    ]
    assert _line(panels["p_time"], "mean_duration_ms") == [
        point["mean_duration_ms"] for point in points if point["mean_duration_ms"] is not None
    ]
    assert 0.0 in _line(panels["p_up"], "uptime_percent")
    # Another project's check of that name, and a shared name, read nothing (AC22).
    assert panels["p_remote"] == {"state": "not_in_project", "kind": "check_trend"}
    assert panels["p_twin"] == {"state": "ambiguous", "kind": "check_trend"}
    # One statement, bound to the one check, for both of its panels (AC95).
    assert [(sql, parameters["check_id"]) for sql, parameters in ran] == [
        (CHECK_TREND_SQL, str(login.id))
    ]


async def test_ac14_a_statement_chart_is_its_statements_trend(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    """T5.1d: one hash on one server, never its neighbour's."""
    start = _hour_ago()
    server = '{"server.address":"db-1","server.port":5432}'
    for query_hash, seconds in (("h-orders", 0.04), ("h-users", 0.9)):
        await _seed_gauge(
            acme,
            start,
            metric="db.client.operation.duration",
            resource=server,
            attrs=f'{{"db.query.hash":"{query_hash}","db.namespace":"app"}}',
            minutes=20,
            value=seconds,
            odd=seconds * 3,
            every=5,
            service="checkout",
        )
    dashboard = await _dashboard(
        db,
        acme,
        _panel(
            "p_statement",
            "statement_trend",
            query_hash="h-orders",
            server_address="db-1",
            server_port=5432,
            label="Orders",
        ),
    )
    window = _fixed(start, 20, acme)

    async with _app_client(db, acme, acme.member, reader) as client:
        panels = await _batch(client, dashboard, window)
        route = (await client.get(f"{API_PREFIX}/dbstats/statements/trend", params=window)).json()

    [orders] = [found for found in route["statements"] if found["query_hash"] == "h-orders"]
    assert _line(panels["p_statement"], "mean_ms") == [
        point["mean_ms"] for point in orders["points"]
    ]
    assert max(_line(panels["p_statement"], "mean_ms")) < 900


async def test_ac14_numbers_and_tables_are_their_screens_rows_and_a_compare_its_previous_read(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    """T5.2, T5.3. Over 20 minutes whose previous 20 the data covers: each number is its
    route's, each compared value is its route's over the previous window, and each table
    is its screen's first rows. A window whose previous one is empty compares nothing."""
    start = _hour_ago()
    await _seed(acme, "checkout", start, minutes=40, per_minute=50)
    await _seed(acme, "payment", start, minutes=40, per_minute=30)
    await _seed_endpoint(acme, "checkout", "POST /pay", start, minutes=40, per_minute=9)
    db.add_all(
        ServiceLastSeen(
            org_id=acme.org_id, project_id=acme.project_id, service_name=name, last_seen_at=start
        )
        for name in ("checkout", "payment")
    )
    small = {"height": "small"}
    dashboard = await _dashboard(
        db,
        acme,
        _panel("p_rate", "project_number", measure="requests_per_minute", compare=True) | small,
        _panel("p_services", "project_number", measure="service_count", compare=False) | small,
        _panel(
            "p_checkout", "service_number", service="checkout", measure="error_rate", compare=True
        )
        | small,
        _panel("p_ranked", "services_table", order="worst", rows=1),
        _panel("p_endpoints", "endpoints_table", service="checkout", order="slowest", rows=5),
    )
    window = _fixed(start + timedelta(minutes=20), 20, acme)
    previous = _fixed(start, 20, acme)

    async with _app_client(db, acme, acme.member, reader) as client:
        panels = await _batch(client, dashboard, window)
        early = await _batch(client, dashboard, _fixed(start, 20, acme))
        totals = (await client.get(f"{API_PREFIX}/services/overview", params=window)).json()
        before = (await client.get(f"{API_PREFIX}/services/overview", params=previous)).json()
        checkout = (await client.get(f"{API_PREFIX}/services/checkout", params=window)).json()
        checkout_before = (
            await client.get(f"{API_PREFIX}/services/checkout", params=previous)
        ).json()
        ranked = (await client.get(f"{API_PREFIX}/services", params=window | {"limit": 1})).json()
        endpoints = (
            await client.get(
                f"{API_PREFIX}/services/checkout/endpoints",
                params=window | {"limit": 5, "order": "slowest"},
            )
        ).json()

    assert {panel["state"] for panel in panels.values()} == {"ok"}
    assert panels["p_rate"]["number"] == {
        "measure": "requests_per_minute",
        "value": totals["totals"]["requests_per_minute"],
        "previous": before["totals"]["requests_per_minute"],
        "previous_coverage": 1.0,
    }
    assert panels["p_services"]["number"]["value"] == totals["totals"]["service_count"] == 2
    assert panels["p_checkout"]["number"]["value"] == checkout["service"]["error_rate"]
    assert panels["p_checkout"]["number"]["previous"] == checkout_before["service"]["error_rate"]
    assert panels["p_ranked"]["rows"] == ranked["services"]
    assert panels["p_endpoints"]["rows"] == endpoints["endpoints"]
    # The first 20 minutes have no window before them: a number, and no comparison (AC73).
    assert early["p_rate"]["number"]["previous"] is None
    assert early["p_rate"]["number"]["previous_coverage"] is None


EDGES_INSERT: Final = """
INSERT INTO service_edges_1m
    (tenant_id, project_id, client_service, server_service, edge_kind, minute,
     call_count, error_count, latency_quantiles)
SELECT
    {tenant:String}, {project:String}, 'checkout', callee, 'CLIENT->SERVER',
    toDateTime({start:DateTime}) + toIntervalMinute(number % 10) AS minute,
    count(), countIf(callee = 'payment' AND number % 3 = 0),
    quantilesState(0.5, 0.95, 0.99)(toFloat64(number % 97))
FROM numbers(300)
ARRAY JOIN ['payment', 'inventory'] AS callee
GROUP BY callee, minute
"""

VITALS_INSERT: Final = """
INSERT INTO web_vitals_1m
    (tenant_id, project_id, vital_name, route, url_path, device_class, country, minute,
     reading_count, value_quantiles)
SELECT
    {tenant:String}, {project:String}, vital, '/', '/', device, 'IN',
    toDateTime({start:DateTime}) + toIntervalMinute(number % 10) AS minute,
    count(), quantilesState(0.5, 0.75, 0.95)(toFloat64(number % 50))
FROM numbers(200)
ARRAY JOIN ['LCP', 'CLS'] AS vital, ['mobile', 'desktop'] AS device
GROUP BY vital, device, minute
"""

ERROR_INSERT: Final = """
INSERT INTO logs (timestamp, received_at, retention_days, tenant_id, project_id,
                  trace_id, span_id, service_name, severity, severity_number,
                  body, body_structured, resource_attrs, log_attrs,
                  exception_type, exception_message, timestamp_source)
SELECT toDateTime64({start:DateTime}, 3) + toIntervalSecond(number * 7),
       toDateTime64({start:DateTime}, 3) + toIntervalSecond(number * 7), 30,
       {tenant:String}, {project:String}, repeat('0', 32), repeat('0', 16), 'checkout',
       'ERROR', 17, {message:String}, '{}', {resource:String}, {attrs:String},
       {type:String}, {message:String}, 'event'
FROM numbers({count:UInt32})
"""


async def _seed_rest(team: Team, start: datetime) -> None:
    """The dependency, browser and error rows the remaining tables read."""
    keys = {"tenant": str(team.org_id), "project": str(team.project_id)}
    writer = await _connect(WRITER_DSN)
    try:
        await writer.command(EDGES_INSERT, parameters=keys | {"start": start.replace(tzinfo=None)})
        await writer.command(VITALS_INSERT, parameters=keys | {"start": start.replace(tzinfo=None)})
        for error_type, count in (("TimeoutError", 9), ("KeyError", 4)):
            await writer.command(
                ERROR_INSERT,
                parameters=keys
                | {
                    "start": start.replace(tzinfo=None),
                    "message": f"{error_type} while charging",
                    "type": error_type,
                    "count": count,
                    "resource": '{"service":{"name":"checkout"}}',
                    "attrs": f'{{"exception":{{"type":"{error_type}","message":"x"}}}}',
                },
            )
    finally:
        await writer.close()


async def test_ac14_the_other_tables_are_their_screens_first_rows(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,
) -> None:
    """T5.2: dependencies, web vitals, error groups and slow statements, each its screen's
    own rows over the same window, cut to the panel's count."""
    start = _hour_ago()
    await _seed_rest(acme, start)
    await _seed_gauge(
        acme,
        start,
        metric="db.client.operation.duration",
        resource='{"server.address":"db-1","server.port":5432}',
        attrs='{"db.query.hash":"h-1","db.namespace":"app"}',
        minutes=10,
        value=0.02,
        service="checkout",
    )
    db.add(
        ServiceLastSeen(
            org_id=acme.org_id,
            project_id=acme.project_id,
            service_name="checkout",
            last_seen_at=start,
        )
    )
    dashboard = await _dashboard(
        db,
        acme,
        _panel("p_deps", "dependencies_table", failing_only=False, rows=20),
        _panel("p_failing", "dependencies_table", failing_only=True, rows=20),
        _panel("p_vitals", "web_vitals_table", device_class="mobile"),
        _panel("p_errors", "error_groups_table", service="checkout", rows=1),
        _panel("p_statements", "database_statements_table", rows=20),
    )
    window = _fixed(start, 10, acme)

    async with _app_client(db, acme, acme.member, reader) as client:
        panels = await _batch(client, dashboard, window)

        async def rows(path: str, key: str, **params: Any) -> Any:
            answer = await client.get(f"{API_PREFIX}{path}", params=window | params)
            assert answer.status_code == 200, answer.text
            return answer.json()[key]

        edges = await rows("/service-map", "edges", limit=20)
        failing = await rows("/service-map", "edges", limit=20, failing_only="true")
        vitals = await rows("/rum/web-vitals", "vitals", device_class="mobile")
        groups = await rows("/error-groups", "groups", limit=1, service_name="checkout")
        statements = await rows("/dbstats/statements", "statements", limit=20)

    assert {panel["state"] for panel in panels.values()} == {"ok"}
    assert panels["p_deps"]["rows"] == edges and len(edges) == 2
    assert panels["p_failing"]["rows"] == failing and len(failing) == 1
    assert panels["p_vitals"]["rows"] == vitals and vitals
    assert panels["p_errors"]["rows"] == groups
    assert groups[0]["exception_type"] == "TimeoutError"
    assert panels["p_statements"]["rows"] == statements and statements
