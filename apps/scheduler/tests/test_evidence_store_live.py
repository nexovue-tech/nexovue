"""The evidence job's telemetry reads against real ClickHouse (F3 plan T1.3; AC64, AC100,
AC14's read half, FR51, A62, A63).

Rows are written straight into the tables the reads use, for a tenant of this test's
own, so each case states exactly what is there. Each read is held to its tenant and
project; the edge read prefers the server door and holds its bound inside the
statement; the error read keeps no group seen in the hour before and no message; a
read past its time bound raises, which the job turns into `could_not_read`.

AC14's full arm, spans sent through the real receiver with a browser key, needs the
dev stack's receiver, Kafka and workers, and is not here; see `status.md`.

This suite fails rather than skips when ClickHouse is down; see tests/helpers/live.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import clickhouse_connect
import pytest
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.client import Client
from detection_history import CLICKHOUSE_DSN, READER_DSN
from live import require_stack
from nexovue_platform.evidence import MAX_EDGES_READ
from nexovue_platform.tenant_statements import lowered_read_settings

from nexovue_scheduler.evidence import store as evidence_store
from nexovue_scheduler.evidence.store import EvidenceReader, connect_store


def _reachable() -> bool:
    try:
        clickhouse_connect.get_client(dsn=CLICKHOUSE_DSN).command("SELECT 1")
        return True
    except Exception:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/scheduler/tests/test_evidence_store_live.py",
    remedy="ClickHouse is not reachable — run `just up`",
)

START = (datetime.now(UTC) - timedelta(hours=2)).replace(second=0, microsecond=0)
END = START + timedelta(minutes=10)
SERVICE = "checkout"


@pytest.fixture
def administrator() -> Iterator[Client]:
    client = clickhouse_connect.get_client(dsn=CLICKHOUSE_DSN)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def tenant() -> tuple[str, str]:
    org, project = str(uuid.uuid4()), str(uuid.uuid4())
    for table in (
        "service_edges_1m",
        "service_stats_1m",
        "spans",
        "logs",
        "metrics_gauge",
        "metrics_sum",
    ):
        delete_after_run(table, org)
    return org, project


def reader(org: str, project: str) -> EvidenceReader:
    return connect_store(READER_DSN).for_project(org, project)


def add_edge(
    client: Client,
    tenant: tuple[str, str],
    callee: str,
    *,
    door: str,
    minute: datetime,
    calls: int,
    errors: int = 0,
    latency: float = 20.0,
    attr: str = "",
    caller: str = SERVICE,
) -> None:
    client.command(
        """
        INSERT INTO service_edges_1m (tenant_id, project_id, client_service, server_service,
            server_peer_kind, server_peer_attr, door, edge_kind, minute, call_count, error_count,
            latency_quantiles)
        SELECT %(org)s, %(project)s, %(caller)s, %(callee)s, '', %(attr)s, %(door)s, 'call',
            %(minute)s, %(calls)s, %(errors)s,
            arrayReduce('quantilesState(0.5, 0.95, 0.99)', [toFloat64(%(latency)s)])
        """,
        parameters={
            "org": tenant[0],
            "project": tenant[1],
            "caller": caller,
            "callee": callee,
            "attr": attr,
            "door": door,
            "minute": minute.replace(tzinfo=None),
            "calls": calls,
            "errors": errors,
            "latency": latency,
        },
    )


def add_error(
    client: Client, tenant: tuple[str, str], exception_type: str, message: str, received: datetime
) -> None:
    client.command(
        """
        INSERT INTO spans (tenant_id, project_id, service_name, timestamp, received_at,
            exception_type, exception_message, trace_id, span_id)
        VALUES (%(org)s, %(project)s, %(service)s, %(at)s, %(at)s, %(type)s, %(message)s,
            %(trace)s, %(span)s)
        """,
        parameters={
            "org": tenant[0],
            "project": tenant[1],
            "service": SERVICE,
            "at": received.replace(tzinfo=None),
            "type": exception_type,
            "message": message,
            "trace": uuid.uuid4().hex,
            "span": uuid.uuid4().hex[:16],
        },
    )


def test_calls_are_read_from_the_server_door_when_the_project_has_one(
    administrator: Client, tenant: tuple[str, str]
) -> None:
    """A63: a browser's forged calls to the same callee never reach the rules."""
    for minute in (START - timedelta(minutes=5), START + timedelta(minutes=2)):
        add_edge(
            administrator, tenant, "payments", door="server", minute=minute, calls=40, errors=1
        )
        add_edge(
            administrator, tenant, "payments", door="browser", minute=minute, calls=900, errors=900
        )

    edges = reader(*tenant).edges(callers=[SERVICE], start=START, end=END)

    assert len(edges.edges) == 1
    edge = edges.edges[0]
    assert (edge.callee, edge.calls_during, edge.failures_during, edge.from_browser) == (
        "payments",
        40,
        1,
        False,
    )
    assert edge.p95_during == pytest.approx(20.0)


def test_a_project_with_only_browser_calls_reads_them_marked(
    administrator: Client, tenant: tuple[str, str]
) -> None:
    """FR12: no server door at all, so the browser's edges are read, and say so."""
    add_edge(
        administrator,
        tenant,
        "(api.example)",
        door="browser",
        minute=START,
        calls=50,
        attr="server.address",
    )

    edges = reader(*tenant).edges(callers=[SERVICE], start=START, end=END)

    assert [(e.callee, e.from_browser, e.callee_attr) for e in edges.edges] == [
        ("(api.example)", True, "server.address")
    ]


