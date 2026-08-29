"""Database-statistics reads against the running ClickHouse (M6 Phase A).

**There is no data behind these endpoints, and this suite says so out loud.**
Door 4b's poller has never been pointed at a real Postgres, so `metrics_gauge`
holds no row under either metric name. What can honestly be proved live is:

1. the statements are valid against the real schema — they parse, they run;
2. the empty result becomes an empty response, not an error and not a sample;
3. the emptiness is about this signal, not about a broken query — the same
   table, read for the same tenant, does return rows for other metrics.

Until a customer database is connected, that is the whole of the live evidence.
The row mapping is exercised offline in `test_dbstats.py` against the shape
`nexovue_scheduler.pollers.envelope` publishes.

It fails when the stack is not up rather than skipping, because a skip here is
indistinguishable from a pass. `NEXOVUE_SKIP_LIVE_TESTS=1` opts out on purpose.
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
from nexovue_api.dbstats import queries, service

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_HTTP_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

LOOKBACK: Final = timedelta(days=2)


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
    suite="apps/api/tests/test_dbstats_live.py",
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
async def tenant_with_metrics(client: AsyncClient) -> tuple[str, str]:
    """A tenant that has *some* gauge readings, so emptiness means one thing.

    Raw client on purpose: asking "who has data" is a question no tenant-scoped
    query can ask, and it is setup rather than a claim about the API.
    """
    result = await client.query(
        """
        SELECT tenant_id, project_id, count() AS readings
        FROM metrics_gauge
        WHERE timestamp >= %(since)s
        GROUP BY tenant_id, project_id
        ORDER BY readings DESC
        LIMIT 1
        """,
        parameters={"since": _since()},
    )
    if not result.result_rows:
        # No gauge data at all: the endpoints are still asserted against a real
        # ClickHouse below, which is the part that can be proved either way.
        return str(uuid.uuid4()), str(uuid.uuid4())
    tenant_id, project_id, _ = result.result_rows[0]
    return str(tenant_id), str(project_id)


def _since() -> datetime:
    return datetime.now(UTC) - LOOKBACK


def _window() -> tuple[datetime, datetime]:
    return _since(), datetime.now(UTC)


async def test_the_poller_has_published_nothing_which_is_why_this_suite_is_thin(
    client: AsyncClient,
) -> None:
    """States the premise as an assertion so it cannot quietly stop being true.

    If this fails, a database has been polled for the first time — and the
    tests below should be replaced with ones that assert on real readings.
    """
    result = await client.query(
        """
        SELECT metric_name, count() AS readings
        FROM metrics_gauge
        WHERE metric_name IN (%(duration)s, %(connections)s)
        GROUP BY metric_name
        """,
        parameters={
            "duration": queries.METRIC_OPERATION_DURATION,
            "connections": queries.METRIC_CONNECTION_COUNT,
        },
    )

    assert result.result_rows == [], (
        "door 4b has published readings for the first time — replace this suite "
        f"with assertions on the real rows: {result.result_rows}"
    )


async def test_slow_statements_answers_with_an_empty_list_against_real_clickhouse(
    client: AsyncClient, tenant_with_metrics: tuple[str, str]
) -> None:
    """The statement is valid against the real schema, and finds nothing."""
    tenant_id, project_id = tenant_with_metrics
    start, end = _window()

    result = await service.slow_statements(
        TenantScopedRepository(client, tenant_id=tenant_id),
        project_id=project_id,
        from_time=start,
        to_time=end,
        limit=service.DEFAULT_STATEMENT_LIMIT,
    )

    assert result.statements == []


async def test_connection_health_answers_with_an_empty_list_against_real_clickhouse(
    client: AsyncClient, tenant_with_metrics: tuple[str, str]
) -> None:
    tenant_id, project_id = tenant_with_metrics
    start, end = _window()

    result = await service.connection_health(
        TenantScopedRepository(client, tenant_id=tenant_id),
        project_id=project_id,
        from_time=start,
        to_time=end,
    )

    assert result.states == []
    assert result.latest_backends_total == 0


async def test_the_emptiness_is_this_signal_and_not_a_query_that_reads_nothing(
    client: AsyncClient, tenant_with_metrics: tuple[str, str]
) -> None:
    """Without this, an endpoint that could never return a row looks identical.

    The same table, the same tenant, the same window: other metrics are there.
    So `[]` above is "door 4b has not run", not "this read is broken".
    """
    tenant_id, _ = tenant_with_metrics
    result = await client.query(
        """
        SELECT count() AS readings
        FROM metrics_gauge
        WHERE tenant_id = %(tenant_id)s AND timestamp >= %(since)s
        """,
        parameters={"tenant_id": tenant_id, "since": _since()},
    )

    assert result.result_rows[0][0] > 0, (
        "no gauge readings at all for this tenant, so the empty database "
        "responses above prove nothing — start the stack and seed it"
    )
