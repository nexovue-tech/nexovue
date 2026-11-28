"""`/slo-readings/{id}/burn`, the target page's chart, against what the job stored (plan
T4.12; spec AC109).

A burn is judged by the real `judge_slos` from rows written to `service_stats_1m`, then
the route's service reads the same rollup for the chart. At `slo_status.evaluated_minute`
its point must equal the rate the job stored for that minute and tier: with a burn
holding, with no tier holding (On track, the slowest tier able to judge), and after the
holding tier changes, when the response names the new tier. With no tier able to judge
there is no point. A minute whose window holds no events is a gap, never 0.

Fails rather than skips when Postgres or ClickHouse is down (tests/helpers/live).
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
import pytest_asyncio
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack
from nexovue_platform import slo
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from nexovue_api.auth.dependencies import Identity, Reader
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.signup.service import provision_tenant
from nexovue_api.slos.readings import service as readings

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
SERVICE: Final = "checkout"
MINUTE: Final = timedelta(minutes=1)
BUSY: Final = 100


def _reachable() -> bool:
    for url, port in ((POSTGRES_DSN, 5432), (CLICKHOUSE_DSN, 8123)):
        parts = urlsplit(url)
        try:
            with socket.create_connection((parts.hostname or "localhost", parts.port or port), 2):
                pass
        except OSError:
            return False
    return True


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_slo_readings_live.py",
    remedy="Postgres and ClickHouse are not reachable — run `just dev`",
)

_MADE: Final[list[uuid.UUID]] = []


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    made = create_async_engine(ASYNC_DSN)
    try:
        yield made
    finally:
        async with made.begin() as connection:
            for org_id in _MADE:
                await connection.execute(
                    text("DELETE FROM organizations WHERE id = :id"), {"id": org_id}
                )
        _MADE.clear()
        await made.dispose()


@pytest_asyncio.fixture
async def clickhouse() -> AsyncIterator[AsyncClient]:
    parts = urlsplit(CLICKHOUSE_DSN)
    client = await clickhouse_connect.get_async_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "",
        password=parts.password or "",
        database=(parts.path or "/").lstrip("/"),
    )
    try:
        yield client
    finally:
        await client.close()


async def _tenant(engine: AsyncEngine) -> tuple[Reader, uuid.UUID, uuid.UUID]:
    """A tenant with one service availability target, 99.9% over 28 days."""
    async with AsyncSession(engine, expire_on_commit=False) as db:
        made = await provision_tenant(
            db,
            organization_name=f"Chart {uuid.uuid4().hex[:6]}",
            email=f"chart-{uuid.uuid4()}@example.test",
            password="correct horse battery staple",
        )
        await db.commit()
    _MADE.append(made.organization.id)
    async with engine.begin() as connection:
        target = await connection.scalar(
            text(
                """
                INSERT INTO slo_targets (org_id, project_id, name, kind, service_name,
                    objective_percent, window_days, upstream_sampling, definition_key,
                    history_read_version, first_event_at)
                VALUES (:org_id, :project_id, 'checkout', 'service_availability', :service,
                    99.9, 28, 'no', :key, 1, now() - interval '30 days')
                RETURNING id
                """
            ),
            {
                "org_id": made.organization.id,
                "project_id": made.project.id,
                "service": SERVICE,
                "key": slo.definition_key(
                    slo.SERVICE_AVAILABILITY,
                    service_name=SERVICE,
                    objective=Decimal("99.9"),
                    window_days=28,
                ),
            },
        )
    reader = Reader(
        org_id=made.organization.id,
        identity=Identity(user=made.user, organization=made.organization),
    )
    assert isinstance(target, uuid.UUID)
    return reader, made.project.id, target


def _write(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    first: datetime,
    count: int,
    failing: dict[datetime, int] | None = None,
    requests: int = BUSY,
) -> None:
    parts = urlsplit(CLICKHOUSE_DSN)
    client = clickhouse_connect.get_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "default",
        password=parts.password or "",
        database=(parts.path or "/nexovue").lstrip("/"),
    )
    delete_after_run("service_stats_1m", str(org_id))
    rows = []
    for offset in range(count):
        minute = first + MINUTE * offset
        failures = (failing or {}).get(minute, 0)
        rows.append([str(org_id), str(project_id), SERVICE, minute, requests, failures])
    client.insert(
        "service_stats_1m",
        rows,
        column_names=[
            "tenant_id",
            "project_id",
            "service_name",
            "minute",
            "request_count",
            "error_count",
        ],
    )


async def _judge(engine: AsyncEngine, project_id: uuid.UUID, settled: datetime) -> None:
    from nexovue_scheduler.slos import job
    from nexovue_scheduler.slos.store import connect_store

    await job.judge_slos(engine, connect_store(READER_DSN), project_id, settled)


async def _stored(engine: AsyncEngine, target: uuid.UUID) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT evaluated_minute, tiers FROM slo_status WHERE slo_id = :id"),
                {"id": target},
            )
        ).one()
    return {"minute": row.evaluated_minute, "tiers": row.tiers}


async def _line(
    engine: AsyncEngine, clickhouse: AsyncClient, reader: Reader, target: uuid.UUID
) -> Any:
    async with AsyncSession(engine) as db:
        return await readings.burn(
            db,
            TenantScopedRepository(clickhouse, tenant_id=str(reader.org_id)),
            reader=reader,
            target_id=target,
        )


def _point_at(line: Any, minute: datetime) -> float | None:
    [point] = [each for each in line.points if each.minute == minute]
    return point.rate  # type: ignore[no-any-return]


def _half_failing(end: datetime) -> dict[datetime, int]:
    return {end - MINUTE * offset: BUSY // 2 for offset in range(5)}


async def test_ac109_the_chart_s_point_equals_the_stored_rate_and_follows_the_tier(
    engine: AsyncEngine, clickhouse: AsyncClient
) -> None:
    reader, project_id, target = await _tenant(engine)
    burning = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(hours=2)
    # Six hours of traffic: older minutes of the chart's day have empty windows.
    _write(
        reader.org_id,
        project_id,
        first=burning - timedelta(hours=6),
        count=6 * 60 + 10,
        failing=_half_failing(burning),
    )

    await _judge(engine, project_id, burning)
    stored = await _stored(engine, target)
    line = await _line(engine, clickhouse, reader, target)
    assert line.tier == stored["tiers"]["chart_tier"] == "fast"
    assert line.evaluated_minute == stored["minute"]
    expected = stored["tiers"]["by_tier"]["fast"]["long_rate"]
    assert _point_at(line, burning) == pytest.approx(expected)
    # A minute whose hour holds no events is a gap, not 0.
    assert _point_at(line, burning - timedelta(hours=20)) is None

    # Five minutes on, the fast tier's short window is clean: the medium tier holds.
    await _judge(engine, project_id, burning + 5 * MINUTE)
    stored = await _stored(engine, target)
    line = await _line(engine, clickhouse, reader, target)
    assert line.tier == stored["tiers"]["chart_tier"] == "medium"
    expected = stored["tiers"]["by_tier"]["medium"]["long_rate"]
    assert _point_at(line, burning + 5 * MINUTE) == pytest.approx(expected)


async def test_ac109_on_track_plots_the_slowest_tier_able_to_judge(
    engine: AsyncEngine, clickhouse: AsyncClient
) -> None:
    reader, project_id, target = await _tenant(engine)
    settled = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(hours=1)
    _write(reader.org_id, project_id, first=settled - timedelta(hours=25), count=25 * 60 + 1)

    await _judge(engine, project_id, settled)
    stored = await _stored(engine, target)
    line = await _line(engine, clickhouse, reader, target)
    assert line.tier == stored["tiers"]["chart_tier"] == "slow"
    assert _point_at(line, settled) == pytest.approx(
        stored["tiers"]["by_tier"]["slow"]["long_rate"]
    )


async def test_ac109_with_no_tier_able_to_judge_there_is_no_point(
    engine: AsyncEngine, clickhouse: AsyncClient
) -> None:
    reader, project_id, target = await _tenant(engine)
    settled = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(hours=1)
    # One request a minute for ten minutes: no tier holds 20 events.
    _write(reader.org_id, project_id, first=settled - 9 * MINUTE, count=10, requests=1)

    await _judge(engine, project_id, settled)
    line = await _line(engine, clickhouse, reader, target)
    assert line.tier is None
    assert line.points == []