def test_the_edge_read_stops_at_its_bound_inside_the_statement(
    administrator: Client, tenant: tuple[str, str]
) -> None:
    """FR51, AC94's read: forged callees cannot grow the read."""
    for number in range(MAX_EDGES_READ + 25):
        add_edge(
            administrator,
            tenant,
            f"(forged-{number})",
            door="server",
            minute=START,
            calls=number + 1,
        )

    edges = reader(*tenant).edges(callers=[SERVICE], start=START, end=END)

    assert len(edges.edges) == MAX_EDGES_READ and edges.truncated
    assert edges.edges[0].callee == f"(forged-{MAX_EDGES_READ + 24})"


def test_no_calls_read_as_none_never_as_a_zero_rate(tenant: tuple[str, str]) -> None:
    edges = reader(*tenant).edges(callers=[SERVICE], start=START, end=END)

    assert edges.edges == () and not edges.truncated


def test_traffic_is_requests_a_minute_in_the_lead_in_and_the_hour_before(
    administrator: Client, tenant: tuple[str, str]
) -> None:
    rows = [
        (tenant[0], tenant[1], SERVICE, (START - timedelta(minutes=m)).replace(tzinfo=None), 60)
        for m in range(16, 76)
    ] + [
        (tenant[0], tenant[1], SERVICE, (START - timedelta(minutes=m)).replace(tzinfo=None), 150)
        for m in range(1, 16)
    ]
    administrator.insert(
        "service_stats_1m",
        rows,
        column_names=["tenant_id", "project_id", "service_name", "minute", "request_count"],
    )

    traffic = reader(*tenant).traffic(
        service=SERVICE,
        lead_from=START - timedelta(minutes=15),
        lead_to=START,
        baseline=timedelta(hours=1),
    )

    assert traffic.before_per_minute == pytest.approx(60.0)
    assert traffic.lead_per_minute == pytest.approx(150.0)


def test_a_new_error_group_is_kept_with_a_checked_type_and_no_message(
    administrator: Client, tenant: tuple[str, str]
) -> None:
    """A62, A47, AC95: an old group is left out; a type that is not an identifier is not
    kept; the message never comes back."""
    window_from = START - timedelta(minutes=15)
    add_error(
        administrator, tenant, "OldError", "seen an hour ago", window_from - timedelta(minutes=30)
    )
    add_error(administrator, tenant, "OldError", "seen an hour ago", START)
    add_error(administrator, tenant, "TimeoutError", "user priya-marker-7 not found", START)
    add_error(administrator, tenant, "User Priya Sharma not found", "free text", START)

    groups = reader(*tenant).new_error_groups(window_from=window_from, window_to=END)

    types = sorted((g.exception_type or "-") for g in groups)
    assert types == ["-", "TimeoutError"]
    assert all("priya" not in repr(group).lower() for group in groups)


def test_another_projects_rows_are_never_read(
    administrator: Client, tenant: tuple[str, str]
) -> None:
    """NFR5: the reader binds the tenant and the project."""
    add_edge(administrator, tenant, "payments", door="server", minute=START, calls=40)

    other = reader(tenant[0], str(uuid.uuid4())).edges(callers=[SERVICE], start=START, end=END)
    stranger = reader(str(uuid.uuid4()), tenant[1]).edges(callers=[SERVICE], start=START, end=END)

    assert other.edges == () and stranger.edges == ()


def test_a_high_disk_on_a_named_host_is_read_and_a_nameless_one_is_not(
    administrator: Client, tenant: tuple[str, str]
) -> None:
    """AC100's read: 95% disk with `host.name`; and a project with none."""
    at = (START + timedelta(minutes=1)).replace(tzinfo=None)
    for state, value in (("used", 95.0), ("free", 5.0)):
        administrator.command(
            """
            INSERT INTO metrics_sum (tenant_id, project_id, service_name, metric_name, timestamp,
                received_at, resource_attrs, attrs, value)
            VALUES (%(org)s, %(project)s, 'hostmetrics', 'system.filesystem.usage', %(at)s, %(at)s,
                %(resource)s, %(attrs)s, %(value)s)
            """,
            parameters={
                "org": tenant[0],
                "project": tenant[1],
                "at": at,
                "resource": '{"host": {"name": "db-1"}}',
                "attrs": f'{{"device": "/dev/sda1", "mountpoint": "/", "state": "{state}"}}',
                "value": value,
            },
        )

    hosts = reader(*tenant).hosts(start=START, end=END)
    nameless = reader(tenant[0], str(uuid.uuid4())).hosts(start=START, end=END)

    assert [(h.host, h.measure, round(h.utilization, 2)) for h in hosts.readings] == [
        ("db-1", "disk", 0.95)
    ]
    assert hosts.names_present and not nameless.names_present


def test_a_read_past_its_time_bound_raises_rather_than_reading_as_none(
    tenant: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC64: a timeout is an error the job writes as `could_not_read`, never "none"."""
    monkeypatch.setattr(
        evidence_store,
        "EVIDENCE_READ_SETTINGS",
        {**lowered_read_settings(max_execution_time=1), "max_execution_time": 0.001},
    )
    started = time.monotonic()

    with pytest.raises(Exception):  # noqa: B017  the driver's own timeout error
        reader(*tenant).new_error_groups(window_from=START - timedelta(days=30), window_to=END)
    assert time.monotonic() - started < 10
