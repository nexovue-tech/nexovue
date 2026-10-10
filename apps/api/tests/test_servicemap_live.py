"""The dependency graph's filters against the real ClickHouse.

**The offline suite could not have caught what this exists for.** Every test of
these two filters asserts that the clause *string* appears in the statement, so
it passes whatever the clause happens to say — and what it said was invalid SQL.
`sum(call_count) AS call_count` puts `call_count` in scope as the sum, so
`HAVING sum(call_count) >= …` resolved to `sum(sum(call_count))` and ClickHouse
answered ILLEGAL_AGGREGATION. Both filters returned a 500 the moment a customer
switched them on, and the map sat on its skeleton for ever.

A statement that is never executed is a string. This one runs all four
combinations.

It fails when the stack is not up rather than skipping, because a skip here is
indistinguishable from a pass. `NEXOVUE_SKIP_LIVE_TESTS=1` opts out on purpose.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
import pytest_asyncio
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.servicemap import service
from nexovue_api.servicemap.schemas import ServiceMapResponse

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_HTTP_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0
LOOKBACK: Final = timedelta(days=2)
LIMIT: Final = 1_000


def _reachable() -> bool:
    parts = urlsplit(CLICKHOUSE_DSN)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or 8123), REACHABILITY_TIMEOUT_SECONDS
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_servicemap_live.py",
    remedy=f"ClickHouse is not answering on {CLICKHOUSE_DSN} — start it with `just dev`",
)


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


@pytest_asyncio.fixture
async def calling(clickhouse: AsyncClient) -> tuple[str, str]:
    """The tenant and project with the most edges, found with a raw read.

    Asking "who has dependencies" needs an unscoped query, which the repository
    rightly refuses, so it is fixture setup rather than a claim about the API.
    """
    result = await clickhouse.query(
        "SELECT tenant_id, project_id, count() AS edges FROM service_edges_1m "
        "WHERE minute >= %(since)s GROUP BY tenant_id, project_id "
        "ORDER BY edges DESC LIMIT 1",
        parameters={"since": datetime.now(UTC) - LOOKBACK},
    )
    assert result.result_rows, (
        "no service edges in ClickHouse for the last two days. This suite asserts on "
        "real instrumentation, so an empty store is a failure here rather than a pass "
        "— run `just gate-seed`, which is what the gate does (two edges, one failing)."
    )
    tenant_id, project_id, _ = result.result_rows[0]
    return str(tenant_id), str(project_id)


async def _map(
    clickhouse: AsyncClient,
    calling: tuple[str, str],
    *,
    min_calls_per_minute: float | None = None,
    failing_only: bool = False,
) -> ServiceMapResponse:
    tenant_id, project_id = calling
    now = datetime.now(UTC)
    return await service.get_service_map(
        TenantScopedRepository(clickhouse, tenant_id=tenant_id),
        project_id=project_id,
        from_time=now - LOOKBACK,
        to_time=now,
        limit=LIMIT,
        min_calls_per_minute=min_calls_per_minute,
        failing_only=failing_only,
    )


@pytest.mark.parametrize(
    ("min_calls_per_minute", "failing_only"),
    [(None, False), (None, True), (1.0, False), (1.0, True)],
)
async def test_every_combination_of_the_two_filters_is_valid_sql(
    clickhouse: AsyncClient,
    calling: tuple[str, str],
    min_calls_per_minute: float | None,
    failing_only: bool,
) -> None:
    """The regression. Both filters raised ILLEGAL_AGGREGATION when switched on,
    and both were covered by tests asserting their clause string appeared."""
    answer = await _map(
        clickhouse, calling, min_calls_per_minute=min_calls_per_minute, failing_only=failing_only
    )

    assert answer.window_start < answer.window_end


async def test_only_failing_returns_edges_and_every_one_of_them_failed(
    clickhouse: AsyncClient, calling: tuple[str, str]
) -> None:
    """Valid SQL that returns everything would pass the test above. This one
    asserts the predicate does what its name says."""
    everything = await _map(clickhouse, calling)
    failing = await _map(clickhouse, calling, failing_only=True)

    if not any(edge.error_count > 0 for edge in everything.edges):
        pytest.skip("nothing is failing in this window, so there is no narrowing to prove")

    assert failing.edges, "edges failed in this window but the filter returned none"
    assert all(edge.error_count > 0 for edge in failing.edges)
    assert len(failing.edges) < len(everything.edges)


async def test_a_volume_floor_drops_the_edges_below_it(
    clickhouse: AsyncClient, calling: tuple[str, str]
) -> None:
    everything = await _map(clickhouse, calling)
    busy = await _map(clickhouse, calling, min_calls_per_minute=1.0)

    assert len(busy.edges) <= len(everything.edges)
    assert all(edge.call_count >= 1 for edge in busy.edges)
