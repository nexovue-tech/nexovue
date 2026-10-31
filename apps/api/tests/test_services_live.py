"""Service overview reads against the running stack.

The offline suite proves the code cannot construct a cross-tenant read. This one
proves the statements are ones ClickHouse actually accepts and answers: the
merge functions, the parameter types and the column names are all things a fake
store cannot check, and all three are easy to get plausibly wrong.

It fails when the stack is not up, because a skip here is indistinguishable from
a pass — `live.require_stack` is that rule, and `NEXOVUE_SKIP_LIVE_TESTS=1` is
the deliberate way out.

It reads only. Nothing is written, so nothing is cleaned up.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
import pytest_asyncio
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.services import service

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)

REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

# A tenant this deployment cannot have issued. Reading as it must return nothing
# whatever the table holds.
TENANT_THAT_NEVER_EXISTED: Final = "00000000-0000-0000-0000-000000000000"

# The rollup buckets by minute, so the window has to include the last bucket.
LAST_BUCKET_SLACK: Final = timedelta(minutes=1)
SETTLE_TIME: Final = timedelta(minutes=5)


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or 8123), REACHABILITY_TIMEOUT_SECONDS
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(CLICKHOUSE_DSN),
    suite="the live service overview suite",
    remedy="ClickHouse is not running — start the dev stack with `just dev`",
)


@dataclass(frozen=True, slots=True)
class BusiestProject:
    """The tenant and project with the most rollup rows, and the window holding them.

    Chosen rather than fixed, so the suite runs against whatever the load
    generator last produced instead of against an id someone has to maintain.
    """

    tenant_id: str
    project_id: str
    window_start: datetime
    window_end: datetime


def _settled_before() -> datetime:
    """Rollups older than this have stopped changing, including late spans."""
    return datetime.now(UTC).replace(tzinfo=None, second=0, microsecond=0) - SETTLE_TIME


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
async def busiest(clickhouse: AsyncClient) -> BusiestProject:
    """The project with real traffic in it, and the window that contains it."""
    result = await clickhouse.query(
        "SELECT tenant_id, project_id, min(minute) AS first_minute, max(minute) AS last_minute "
        "FROM endpoint_stats_1m GROUP BY tenant_id, project_id "
        "ORDER BY sum(request_count) DESC LIMIT 1"
    )
    assert result.result_rows, (
        "endpoint_stats_1m is empty — this suite would prove nothing. "
        "Run the load generator, or check that the trace pipeline is writing."
    )

    tenant_id, project_id, first_minute, last_minute = result.result_rows[0]
    return BusiestProject(
        tenant_id=str(tenant_id),
        project_id=str(project_id),
        window_start=first_minute,
        # Stopped short of the minutes still being written. Under live traffic a newest
        # bucket grows between two reads, and the two would disagree by a request or so.
        window_end=min(last_minute + LAST_BUCKET_SLACK, _settled_before()),
    )


def _repository(client: AsyncClient, tenant_id: str) -> TenantScopedRepository:
    return TenantScopedRepository(client, tenant_id=tenant_id)


# ----------------------------------------------------------- the query answers


async def test_the_service_list_comes_back_with_rows_and_readable_latencies(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """Proves the merge functions and parameter types are ones the server accepts."""
    page = await service.list_services(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.DEFAULT_SERVICE_LIMIT,
    )

    assert page.services, "the busiest project returned no services"
    for row in page.services:
        assert row.service_name
        assert row.request_count > 0
        assert row.requests_per_minute > 0
        assert 0.0 <= row.error_rate <= 1.0
        # A merged state that was read as a raw blob would not be ordered.
        assert row.p50_ms <= row.p95_ms <= row.p99_ms


async def test_the_endpoint_list_ranks_the_endpoint_with_the_most_failures_first(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    page = await service.list_endpoints(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        service_name=None,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.DEFAULT_ENDPOINT_LIMIT,
    )

    assert page.endpoints, "the busiest project returned no endpoints"
    failures = [row.error_count for row in page.endpoints]
    assert failures == sorted(failures, reverse=True)


async def test_naming_a_service_returns_that_service_and_no_other(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    every = await service.list_services(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.DEFAULT_SERVICE_LIMIT,
    )
    named = every.services[0].service_name

    page = await service.list_endpoints(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        service_name=named,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.DEFAULT_ENDPOINT_LIMIT,
    )

    assert page.endpoints
    assert {row.service_name for row in page.endpoints} == {named}


async def test_one_service_counts_exactly_as_its_row_in_the_ranking(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """Against real rollup rows, not a fake that could reimplement the narrowing.

    Counts only. `quantiles` is reservoir sampling with a random number generator, so
    `quantilesMerge` over the same states is *not* reproducible between two reads —
    measured live on this stack at p95 4000.8ms then 3412.6ms for one service. Latency
    is checked for presence and ordering instead; asserting equality would make this
    suite flap for a reason that has nothing to do with the narrowing under test.
    """
    page = await service.list_services(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.MAX_SERVICE_LIMIT,
    )
    assert page.services, "the busiest project returned no services"

    for expected in page.services:
        answer = await service.get_service(
            _repository(clickhouse, busiest.tenant_id),
            project_id=busiest.project_id,
            service_name=expected.service_name,
            from_time=busiest.window_start,
            to_time=busiest.window_end,
        )
        one = answer.service
        assert one is not None, f"{expected.service_name} is in the ranking but reads as silent"
        assert one.service_name == expected.service_name
        assert one.request_count == expected.request_count
        assert one.error_count == expected.error_count
        assert one.requests_per_minute == expected.requests_per_minute
        assert one.error_rate == expected.error_rate
        assert one.p50_ms <= one.p95_ms <= one.p99_ms


async def test_a_service_name_nobody_ever_reported_is_silent_rather_than_an_error(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    answer = await service.get_service(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        service_name="a-service-that-never-reported",
        from_time=busiest.window_start,
        to_time=busiest.window_end,
    )

    assert answer.service is None


async def test_the_service_totals_are_the_endpoint_totals_of_the_same_window(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """Both readings come from one table, so a disagreement is a query bug."""
    services = await service.list_services(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.MAX_SERVICE_LIMIT,
    )
    endpoints = await service.list_endpoints(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        service_name=None,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.MAX_ENDPOINT_LIMIT,
    )

    assert sum(row.request_count for row in services.services) == sum(
        row.request_count for row in endpoints.endpoints
    )
    assert sum(row.error_count for row in services.services) == sum(
        row.error_count for row in endpoints.endpoints
    )


# ------------------------------------------------------------------- isolation


async def test_another_tenants_project_reads_as_empty_against_real_data(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """The same project id, a different session tenant, and nothing comes back."""
    page = await service.list_services(
        _repository(clickhouse, TENANT_THAT_NEVER_EXISTED),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.DEFAULT_SERVICE_LIMIT,
    )

    assert page.services == []


async def test_a_window_before_any_telemetry_is_an_empty_list_not_an_error(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """The rollup's own retention starts somewhere; asking earlier is not a fault."""
    page = await service.list_services(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start - timedelta(days=365),
        to_time=busiest.window_start - timedelta(days=364),
        limit=service.DEFAULT_SERVICE_LIMIT,
    )

    assert page.services == []


