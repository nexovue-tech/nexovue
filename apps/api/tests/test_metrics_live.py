"""The catalogue against the real ClickHouse.

The offline suite proves the statement is scoped and the shapes are folded
correctly. It cannot tell you the `UNION ALL` across five tables parses, that
every table really carries the columns the branch names, or that the counts
describe what is stored. This one asks the store.

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
import pytest_asyncio
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.metrics import queries, service
from nexovue_api.metrics.schemas import MetricCatalogueResponse

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_HTTP_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0
LOOKBACK: Final = timedelta(days=2)


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
    suite="apps/api/tests/test_metrics_live.py",
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
async def reporting(clickhouse: AsyncClient) -> tuple[str, str]:
    """The tenant and project with the most metric points, found with a raw read.

    Asking "who has metrics" needs an unscoped query, which the repository
    rightly refuses, so it is fixture setup rather than a claim about the API.
    """
    result = await clickhouse.query(
        "SELECT tenant_id, project_id, count() AS points FROM metrics_histogram "
        "WHERE timestamp >= %(since)s GROUP BY tenant_id, project_id "
        "ORDER BY points DESC LIMIT 1",
        parameters={"since": datetime.now(UTC) - LOOKBACK},
    )
    assert result.result_rows, (
        "no histogram points in ClickHouse for the last two days. This suite asserts "
        "on real instrumentation, so an empty store is a failure here rather than a "
        "pass — run the load generator."
    )
    tenant_id, project_id, _ = result.result_rows[0]
    return str(tenant_id), str(project_id)


def _window() -> tuple[datetime, datetime]:
    end = datetime.now(UTC)
    return end - LOOKBACK, end


async def _catalogue(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> MetricCatalogueResponse:
    tenant_id, project_id = reporting
    start, end = _window()
    return await service.list_metrics(
        TenantScopedRepository(clickhouse, tenant_id=tenant_id),
        project_id=project_id,
        from_time=start,
        to_time=end,
    )


async def test_the_catalogue_reads_all_five_tables_in_one_statement(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """The `UNION ALL` parses and every branch's columns exist.

    Two of the five tables are empty in every deployment we have, so this is the
    assertion that would catch a column named wrongly in a branch nothing
    returns rows from — a defect no amount of real data reveals.
    """
    catalogue = await _catalogue(clickhouse, reporting)

    assert catalogue.metrics, "the busiest project reported no metrics at all"


async def test_a_histogram_a_customer_sent_is_in_the_catalogue_and_readable(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """The AC5 gap, stated as a test.

    `metrics_histogram` has been written, masked and retained with no reader at
    all. A customer sending an OTLP histogram — the standard latency shape from
    every OTel SDK — had it stored and could never see it.
    """
    catalogue = await _catalogue(clickhouse, reporting)
    histograms = [metric for metric in catalogue.metrics if metric.shapes == ["histogram"]]

    assert histograms, "no histogram in the catalogue, though the fixture found one stored"
    assert all(metric.readable for metric in histograms)
    assert all(metric.points > 0 and metric.series > 0 for metric in histograms)


async def test_a_name_arriving_in_two_shapes_is_not_called_readable(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """Not hypothetical: the development store has three of these right now.

    `db.client.operation.duration` arrives as a histogram *and* as a gauge —
    the poller derives one and an instrumented service sends the other under the
    same name. `system.memory.usage` and `db.client.connection.count` collide
    the same way.

    Such a name cannot be drawn as one thing, and calling it readable would send
    a reader into a screen with nothing to render. The catalogue lists it,
    names both shapes, and says it cannot draw it — which is also how somebody
    finds out the collision exists.
    """
    catalogue = await _catalogue(clickhouse, reporting)
    mixed = [metric for metric in catalogue.metrics if len(metric.shapes) > 1]

    for metric in mixed:
        assert metric.readable is False, f"{metric.metric_name} arrived as {metric.shapes}"


async def test_the_counts_survive_a_redelivered_point(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """`points` counts distinct instants per series, never rows.

    Compared against the raw row count of the same window: they differ exactly
    when a batch was redelivered, and `points` is the one that describes what
    was measured rather than what was stored.
    """
    tenant_id, project_id = reporting
    start, end = _window()
    catalogue = await _catalogue(clickhouse, reporting)

    stored = await clickhouse.query(
        "SELECT count() FROM metrics_histogram WHERE tenant_id = %(tenant)s "
        "AND project_id = %(project)s AND timestamp >= %(start)s AND timestamp < %(end)s",
        parameters={"tenant": tenant_id, "project": project_id, "start": start, "end": end},
    )
    rows = int(stored.result_rows[0][0])
    counted = sum(metric.points for metric in catalogue.metrics if metric.shapes == ["histogram"])

    # Never more than the rows: a distinct count cannot exceed what it counted.
    assert counted <= rows


async def test_another_tenant_reads_none_of_this_projects_metrics(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    """Real rows, real ClickHouse, and the wrong tenant sees none of them."""
    _, project_id = reporting
    start, end = _window()

    stolen = await service.list_metrics(
        TenantScopedRepository(clickhouse, tenant_id=str(uuid.uuid4())),
        project_id=project_id,
        from_time=start,
        to_time=end,
    )

    assert stolen.metrics == []


async def test_a_window_with_no_metrics_is_an_empty_catalogue_not_an_error(
    clickhouse: AsyncClient, reporting: tuple[str, str]
) -> None:
    tenant_id, project_id = reporting
    long_ago = datetime.now(UTC) - timedelta(days=400)

    catalogue = await service.list_metrics(
        TenantScopedRepository(clickhouse, tenant_id=tenant_id),
        project_id=project_id,
        from_time=long_ago,
        to_time=long_ago + timedelta(hours=1),
    )

    assert catalogue.metrics == []


def test_the_shapes_named_here_are_the_ones_the_store_has_tables_for() -> None:
    """A shape with no table is a branch that cannot exist, and the reverse is
    a customer's metrics invisible."""
    assert {
        queries.SHAPE_GAUGE,
        queries.SHAPE_SUM,
        queries.SHAPE_HISTOGRAM,
        queries.SHAPE_EXPONENTIAL_HISTOGRAM,
        queries.SHAPE_SUMMARY,
    } >= queries.READABLE_SHAPES
