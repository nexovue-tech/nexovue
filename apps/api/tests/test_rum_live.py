"""The web vitals read against real ClickHouse.

The offline suites prove the service reads a p75 out of the array position the
rollup declared. They cannot prove that `quantilesMerge` accepts the levels the
query names, that the tenant filter reaches the statement, or that a state the
Go writer folded is one this query can merge — and those are the three ways the
number on the screen becomes silently wrong rather than absent.

Rows are written under tenant ids this suite owns and deleted on the way out;
ClickHouse has no transaction to roll back.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.rum import service

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

ROLLUP_TABLE: Final = "web_vitals_1m"

# The levels migration 000031 declared. Named here so a test that stopped
# matching the migration fails as a mismatch rather than as a wrong number.
QUANTILE_LEVELS: Final = "0.5, 0.75, 0.95"


def _parts() -> tuple[str, int, str, str, str]:
    url = urlsplit(CLICKHOUSE_DSN)
    return (
        url.hostname or "localhost",
        url.port or 8123,
        url.username or "",
        url.password or "",
        (url.path or "/").lstrip("/"),
    )


def _reachable() -> bool:
    host, port, *_ = _parts()
    try:
        with socket.create_connection((host, port), REACHABILITY_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_rum_live.py",
    remedy=f"ClickHouse is not answering on {CLICKHOUSE_DSN} — start it with `just dev`",
)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    host, port, username, password, database = _parts()
    connection = await clickhouse_connect.get_async_client(
        host=host, port=port, username=username, password=password, database=database
    )
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
async def tenant(client: AsyncClient) -> AsyncIterator[str]:
    """A tenant id nothing else writes to, emptied on the way out."""
    tenant_id = f"test-rum-{uuid.uuid4()}"
    try:
        yield tenant_id
    finally:
        await client.command(
            f"ALTER TABLE {ROLLUP_TABLE} DELETE WHERE tenant_id = %(tenant)s",
            parameters={"tenant": tenant_id},
            settings={"mutations_sync": "2"},
        )


async def _write_readings(
    client: AsyncClient,
    *,
    tenant_id: str,
    project_id: str,
    vital: str,
    values: list[float],
    minute: datetime,
    url_path: str = "/checkout",
) -> None:
    """One rollup row, folded into a quantile state by the server.

    The same `arrayReduce` the Go writer uses, so what this suite reads back is a
    state built the way production builds it rather than one shaped for a test.
    """
    await client.command(
        f"""
        INSERT INTO {ROLLUP_TABLE}
            (tenant_id, derived_retention_days, project_id, vital_name, url_path,
             device_class, country, minute, reading_count, value_quantiles)
        SELECT %(tenant)s, 395, %(project)s, %(vital)s, %(path)s, 'mobile', '',
               %(minute)s, %(count)s,
               arrayReduce('quantilesState({QUANTILE_LEVELS})', %(values)s)
        """,
        parameters={
            "tenant": tenant_id,
            "project": project_id,
            "vital": vital,
            "path": url_path,
            "minute": minute,
            "count": len(values),
            "values": values,
        },
    )


def _window() -> tuple[datetime, datetime]:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    return now - timedelta(hours=1), now + timedelta(minutes=1)


async def test_the_p75_read_back_is_the_percentile_of_what_was_written(
    client: AsyncClient, tenant: str
) -> None:
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    # 100, 200, 300, 400 → p75 is 325 by ClickHouse's interpolating quantiles.
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[100.0, 200.0, 300.0, 400.0],
        minute=minute,
    )

    start, end = _window()
    answer = await service.summarize_web_vitals(
        TenantScopedRepository(client, tenant_id=tenant),
        project_id=project_id,
        from_time=start,
        to_time=end,
    )

    assert len(answer.vitals) == 1
    summary = answer.vitals[0]
    assert summary.name == "lcp"
    assert summary.readings == 4
    assert summary.p75 == pytest.approx(325.0)


async def test_minutes_merge_rather_than_averaging_their_percentiles(
    client: AsyncClient, tenant: str
) -> None:
    """Two minutes of one page, read as one distribution.

    This is what the rollup exists for. Averaging the two minutes' p75s would give
    a different, wrong number, so the merge has to happen in the store.
    """
    project_id = str(uuid.uuid4())
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[100.0, 100.0, 100.0, 100.0],
        minute=now - timedelta(minutes=2),
    )
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[900.0, 900.0, 900.0, 900.0],
        minute=now,
    )

    start, end = _window()
    answer = await service.summarize_web_vitals(
        TenantScopedRepository(client, tenant_id=tenant),
        project_id=project_id,
        from_time=start,
        to_time=end,
    )

    summary = answer.vitals[0]
    assert summary.readings == 8
    # The p75 of eight readings, four at 100 and four at 900, is 900 — not the
    # 500 an average of the two minutes' percentiles would produce.
    assert summary.p75 == pytest.approx(900.0)


async def test_every_vital_comes_back_separately_and_ordered(
    client: AsyncClient, tenant: str
) -> None:
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    for vital, value in (("lcp", 2400.0), ("inp", 180.0), ("cls", 0.08)):
        await _write_readings(
            client,
            tenant_id=tenant,
            project_id=project_id,
            vital=vital,
            values=[value] * 4,
            minute=minute,
        )

    start, end = _window()
    answer = await service.summarize_web_vitals(
        TenantScopedRepository(client, tenant_id=tenant),
        project_id=project_id,
        from_time=start,
        to_time=end,
    )

    assert [summary.name for summary in answer.vitals] == ["cls", "inp", "lcp"]
    # CLS is a ratio, so a column that rounded it to an integer would read zero.
    cls = next(summary for summary in answer.vitals if summary.name == "cls")
    assert cls.p75 == pytest.approx(0.08)


async def test_another_tenants_readings_are_not_visible(client: AsyncClient, tenant: str) -> None:
    """Tenant isolation at the repository layer, which is a Phase 1 invariant."""
    project_id = str(uuid.uuid4())
    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    await _write_readings(
        client,
        tenant_id=tenant,
        project_id=project_id,
        vital="lcp",
        values=[100.0, 200.0, 300.0, 400.0],
        minute=minute,
    )

    start, end = _window()
    # The same project id, read as somebody else. The project is not the boundary.
    answer = await service.summarize_web_vitals(
        TenantScopedRepository(client, tenant_id=f"test-rum-other-{uuid.uuid4()}"),
        project_id=project_id,
        from_time=start,
        to_time=end,
    )
    assert answer.vitals == []


async def test_a_window_with_no_readings_is_an_empty_list_and_not_an_error(
    client: AsyncClient, tenant: str
) -> None:
    answer = await service.summarize_web_vitals(
        TenantScopedRepository(client, tenant_id=tenant),
        project_id=str(uuid.uuid4()),
        from_time=datetime.now(UTC) - timedelta(hours=2),
        to_time=datetime.now(UTC) - timedelta(hours=1),
    )
    assert answer.vitals == []
