"""The profiles reads against real ClickHouse (F17: FR8, BR6, AC7, AC8, AC12).

The tree suite proves the merge and the prune. Only a real server proves the
statements parse, the tenant and project predicates reach them, the narrowing
fragments bind, and the frame arrays come back in the order the worker wrote.

Rows are written under tenant ids this suite owns and deleted when the run ends
(tests/helpers/clickhouse_cleanup.py); ClickHouse has no transaction to roll back.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.profiles import service

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0
TABLE: Final = "profile_samples"
PROJECT: Final = "01996d5e-9a40-7c01-a000-0000000000f1"


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
    suite="apps/api/tests/test_profiles_live.py",
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


def _tenant() -> str:
    tenant = f"test-profiles-{uuid.uuid4()}"
    delete_after_run(TABLE, tenant)
    return tenant


async def _write(client: AsyncClient, tenant: str, rows: list[dict[str, Any]]) -> None:
    columns = [
        "timestamp", "received_at", "retention_days", "tenant_id", "project_id",
        "service_name", "host_name", "service_version", "environment", "sample_type",
        "sample_unit", "value", "stack_hash", "frame_functions", "frame_files", "frame_lines",
        "frame_binaries", "frame_addresses", "trace_id", "span_id",
    ]  # fmt: skip
    now = datetime.now(UTC)
    data = [
        [
            row.get("at", now - timedelta(minutes=5)),
            row.get("received_at", now),
            row.get("retention_days", 1),
            tenant,
            PROJECT,
            row.get("service", "checkout"),
            row.get("host", "web-1"),
            row.get("version", "1.4.2"),
            row.get("environment", "production"),
            row.get("type", "cpu"),
            "nanoseconds",
            row["value"],
            abs(hash(tuple(row["frames"]))) % (2**63),
            row["frames"],
            [f"{frame}.go" for frame in row["frames"]],
            [1] * len(row["frames"]),
            [""] * len(row["frames"]),
            [0] * len(row["frames"]),
            row.get("trace_id", "0" * 32),
            row.get("span_id", "0" * 16),
        ]
        for row in rows
    ]
    await client.insert(TABLE, data, column_names=columns)


def _window() -> service.Window:
    now = datetime.now(UTC)
    return service.Window(start=now - timedelta(hours=1), end=now + timedelta(minutes=1))


def _repository(client: AsyncClient, tenant: str) -> TenantScopedRepository:
    return TenantScopedRepository(client, tenant_id=tenant)


async def _graph(client: AsyncClient, tenant: str, **narrowing: str) -> Any:
    return await service.flame_graph(
        _repository(client, tenant),
        project_id=PROJECT,
        selection=service.Selection(service="checkout", sample_type="cpu", **narrowing),
        window=_window(),
    )


async def test_a_services_profile_reads_back_as_a_tree_of_its_stacks(client: AsyncClient) -> None:
    tenant = _tenant()
    await _write(
        client,
        tenant,
        [
            {"value": 60, "frames": ["sha256.block", "Cart.Checkout", "http.serve"]},
            {"value": 40, "frames": ["json.Marshal", "Cart.Checkout", "http.serve"]},
        ],
    )

    graph = await _graph(client, tenant)

    assert (graph.total, graph.samples, graph.unit) == (100, 2, "nanoseconds")
    [serve] = graph.root.children
    assert serve.name == "http.serve"
    [checkout] = serve.children
    assert [child.name for child in checkout.children] == ["sha256.block", "json.Marshal"]
    assert graph.functions[0].name == "sha256.block"

    listed = await service.profiled_services(
        _repository(client, tenant), project_id=PROJECT, window=_window()
    )
    assert [(entry.service_name, entry.types[0].sample_type) for entry in listed.services] == [
        ("checkout", "cpu")
    ]


async def test_the_busiest_service_is_listed_first(client: AsyncClient) -> None:
    """A whole-host profiler reports every process; the picker leads with volume."""
    tenant = _tenant()
    await _write(
        client,
        tenant,
        [{"value": 1, "frames": ["idle"], "service": "unknown_service:dockerd"}]
        + [{"value": 1, "frames": [f"busy{i}"], "service": "checkout"} for i in range(3)],
    )

    listed = await service.profiled_services(
        _repository(client, tenant), project_id=PROJECT, window=_window()
    )

    assert [entry.service_name for entry in listed.services] == [
        "checkout",
        "unknown_service:dockerd",
    ]


async def test_another_tenants_profile_is_never_read(client: AsyncClient) -> None:
    """AC7."""
    mine, theirs = _tenant(), _tenant()
    await _write(client, theirs, [{"value": 99, "frames": ["their.secret", "main"]}])

    graph = await _graph(client, mine)
    filters = await service.profile_filters(
        _repository(client, mine),
        project_id=PROJECT,
        selection=service.Selection(service="checkout", sample_type="cpu"),
        window=_window(),
    )

    assert graph.samples == 0
    assert graph.root.children == []
    assert filters.hosts == []


async def test_host_version_and_environment_narrow_the_graph_to_their_subset(
    client: AsyncClient,
) -> None:
    """AC8, and the filter menus offer exactly the values present, busiest first."""
    tenant = _tenant()
    await _write(
        client,
        tenant,
        [
            {"value": 10, "frames": ["old.path", "main"], "version": "1.0.0", "host": "web-1"},
            {"value": 20, "frames": ["new.path", "main"], "version": "2.0.0", "host": "web-2"},
            {"value": 5, "frames": ["new.path", "main"], "version": "2.0.0", "host": "web-2"},
            {"value": 7, "frames": ["staging.path", "main"], "environment": "staging"},
        ],
    )

    by_version = await _graph(client, tenant, version="2.0.0")
    by_host = await _graph(client, tenant, host="web-1")
    by_environment = await _graph(client, tenant, environment="staging")
    filters = await service.profile_filters(
        _repository(client, tenant),
        project_id=PROJECT,
        selection=service.Selection(service="checkout", sample_type="cpu"),
        window=_window(),
    )

    assert by_version.total == 25
    assert by_version.functions[0].name == "new.path"
    assert by_host.total == 17
    assert by_environment.functions[0].name == "staging.path"
    assert [value.value for value in filters.versions] == ["2.0.0", "1.0.0", "1.4.2"]
    assert [value.value for value in filters.hosts] == ["web-1", "web-2"]
    assert {value.value for value in filters.environments} == {"production", "staging"}


async def test_the_timeline_has_every_bucket_and_none_where_nothing_arrived(
    client: AsyncClient,
) -> None:
    tenant = _tenant()
    now = datetime.now(UTC)
    await _write(
        client,
        tenant,
        [
            {"value": 3, "frames": ["main"], "at": now - timedelta(minutes=50)},
            {"value": 4, "frames": ["main"], "at": now - timedelta(minutes=50)},
        ],
    )

    timeline = await service.profile_timeline(
        _repository(client, tenant),
        project_id=PROJECT,
        selection=service.Selection(service="checkout", sample_type="cpu"),
        window=service.Window(start=now - timedelta(hours=1), end=now),
    )

    assert timeline.unit == "nanoseconds"
    assert timeline.bucket_seconds == 60
    measured = [point for point in timeline.points if point.value is not None]
    assert [(point.value, point.samples) for point in measured] == [(7, 2)]
    assert len(timeline.points) >= 60


async def test_a_span_offers_only_its_own_samples_or_says_why_it_has_none(
    client: AsyncClient,
) -> None:
    """AC10: linked samples, and nothing else, make up a span's profile; a profiled
    service with none in the span says so; an unprofiled one says that instead."""
    tenant = _tenant()
    trace, span, other = "a" * 32, "b" * 16, "c" * 16
    await _write(
        client,
        tenant,
        [
            {"value": 5, "frames": ["in.span", "main"], "trace_id": trace, "span_id": span},
            {"value": 9, "frames": ["elsewhere", "main"], "trace_id": trace, "span_id": other},
            {"value": 1, "frames": ["unlinked", "main"]},
        ],
    )
    repository = _repository(client, tenant)
    now = datetime.now(UTC)
    around = service.Window(start=now - timedelta(minutes=6), end=now - timedelta(minutes=4))

    linked = await service.span_profile(
        repository, project_id=PROJECT, service="checkout", trace_id=trace, span_id=span,
        span_window=around,
    )  # fmt: skip
    missed = await service.span_profile(
        repository, project_id=PROJECT, service="checkout", trace_id=trace, span_id="d" * 16,
        span_window=around,
    )  # fmt: skip
    absent = await service.span_profile(
        repository, project_id=PROJECT, service="cart", trace_id=trace, span_id=span,
        span_window=around,
    )  # fmt: skip
    graph = await _graph(client, tenant, trace_id=trace, span_id=span)

    assert (linked.state, linked.linked_samples, linked.types) == ("linked", 1, ["cpu"])
    assert missed.state == "no_samples"
    assert absent.state == "not_profiled"
    assert [function.name for function in graph.functions] == ["in.span", "main"]
    assert graph.total == 5


async def test_two_versions_compare_by_share_and_the_grown_function_ranks_first(
    client: AsyncClient,
) -> None:
    """AC9, against real rows: 2.0.0 is busier overall, but only `sha256.block`'s
    share of the whole doubled."""
    tenant = _tenant()
    await _write(
        client,
        tenant,
        [
            {"value": 20, "frames": ["sha256.block", "main"], "version": "1.0.0"},
            {"value": 80, "frames": ["json.Marshal", "main"], "version": "1.0.0"},
            {"value": 80, "frames": ["sha256.block", "main"], "version": "2.0.0"},
            {"value": 120, "frames": ["json.Marshal", "main"], "version": "2.0.0"},
        ],
    )
    window = _window()

    compared = await service.compare(
        _repository(client, tenant),
        project_id=PROJECT,
        baseline=(service.Selection("checkout", "cpu", version="1.0.0"), window),
        comparison=(service.Selection("checkout", "cpu", version="2.0.0"), window),
    )

    assert (compared.baseline_samples, compared.comparison_samples) == (2, 2)
    assert compared.functions[0].name == "sha256.block"
    assert compared.functions[0].change > 0
    main = compared.root.children[0]
    assert (main.baseline_share, main.comparison_share) == (1.0, 1.0)


async def test_the_same_profile_twice_is_refused_as_a_comparison(client: AsyncClient) -> None:
    same = (service.Selection("checkout", "cpu"), _window())
    with pytest.raises(InvalidRequestError):
        await service.compare(
            _repository(client, _tenant()), project_id=PROJECT, baseline=same, comparison=same
        )


async def test_a_profile_past_its_retention_is_not_read_before_the_ttl_removes_it(
    client: AsyncClient,
) -> None:
    """AC14: a row that expires while stored is gone to readers at once, though the
    TTL removes it only at a later merge. Two rows share one part — ClickHouse drops a
    part the moment all of it has expired, which would prove nothing — and one of them
    expires two seconds after it is written."""
    tenant = _tenant()
    now = datetime.now(UTC)
    expiring = now - timedelta(days=1) + timedelta(seconds=2)
    staying = now - timedelta(days=1) + timedelta(hours=1)
    await _write(
        client,
        tenant,
        [
            {"value": 5, "frames": ["expiring"], "at": expiring, "received_at": expiring},
            {"value": 7, "frames": ["staying"], "at": staying, "received_at": staying},
        ],
    )
    await asyncio.sleep(3)

    graph = await service.flame_graph(
        _repository(client, tenant),
        project_id=PROJECT,
        selection=service.Selection("checkout", "cpu"),
        window=service.Window(start=now - timedelta(days=2), end=now + timedelta(minutes=1)),
    )
    stored = await client.query(
        "SELECT count() FROM profile_samples WHERE tenant_id = {tenant:String}",
        parameters={"tenant": tenant},
    )

    assert stored.result_rows[0][0] == 2, "both rows must be stored for this to prove the read"
    assert [function.name for function in graph.functions] == ["staying"]


async def test_the_newest_profile_is_found_outside_the_window_asked_for(
    client: AsyncClient,
) -> None:
    """The range-empty state points at the nearest range that has profiles (BR6)."""
    tenant = _tenant()
    at = datetime.now(UTC) - timedelta(hours=5)
    await _write(
        client,
        tenant,
        [{"value": 1, "frames": ["main"], "at": at, "received_at": at, "retention_days": 14}],
    )

    latest = await service.latest_profile(
        _repository(client, tenant), project_id=PROJECT, service="checkout"
    )
    other = await service.latest_profile(
        _repository(client, tenant), project_id=PROJECT, service="cart"
    )

    assert latest.last_received is not None
    assert abs((latest.last_received - at).total_seconds()) < 1
    assert other.last_received is None


async def test_an_empty_window_says_it_holds_nothing(client: AsyncClient) -> None:
    """BR6, AC12: no samples is reported as none, never as a graph of zero."""
    graph = await _graph(client, _tenant())

    assert (graph.samples, graph.total, graph.root.children) == (0, 0, [])