async def test_the_rows_this_tenant_reads_are_the_rows_the_table_holds_for_it(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """Counted independently of the query under test, so an over-broad read shows."""
    expected = await clickhouse.query(
        "SELECT sum(request_count) FROM endpoint_stats_1m "
        "WHERE tenant_id = {tenant_id:String} AND project_id = {project_id:String} "
        "AND minute >= {from_time:DateTime} AND minute < {to_time:DateTime}",
        parameters={
            "tenant_id": busiest.tenant_id,
            "project_id": busiest.project_id,
            "from_time": busiest.window_start,
            "to_time": busiest.window_end,
        },
    )
    total_in_table = int(expected.result_rows[0][0])

    page = await service.list_services(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.MAX_SERVICE_LIMIT,
    )

    assert total_in_table > 0
    assert sum(row.request_count for row in page.services) == total_in_table


async def test_the_projects_p95_is_a_merge_and_not_an_average_of_its_services(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """The reason the dashboard gets a read of its own.

    A project's p95 is `quantilesMerge` over every stored state in the window.
    The mean of its services' finished p95s is a different number, and on real
    data it is not close: one slow service serving almost no traffic drags the
    average while contributing nothing to the distribution. Measured here at the
    time of writing, an hour of the development project answered 21.8ms merged
    against 3,455ms averaged — a factor of 159.

    A browser composing the dashboard from `/services` would have shown the
    second number, and nothing on the screen could have revealed it was wrong.
    """
    repository = _repository(clickhouse, busiest.tenant_id)
    overview = await service.project_overview(
        repository,
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
    )
    listed = await service.list_services(
        repository,
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=service.MAX_SERVICE_LIMIT,
    )

    assert len(listed.services) > 1, "one service cannot show the difference"
    # The premise: the list is whole, so its sum is the project's real total and
    # the comparison below is against every service rather than a page of them.
    assert overview.totals.service_count == len(listed.services)

    # A read grouped by service answers the same question per service, and a
    # caller taking its first row would pass every assertion about shape. This
    # is what tells the two apart: the total belongs to the project.
    assert overview.totals.request_count == sum(row.request_count for row in listed.services)

    averaged = sum(row.p95_ms for row in listed.services) / len(listed.services)
    assert overview.totals.p95_ms != pytest.approx(averaged, rel=0.01)


async def test_the_projects_totals_are_not_the_sum_of_a_capped_list(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """`uniqExact` over the rollup, not the length of a page.

    The list is capped, so its count is the count of its first page. They agree
    on a small project and stop agreeing at exactly the size where nobody is
    watching for it — which is why the dashboard never asks the list.
    """
    repository = _repository(clickhouse, busiest.tenant_id)
    overview = await service.project_overview(
        repository,
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
    )
    one_page = await service.list_services(
        repository,
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
        limit=1,
    )

    assert len(one_page.services) == 1
    assert overview.totals.service_count > 1
    assert overview.totals.request_count > one_page.services[0].request_count


async def test_every_overview_bucket_carries_all_three_percentiles(
    clickhouse: AsyncClient, busiest: BusiestProject
) -> None:
    """The duration panel is three lines, and a missing one would draw at zero."""
    overview = await service.project_overview(
        _repository(clickhouse, busiest.tenant_id),
        project_id=busiest.project_id,
        from_time=busiest.window_start,
        to_time=busiest.window_end,
    )

    assert overview.points, "the busiest project has no buckets; the rollup is not filling"
    for point in overview.points:
        assert point.p50_ms <= point.p95_ms <= point.p99_ms
