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

The fixture writes its own rows to `endpoint_stats_1m`, under its own tenant, as the
workers' rollup does, so nothing depends on demo traffic.
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
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_findings_cases_live import Team, _client, _reachable, _team, db  # noqa: F401

from nexovue_api.auth.dependencies import get_tenant_repository
from nexovue_api.auth.sessions import Session
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import Dashboard, ServiceLastSeen, User
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
# The latency tolerance AC14 uses, set by T2.8's measurement (below and `status.md`): a
# merge of 30,000 samples over ten stored parts, read 20 times through one route, never
# moved, on ClickHouse 26.3. ClickHouse still documents `quantile` as non-deterministic,
# so a difference here one day means measuring again, not widening this by hand.
LATENCY_TOLERANCE: Final = 0.0
SLOT_WAIT: Final = 5.0
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
    return await _team(db, f"Panels{uuid.uuid4().hex[:8]}")


async def _seed(
    team: Team, service: str, start: datetime, *, minutes: int, per_minute: int
) -> None:
    writer = await _connect(WRITER_DSN)
    try:
        await writer.command(
            FIXTURE_INSERT,
            parameters={
                "tenant": str(team.org_id),
                "project": str(team.project_id),
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
    for quantile in ("p50_ms", "p95_ms", "p99_ms"):
        assert _line(panels["p_latency"], quantile) == pytest.approx(
            [point[quantile] for point in overview["points"]], rel=LATENCY_TOLERANCE, abs=0
        )
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
