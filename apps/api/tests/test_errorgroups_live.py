"""The error group statements against real ClickHouse.

The offline suite serves rows from a fake store that returns a fixed column list
whatever SQL it is handed, so it proves the service's mapping and nothing about
the statement. That gap is not hypothetical: a column added to the grouping
projection but not to the statement that selects out of it passed every offline
test and raised `KeyError: 'release'` on the first real request.

So this suite runs the real statements. Rows are written under a tenant id it
owns and deleted on the way out; ClickHouse has no transaction to roll back.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.errorgroups import service

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

RELEASE: Final = "2026.09.06-1"
EXCEPTION_TYPE: Final = "TypeError"
MESSAGE: Final = "Cannot read properties of undefined (reading 'lines')"
STACKTRACE: Final = (
    "TypeError: Cannot read properties of undefined (reading 'lines')\n"
    "    at t (https://shop.example.com/static/js/checkout.min.js:1:111)"
)


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
    suite="apps/api/tests/test_errorgroups_live.py",
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
    tenant_id = f"test-errors-{uuid.uuid4()}"
    try:
        yield tenant_id
    finally:
        delete_after_run("logs", tenant_id)


async def _write_browser_error(
    client: AsyncClient, *, tenant_id: str, project_id: str, release: str
) -> None:
    """One exception shaped as the browser door writes it.

    The release goes on the resource, which is where the SDK's `serviceVersion`
    puts it — not on the record. A test that put it on the record would pass
    against a statement that reads the wrong place.
    """
    now = datetime.now(UTC)
    resource: dict[str, object] = {"service": {"name": "acme-storefront"}}
    if release:
        resource["service"] = {"name": "acme-storefront", "version": release}

    await client.command(
        """
        INSERT INTO logs (timestamp, received_at, retention_days, tenant_id, project_id,
                          trace_id, span_id, service_name, severity, severity_number,
                          body, body_structured, resource_attrs, log_attrs,
                          exception_type, exception_message, timestamp_source)
        SELECT %(at)s, %(at)s, 30, %(tenant)s, %(project)s,
               repeat('0', 32), repeat('0', 16), 'acme-storefront', 'ERROR', 17,
               %(message)s, '{}', %(resource)s, %(attrs)s,
               %(type)s, %(message)s, 'event'
        """,
        parameters={
            "at": now,
            "tenant": tenant_id,
            "project": project_id,
            "message": MESSAGE,
            "resource": json.dumps(resource),
            "attrs": json.dumps(
                {
                    "exception": {
                        "type": EXCEPTION_TYPE,
                        "message": MESSAGE,
                        "stacktrace": STACKTRACE,
                    }
                }
            ),
            "type": EXCEPTION_TYPE,
        },
    )


def _window() -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    return now - timedelta(hours=1), now + timedelta(minutes=1)


async def _detail(client: AsyncClient, tenant_id: str, project_id: str):  # type: ignore[no-untyped-def]
    start, end = _window()
    groups = await service.list_error_groups(
        TenantScopedRepository(client, tenant_id=tenant_id),
        project_id=project_id,
        service_name=None,
        from_time=start,
        to_time=end,
        limit=50,
    )
    assert groups.groups, "the seeded exception did not group"
    return await service.get_error_group(
        TenantScopedRepository(client, tenant_id=tenant_id),
        project_id=project_id,
        group_id=groups.groups[0].group_id,
        from_time=start,
        to_time=end,
    )


async def test_an_occurrence_carries_the_release_off_the_resource(
    client: AsyncClient, tenant: str
) -> None:
    """The statement, not the mapping. This is the one that caught the KeyError."""
    project_id = str(uuid.uuid4())
    await _write_browser_error(client, tenant_id=tenant, project_id=project_id, release=RELEASE)

    detail = await _detail(client, tenant, project_id)

    assert detail.recent[0].release == RELEASE
    assert detail.recent[0].stacktrace == STACKTRACE


async def test_a_sender_that_stamped_no_version_reports_an_empty_release(
    client: AsyncClient, tenant: str
) -> None:
    """`serviceVersion` is optional in the browser SDK, so this is the common case.

    Empty rather than absent: the screen branches on it to say "no release, so no
    source map" instead of silently not symbolicating.
    """
    project_id = str(uuid.uuid4())
    await _write_browser_error(client, tenant_id=tenant, project_id=project_id, release="")

    detail = await _detail(client, tenant, project_id)

    assert detail.recent[0].release == ""


async def test_another_tenant_reads_none_of_these_errors(client: AsyncClient, tenant: str) -> None:
    """Tenant isolation at the repository layer, which is a Phase 1 invariant."""
    project_id = str(uuid.uuid4())
    await _write_browser_error(client, tenant_id=tenant, project_id=project_id, release=RELEASE)

    start, end = _window()
    groups = await service.list_error_groups(
        TenantScopedRepository(client, tenant_id=f"test-errors-other-{uuid.uuid4()}"),
        project_id=project_id,
        service_name=None,
        from_time=start,
        to_time=end,
        limit=50,
    )
    assert groups.groups == []


async def _write_error_at(
    client: AsyncClient, *, tenant_id: str, project_id: str, at: datetime, trace: str
) -> None:
    """One exception at a chosen instant, carrying trace context so it deduplicates."""
    await client.command(
        """
        INSERT INTO logs (timestamp, received_at, retention_days, tenant_id, project_id,
                          trace_id, span_id, service_name, severity, severity_number,
                          body, body_structured, resource_attrs, log_attrs,
                          exception_type, exception_message, timestamp_source)
        SELECT %(at)s, %(at)s, 30, %(tenant)s, %(project)s,
               %(trace)s, %(span)s, 'acme-storefront', 'ERROR', 17,
               %(message)s, '{}', %(resource)s, %(attrs)s,
               %(type)s, %(message)s, 'event'
        """,
        parameters={
            "at": at,
            "tenant": tenant_id,
            "project": project_id,
            "trace": trace,
            "span": trace[:16],
            "message": MESSAGE,
            "resource": json.dumps({"service": {"name": "acme-storefront"}}),
            "attrs": json.dumps(
                {"exception": {"type": EXCEPTION_TYPE, "message": MESSAGE, "stacktrace": ""}}
            ),
            "type": EXCEPTION_TYPE,
        },
    )


async def test_a_groups_buckets_sum_to_the_occurrences_its_row_claims(
    client: AsyncClient, tenant: str
) -> None:
    """The sparkline and the number beside it must count the same thing.

    Occurrences are deduplicated on trace:span, because one exception reaches us as both
    a span event and a log record. A trend that counted rows instead would draw twice the
    shape the row's own number claims, and nothing on the screen could say which was
    right.
    """
    project_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    window_start, window_end = now - timedelta(hours=2), now + timedelta(minutes=1)

    # Five occurrences spread across the window, each with its own trace context.
    for index in range(5):
        await _write_error_at(
            client,
            tenant_id=tenant,
            project_id=project_id,
            at=now - timedelta(minutes=index * 20),
            trace=f"{index:032x}",
        )

    repository = TenantScopedRepository(client, tenant_id=tenant)
    listed = await service.list_error_groups(
        repository,
        project_id=project_id,
        service_name=None,
        from_time=window_start,
        to_time=window_end,
        limit=10,
    )
    trends = await service.group_trends(
        repository,
        project_id=project_id,
        service_name=None,
        from_time=window_start,
        to_time=window_end,
    )

    assert len(listed.groups) == 1
    group = listed.groups[0]
    assert group.occurrences == 5

    series = next(trend for trend in trends.groups if trend.group_id == group.group_id)
    assert sum(point.occurrences for point in series.points) == group.occurrences
    # Spread over 80 minutes, so the buckets are not all one.
    assert len(series.points) > 1


async def test_a_quiet_bucket_counts_zero_rather_than_going_missing(
    client: AsyncClient, tenant: str
) -> None:
    """Four exceptions in an hour must still draw a shape.

    The statement returns only buckets holding an occurrence, so a sparse group would
    arrive as four points and render as a rule rather than a line. A quiet bucket is a
    real reading here — zero errors happened — and leaving it out claims the group was
    busy throughout when in fact it stopped.
    """
    project_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    window_start, window_end = now - timedelta(hours=1), now + timedelta(minutes=1)

    # Two occurrences at the very start of the window and nothing since.
    for index in range(2):
        await _write_error_at(
            client,
            tenant_id=tenant,
            project_id=project_id,
            at=now - timedelta(minutes=58 - index),
            trace=f"{index + 100:032x}",
        )

    trends = await service.group_trends(
        TenantScopedRepository(client, tenant_id=tenant),
        project_id=project_id,
        service_name=None,
        from_time=window_start,
        to_time=window_end,
    )

    assert len(trends.groups) == 1
    points = trends.groups[0].points

    # A window of buckets, not the two that hold an occurrence. The exact count follows
    # from `rollup.trend_bucket_seconds`, which widens the bucket to keep the point count
    # near sixty — an hour lands around thirty.
    assert len(points) > 20
    assert sum(point.occurrences for point in points) == 2

    # The shape is the whole point: busy at the start, silent since.
    assert sum(point.occurrences for point in points[:5]) == 2
    assert all(point.occurrences == 0 for point in points[5:])
