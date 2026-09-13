"""A filter must reach the rows the page does not.

Every ranked read here is capped — services at a hundred, error groups at fifty.
A filter applied to what came back can only narrow that page, so the answer to
"show me the failing services" becomes "the failing ones among the first
hundred", and nothing on the screen is able to say so. It is not a visible bug
until a customer is large enough to have the hundred-and-first.

Each test below seeds one deliberately unreachable match: a row that ranks last
under the screen's own ordering, behind more rows than the cap allows. An
unfiltered read must miss it — that assertion is the premise, and without it the
test would pass against a filter that does nothing — and the filtered read must
return it.

These run against the real ClickHouse. A filter that lives in SQL cannot be
proved by a fake that answers regardless of `LIMIT`.
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
import pytest_asyncio
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.errorgroups import service as errors
from nexovue_api.infrastructure import service as infrastructure
from nexovue_api.services import service as services

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_HTTP_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

# Small enough to seed quickly, larger than one so "past the cap" means something.
CAP: Final = 5
PROJECT: Final = "filters-past-the-cap"
RETENTION_DAYS: Final = 30


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
    suite="apps/api/tests/test_filters_reach_past_the_cap.py",
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


@pytest.fixture
def tenant() -> str:
    return f"past-the-cap-{uuid.uuid4()}"


def _repository(client: AsyncClient, tenant_id: str) -> TenantScopedRepository:
    return TenantScopedRepository(client, tenant_id=tenant_id)


def _window() -> tuple[datetime, datetime]:
    end = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    return end - timedelta(hours=1), end


async def _drop(client: AsyncClient, table: str, tenant_id: str) -> None:
    await client.command(
        f"ALTER TABLE {table} DELETE WHERE tenant_id = %(tenant)s",
        parameters={"tenant": tenant_id},
        settings={"mutations_sync": "2"},
    )


# --------------------------------------------------------------------- services


async def _write_service(
    client: AsyncClient,
    *,
    tenant_id: str,
    name: str,
    minute: datetime,
    requests: int,
    errors_count: int,
    latency_ms: float,
) -> None:
    await client.command(
        """
        INSERT INTO endpoint_stats_1m
            (tenant_id, derived_retention_days, project_id, service_name, span_kind,
             span_name, minute, request_count, error_count, latency_quantiles)
        SELECT %(tenant)s, %(retention)s, %(project)s, %(name)s, 'SERVER',
               'GET /', %(minute)s, %(requests)s, %(errors)s,
               arrayReduce('quantilesState(0.5, 0.75, 0.9, 0.95, 0.99)', [%(latency)s])
        """,
        parameters={
            "tenant": tenant_id,
            "retention": RETENTION_DAYS,
            "project": PROJECT,
            "name": name,
            "minute": minute,
            "requests": requests,
            "errors": errors_count,
            "latency": latency_ms,
        },
    )


async def test_the_slowest_service_is_found_even_when_it_fails_for_nobody(
    clickhouse: AsyncClient, tenant: str
) -> None:
    """Ordering is the store's, or the slowest service is unreachable.

    The default ranking leads with failures. A service that is the slowest in the
    project and has never failed therefore sorts last, and re-ordering the page
    in the browser can never reach it — it was cut before the browser saw it.
    """
    start, end = _window()
    minute = start + timedelta(minutes=1)
    try:
        for index in range(CAP):
            await _write_service(
                clickhouse,
                tenant_id=tenant,
                name=f"noisy-{index}",
                minute=minute,
                requests=100,
                errors_count=10 + index,
                latency_ms=20.0,
            )
        await _write_service(
            clickhouse,
            tenant_id=tenant,
            name="slow-and-healthy",
            minute=minute,
            requests=100,
            errors_count=0,
            latency_ms=9000.0,
        )

        repository = _repository(clickhouse, tenant)
        worst = await services.list_services(
            repository, project_id=PROJECT, from_time=start, to_time=end, limit=CAP
        )
        slowest = await services.list_services(
            repository,
            project_id=PROJECT,
            from_time=start,
            to_time=end,
            limit=CAP,
            order="slowest",
        )

        # The premise: the page the browser would have re-sorted does not hold it.
        assert "slow-and-healthy" not in [row.service_name for row in worst.services]
        assert slowest.services[0].service_name == "slow-and-healthy"
    finally:
        await _drop(clickhouse, "endpoint_stats_1m", tenant)


async def test_a_failing_service_past_the_cap_is_returned_when_asked_for(
    clickhouse: AsyncClient, tenant: str
) -> None:
    """ "Only with errors" has to be a predicate, not a pass over the page.

    Here the failing service is the quietest thing in the project, so volume
    keeps it off a page that busy healthy services fill.
    """
    start, end = _window()
    minute = start + timedelta(minutes=1)
    try:
        for index in range(CAP):
            await _write_service(
                clickhouse,
                tenant_id=tenant,
                name=f"busy-{index}",
                minute=minute,
                requests=10_000 + index,
                errors_count=0,
                latency_ms=500.0,
            )
        await _write_service(
            clickhouse,
            tenant_id=tenant,
            name="quiet-but-broken",
            minute=minute,
            requests=1,
            errors_count=1,
            latency_ms=5.0,
        )

        repository = _repository(clickhouse, tenant)
        everything = await services.list_services(
            repository, project_id=PROJECT, from_time=start, to_time=end, limit=CAP
        )
        failing = await services.list_services(
            repository,
            project_id=PROJECT,
            from_time=start,
            to_time=end,
            limit=CAP,
            with_errors=True,
        )

        assert len(everything.services) == CAP
        assert [row.service_name for row in failing.services] == ["quiet-but-broken"]
    finally:
        await _drop(clickhouse, "endpoint_stats_1m", tenant)


# ----------------------------------------------------------------------- errors


async def _write_error(
    client: AsyncClient,
    *,
    tenant_id: str,
    exception_type: str,
    at: datetime,
    occurrences: int,
) -> None:
    """One exception, written the way the error path reads it.

    `exception_type` is a column of its own and is what `_LOG_ERRORS` tests, so
    seeding it only inside the attributes would produce rows the grouping never
    sees.
    """
    for index in range(occurrences):
        await client.command(
            """
            INSERT INTO logs
                (timestamp, received_at, retention_days, tenant_id, project_id,
                 trace_id, span_id, service_name, severity, severity_number, body,
                 exception_type, exception_message)
            SELECT %(at)s, %(at)s, %(retention)s, %(tenant)s, %(project)s,
                   '', '', 'checkout', 'ERROR', 17, %(body)s,
                   %(type)s, 'something went wrong'
            """,
            parameters={
                "at": at + timedelta(milliseconds=index),
                "retention": RETENTION_DAYS,
                "tenant": tenant_id,
                "project": PROJECT,
                "body": f"{exception_type}: something went wrong",
                "type": exception_type,
            },
        )


async def test_a_new_error_quieter_than_the_cap_is_returned_when_asked_for(
    clickhouse: AsyncClient, tenant: str
) -> None:
    """A new incident is usually not yet noisy, which is why this matters.

    The list is ranked by how often each group happened. An exception that
    started two minutes ago has happened twice, so it sorts behind every
    long-running bug — exactly the row "what is new" is asked to find, and
    exactly the row a filter over the page cannot reach.
    """
    start, end = _window()
    try:
        for index in range(CAP):
            await _write_error(
                clickhouse,
                tenant_id=tenant,
                exception_type=f"OldError{index}",
                at=start + timedelta(seconds=1),
                occurrences=3,
            )
        await _write_error(
            clickhouse,
            tenant_id=tenant,
            exception_type="BrandNewError",
            at=end - timedelta(minutes=2),
            occurrences=1,
        )

        repository = _repository(clickhouse, tenant)
        everything = await errors.list_error_groups(
            repository,
            project_id=PROJECT,
            service_name=None,
            from_time=start,
            to_time=end,
            limit=CAP,
        )
        fresh = await errors.list_error_groups(
            repository,
            project_id=PROJECT,
            service_name=None,
            from_time=start,
            to_time=end,
            limit=CAP,
            new_only=True,
        )

        assert len(everything.groups) == CAP
        assert "BrandNewError" not in [group.exception_type for group in everything.groups]
        assert [group.exception_type for group in fresh.groups] == ["BrandNewError"]
        # The same rule decides the filter and the marker the screen draws.
        assert all(group.started_in_window for group in fresh.groups)
    finally:
        await _drop(clickhouse, "logs", tenant)


# --------------------------------------------------------------- infrastructure


async def _write_node(
    client: AsyncClient,
    *,
    tenant_id: str,
    at: datetime,
    resource: str,
    value: float,
) -> None:
    await client.command(
        """
        INSERT INTO metrics_gauge
            (timestamp, start_timestamp, received_at, retention_days, tenant_id,
             project_id, service_name, scope_name, scope_version, metric_name,
             metric_unit, metric_description, resource_attrs, attrs, flags, value)
        SELECT %(at)s, %(at)s, %(at)s, %(retention)s, %(tenant)s, %(project)s,
               'otelcol', '', '', 'system.memory.usage', 'By', '',
               %(resource)s, '{"state":"used"}', 0, %(value)s
        """,
        parameters={
            "at": at,
            "retention": RETENTION_DAYS,
            "tenant": tenant_id,
            "project": PROJECT,
            "resource": resource,
            "value": value,
        },
    )


async def test_a_container_is_returned_when_hosts_would_have_filled_the_answer(
    clickhouse: AsyncClient, tenant: str
) -> None:
    """`kind` narrows the series read, so a container is never crowded out.

    The nodes are ordered by name, so a container named late in the alphabet sits
    behind every host. Filtering in the browser would have had to receive it
    first.
    """
    start, end = _window()
    at = start + timedelta(minutes=1)
    try:
        for index in range(CAP):
            await _write_node(
                clickhouse,
                tenant_id=tenant,
                at=at,
                resource=f'{{"host":{{"name":"aaa-host-{index}"}}}}',
                value=1_000.0,
            )
        # The machine itself, and one container running on it. Both name
        # `zzz-host`, which is what lets a single predicate reach the pair.
        await _write_node(
            clickhouse,
            tenant_id=tenant,
            at=at,
            resource='{"host":{"name":"zzz-host"}}',
            value=3_000.0,
        )
        await _write_node(
            clickhouse,
            tenant_id=tenant,
            at=at,
            resource='{"host":{"name":"zzz-host"},"container":{"name":"zzz-container"}}',
            value=2_000.0,
        )

        repository = _repository(clickhouse, tenant)
        everything = await infrastructure.list_nodes(
            repository, project_id=PROJECT, from_time=start, to_time=end
        )
        containers = await infrastructure.list_nodes(
            repository, project_id=PROJECT, from_time=start, to_time=end, kind="container"
        )

        assert len(everything.nodes) == CAP + 2
        assert [node.container for node in containers.nodes] == ["zzz-container"]

        # `host` reaches the machine and what runs on it in one predicate.
        machine = await infrastructure.list_nodes(
            repository, project_id=PROJECT, from_time=start, to_time=end, host="zzz-host"
        )
        assert {node.kind for node in machine.nodes} == {"host", "container"}
    finally:
        await _drop(clickhouse, "metrics_gauge", tenant)


def test_the_cap_this_suite_seeds_against_is_smaller_than_the_real_ones() -> None:
    """The property is the cap's, not this number's.

    Seeding a hundred and one services per test would prove the same thing and
    take a minute to do it. What has to stay true is that the caps these screens
    really use are larger than the page each test fills.
    """
    assert CAP < services.MAX_SERVICE_LIMIT
    assert CAP < errors.MAX_LIMIT
