"""Infrastructure reads against real ClickHouse.

The offline suite proves the statements carry a tenant predicate and the
roll-ups read the semantic conventions correctly. This proves ClickHouse agrees:
that the statements parse, that the JSON paths into `resource_attrs` and `attrs`
resolve, that the parameter types bind, and that the numbers come back out the
way the roll-up expects.

**Why this suite writes rows.** No customer has ever sent a `system.*` metric to
this database — a distinct on `metric_name` across all five metric tables on
2026-08-30 returned only synthetic-check, demo-app and our own derived counters.
So there is nothing here to read, and a suite that only asserted "empty" would
prove the statement runs and nothing about what it returns. It therefore writes
a small fleet under a tenant id no organization has, reads it back through the
real query, and removes it again.

What it writes it removes: a mutation scoped to that tenant id, which is the
first column of the sort key, so ClickHouse skips every part that cannot hold
it. The rows also carry `retention_days = 1`, so the table's TTL removes them
within a day even if the mutation never runs.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import SplitResult, urlsplit

import clickhouse_connect
import pytest
from clickhouse_cleanup import delete_after_run
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantFilterMissingError, TenantScopedRepository
from nexovue_api.infrastructure import conventions, service

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)

PROJECT: Final = "live-infrastructure-project"
HOST: Final = "live-probe-host"
CONTAINER: Final = "live-probe-container"

# A resource that names no machine, which is what every host metric in this
# store actually looks like. Two instances of one service, as a restart produces.
REPORTER: Final = "live-probe-reporter"
FIRST_INSTANCE: Final = "instance-one"
SECOND_INSTANCE: Final = "instance-two"

# The second collector reporting about the *same named host*, under a different
# service resource. It must not split the host or double its readings.
SECOND_HOST_REPORTER: Final = "live-probe-second-agent"

# A third process of that service, whose last reading landed long enough before
# the window closed that it has plainly stopped reporting.
SILENT_INSTANCE: Final = "instance-that-went-quiet"

METRIC_TABLES: Final = ("metrics_gauge", "metrics_sum")

# One day of retention, so the table's TTL removes anything the mutation misses.
PROBE_RETENTION_DAYS: Final = 1

# How far apart the fleet's two observations sit, which is what a rate divides by.
WINDOW_SECONDS: Final = 100

# How long before the others the silent instance stopped. Well past the five
# intervals its own two points imply, and still inside the read window.
SILENCE_SECONDS: Final = 480

# The disk this host reports, its only partition, and a genuinely second disk.
# `/proc/diskstats` lists all three, and the first two carry the same bytes.
DISK: Final = "vda"
DISK_PARTITION: Final = "vda1"
SECOND_DISK: Final = "vdb"

# The volume behind `/`, mounted a second time at `/bind` — the same bytes under
# two mountpoints, which is what the Collector's scraper emits for a bind mount.
ROOT_DEVICE: Final = "/dev/vda1"
DATA_DEVICE: Final = "/dev/vdb1"


def _dsn() -> SplitResult:
    return urlsplit(CLICKHOUSE_DSN)


def _reachable() -> bool:
    parts = _dsn()
    try:
        with socket.create_connection((parts.hostname or "", parts.port or 8123), timeout=2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="the live infrastructure suite",
    remedy="ClickHouse is not running — start it with `just dev`",
)


@pytest.fixture
async def clickhouse() -> AsyncIterator[Any]:
    parts = _dsn()
    client = await clickhouse_connect.get_async_client(
        host=parts.hostname,
        port=parts.port,
        username=parts.username or "",
        password=parts.password or "",
        database=parts.path.lstrip("/") or "nexovue",
    )
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
async def fleet(clickhouse: Any) -> AsyncIterator[str]:
    """A host and a container reporting under a tenant id nobody owns."""
    tenant_id = str(uuid.uuid4())
    end = datetime.now(UTC).replace(microsecond=0)
    start = end - timedelta(seconds=WINDOW_SECONDS)

    await clickhouse.command(_insert("metrics_gauge", _gauge_rows(tenant_id, start, end)))
    await clickhouse.command(_insert("metrics_sum", _sum_rows(tenant_id, start, end)))
    await clickhouse.command(_insert("metrics_gauge", _reporter_rows(tenant_id, start, end)))
    try:
        yield tenant_id
    finally:
        for table in METRIC_TABLES:
            delete_after_run(table, tenant_id)


def _window() -> tuple[datetime, datetime]:
    """Wide enough to hold the fleet whatever the clock did while it was written.

    It has to reach back past the silent instance's *first* point as well as its
    last, or the read would see one point, measure no cadence, and prove the
    fallback rather than the measured rule.
    """
    end = datetime.now(UTC) + timedelta(minutes=1)
    return end - timedelta(minutes=30), end


def _repository(client: Any, tenant_id: str) -> TenantScopedRepository:
    return TenantScopedRepository(client, tenant_id=tenant_id)


# ------------------------------------------------------------------- the reads


async def test_a_reporting_host_and_container_come_back_rolled_up(
    clickhouse: Any, fleet: str
) -> None:
    """The whole path on real ClickHouse: JSON paths, parameter binding, roll-up."""
    start, end = _window()

    answer = await service.list_nodes(
        _repository(clickhouse, fleet), project_id=PROJECT, from_time=start, to_time=end
    )
    by_kind = {node.kind: node for node in answer.nodes}

    # The fleet also holds two processes that named no machine; they are the
    # subject of their own tests below and are `reporter`, never `host`.
    assert set(by_kind) == {"host", "container", "reporter"}, answer.nodes
    assert answer.truncated is False

    host = by_kind["host"]
    assert host.host == HOST
    # Two logical CPUs, 30% and 50% busy.
    assert host.cpu_utilization is not None
    assert round(host.cpu_utilization, 6) == 0.4
    assert host.memory_used_bytes == 6_000_000_000
    assert host.memory_limit_bytes == 10_000_000_000
    assert host.memory_utilization == 0.6
    # `/` and its bind mount at `/bind` are one device and count once; `/data`
    # is a second device and is added.
    assert host.filesystem_used_bytes == 40
    assert host.filesystem_limit_bytes == 200
    assert host.filesystem_utilization == 0.2
    # 5,000 bytes over 100 seconds on each of eth0's two directions. Loopback
    # moved 45,000 on each and contributes nothing.
    assert host.network_bytes_per_second == 100.0
    # vda at 100 B/s plus vdb at 10. vda1 is a partition of vda carrying the
    # same bytes, and adding it would say 200.
    assert host.disk_bytes_per_second == 110.0
    # Reporting every hundred seconds, right up to the end of the window.
    assert host.reporting_interval_seconds == float(WINDOW_SECONDS)
    assert host.stale is False

    container = by_kind["container"]
    assert container.container == CONTAINER
    assert container.cpu_cores_used == 1.5
    assert container.memory_used_bytes == 500
    assert container.memory_limit_bytes == 2_000


@pytest.fixture
async def redelivered_fleet(clickhouse: Any) -> AsyncIterator[str]:
    """The same fleet, stored twice — what a redelivery leaves behind.

    Two blocks rather than one statement run twice: ClickHouse drops a
    byte-identical block on sight (ADR-0075), so a repeated statement would leave
    nothing to catch. The token models a re-batch — the same points in a block
    the store has not seen.
    """
    tenant_id = str(uuid.uuid4())
    end = datetime.now(UTC).replace(microsecond=0)
    start = end - timedelta(seconds=WINDOW_SECONDS)

    written = (
        ("metrics_gauge", _gauge_rows(tenant_id, start, end)),
        ("metrics_sum", _sum_rows(tenant_id, start, end)),
        ("metrics_gauge", _reporter_rows(tenant_id, start, end)),
    )
    for attempt in ("first", "redelivered"):
        for index, (table, rows) in enumerate(written):
            await clickhouse.command(
                _insert(table, rows),
                settings={"insert_deduplication_token": f"{tenant_id}-{index}-{attempt}"},
            )
    try:
        yield tenant_id
    finally:
        for table in METRIC_TABLES:
            delete_after_run(table, tenant_id)


async def test_a_host_stored_twice_is_not_reported_as_sampling_twice_as_often(
    clickhouse: Any, redelivered_fleet: str
) -> None:
    """`reporting_interval_seconds` divides the window by the readings counted.

    Counting stored rows would halve it, and the screen would say a host reports
    every fifty seconds when it reports every hundred — a number nothing in the
    fleet ever measured.
    """
    start, end = _window()

    answer = await service.list_nodes(
        _repository(clickhouse, redelivered_fleet),
        project_id=PROJECT,
        from_time=start,
        to_time=end,
    )
    host = next(node for node in answer.nodes if node.kind == "host")

    assert host.reporting_interval_seconds == float(WINDOW_SECONDS)
    # The readings themselves are unchanged: every aggregate beside the count
    # takes a value rather than adding rows up.
    assert host.memory_used_bytes == 6_000_000_000
    assert round(host.cpu_utilization or 0.0, 6) == 0.4


async def test_signals_stored_twice_report_the_points_the_fleet_sent(
    clickhouse: Any, redelivered_fleet: str
) -> None:
    """`points` is grouped by metric alone, so its count must name the series too."""
    start, end = _window()

    duplicated = await service.list_signals(
        _repository(clickhouse, redelivered_fleet),
        project_id=PROJECT,
        from_time=start,
        to_time=end,
    )

    by_name = {signal.metric_name: signal for signal in duplicated.signals}
    memory = by_name[conventions.SYSTEM_MEMORY_USAGE]
    # One node, writing each of its series at `start` and at `end`.
    assert memory.nodes == 1
    assert memory.points == 2 * memory.series


async def test_signals_names_what_the_fleet_actually_sent(clickhouse: Any, fleet: str) -> None:
    start, end = _window()

    answer = await service.list_signals(
        _repository(clickhouse, fleet), project_id=PROJECT, from_time=start, to_time=end
    )

    assert {signal.metric_name for signal in answer.signals} == {
        conventions.SYSTEM_CPU_UTILIZATION,
        conventions.SYSTEM_MEMORY_USAGE,
        conventions.SYSTEM_FILESYSTEM_USAGE,
        conventions.SYSTEM_DISK_IO,
        conventions.SYSTEM_NETWORK_IO,
        conventions.CONTAINER_CPU_USAGE,
        conventions.CONTAINER_MEMORY_USAGE,
        conventions.CONTAINER_MEMORY_AVAILABLE,
    }
    assert all(signal.points > 0 for signal in answer.signals)

    # One node per metric, except CPU: the named host and the three unattributed
    # processes all report it, and the count is now at the reporter grain.
    by_name = {signal.metric_name: signal.nodes for signal in answer.signals}
    assert by_name[conventions.SYSTEM_CPU_UTILIZATION] == 4
    assert by_name[conventions.SYSTEM_MEMORY_USAGE] == 1


async def test_another_tenant_sees_none_of_that_fleet(clickhouse: Any, fleet: str) -> None:
    """Real ClickHouse, real rows, and the predicate is what hides them."""
    start, end = _window()
    stranger = str(uuid.uuid4())

    nodes = await service.list_nodes(
        _repository(clickhouse, stranger), project_id=PROJECT, from_time=start, to_time=end
    )
    signals = await service.list_signals(
        _repository(clickhouse, stranger), project_id=PROJECT, from_time=start, to_time=end
    )

    assert nodes.nodes == []
    assert signals.signals == []


async def test_a_project_with_no_host_metrics_gets_an_empty_list_not_an_error(
    clickhouse: Any,
) -> None:
    """The state every real project is in today, answered by the real database."""
    start, end = _window()

    answer = await service.list_nodes(
        _repository(clickhouse, str(uuid.uuid4())),
        project_id="a-project-that-never-existed",
        from_time=start,
        to_time=end,
    )

    assert answer.nodes == []


async def test_the_repository_refuses_a_statement_that_lost_its_tenant_predicate(
    clickhouse: Any,
) -> None:
    """The negative control against the live client: a statement without the
    predicate would read every tenant's hosts, so it never reaches the server."""
    with pytest.raises(TenantFilterMissingError):
        await _repository(clickhouse, str(uuid.uuid4())).query(
            "SELECT metric_name FROM metrics_gauge WHERE project_id = {project_id:String}",
            {"project_id": PROJECT},
        )


async def test_processes_without_a_host_name_stay_separate_nodes(
    clickhouse: Any, fleet: str
) -> None:
    """The grouping guarantee, asserted where it actually lives: the SQL.

    None of these resources names a host or a container, so the old
    `(host, container)` key put them in one node and averaged a 20%-busy process
    with an 80%-busy one into a single meaningless 50%. They are now identified
    by `service.instance.id`, which is what our own store's `recommendation`
    readings needed.
    """
    start, end = _window()

    answer = await service.list_nodes(
        _repository(clickhouse, fleet), project_id=PROJECT, from_time=start, to_time=end
    )
    reporters = sorted(
        (node for node in answer.nodes if node.kind == "reporter"),
        key=lambda node: node.reporter_instance,
    )

    assert [node.reporter_instance for node in reporters] == sorted(
        [FIRST_INSTANCE, SECOND_INSTANCE, SILENT_INSTANCE]
    )
    assert all(node.reporter == REPORTER for node in reporters)
    # Never a host: naming a machine after a process id would invent one.
    assert all(node.host == "" and node.container == "" for node in reporters)
    assert {node.reporter_instance: round(node.cpu_utilization or -1, 6) for node in reporters} == {
        FIRST_INSTANCE: 0.2,
        SECOND_INSTANCE: 0.8,
        SILENT_INSTANCE: 0.5,
    }


async def test_a_process_that_stopped_reporting_is_marked_and_still_listed(
    clickhouse: Any, fleet: str
) -> None:
    """The defect stated the right way round: is this thing down, or has it only
    stopped reporting?

    Three processes of one service reached the store; the third's last reading
    landed eight minutes before the window closed, having arrived every hundred
    seconds until then. It is separated from the live two by that cadence and
    not by a fixed age, and its readings stay on the screen — they are its last
    known state, and a vanished node is the most important row here.
    """
    start, end = _window()

    answer = await service.list_nodes(
        _repository(clickhouse, fleet), project_id=PROJECT, from_time=start, to_time=end
    )
    by_instance = {node.reporter_instance: node for node in answer.nodes if node.kind == "reporter"}

    silent = by_instance[SILENT_INSTANCE]
    assert silent.reporting_interval_seconds == float(WINDOW_SECONDS)
    assert silent.stale is True
    assert silent.cpu_utilization == 0.5

    assert by_instance[FIRST_INSTANCE].stale is False
    assert by_instance[SECOND_INSTANCE].stale is False


async def test_a_named_host_reported_by_two_services_is_one_node_with_one_memory_figure(
    clickhouse: Any, fleet: str
) -> None:
    """The regression the reporter column could have introduced.

    `SYSTEM_MEMORY_USAGE` for this host arrives under two `service_name` values.
    If the read grouped by the reporter as well as the host, the roll-up would
    receive two series where it expects one and *sum* them — reporting the
    machine as having twice the memory it has.
    """
    start, end = _window()

    answer = await service.list_nodes(
        _repository(clickhouse, fleet), project_id=PROJECT, from_time=start, to_time=end
    )
    hosts = [node for node in answer.nodes if node.kind == "host"]

    assert len(hosts) == 1, hosts
    assert hosts[0].reporter == ""
    assert hosts[0].memory_used_bytes == 6_000_000_000
    assert hosts[0].memory_limit_bytes == 10_000_000_000


# ------------------------------------------------------------------- the fleet
#
# Written as SQL text rather than through the driver's row binding, because the
# JSON columns are what this suite exists to exercise and a driver that reshaped
# them would hide the thing being proved.

_COLUMNS: Final = (
    "timestamp, start_timestamp, received_at, retention_days, tenant_id, project_id, "
    "service_name, scope_name, scope_version, metric_name, metric_unit, "
    "metric_description, resource_attrs, attrs, flags, value"
)


def _quoted(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _insert(table: str, rows: list[str]) -> str:
    return f"INSERT INTO {table} ({_COLUMNS}) VALUES\n" + ",\n".join(rows)


def _row(
    tenant_id: str,
    *,
    at: datetime,
    resource: dict[str, Any],
    metric_name: str,
    unit: str,
    attributes: dict[str, Any],
    value: float,
    service_name: str = "otelcol",
) -> str:
    stamp = _quoted(at.strftime("%Y-%m-%d %H:%M:%S"))
    fields = [
        stamp,
        stamp,
        stamp,
        str(PROBE_RETENTION_DAYS),
        _quoted(tenant_id),
        _quoted(PROJECT),
        _quoted(service_name),
        _quoted(""),
        _quoted(""),
        _quoted(metric_name),
        _quoted(unit),
        _quoted(""),
        _quoted(json.dumps(resource)),
        _quoted(json.dumps(attributes)),
        "0",
        repr(float(value)),
    ]
    return "(" + ", ".join(fields) + ")"


_HOST_RESOURCE: Final = {"host": {"name": HOST}}
_CONTAINER_RESOURCE: Final = {"host": {"name": HOST}, "container": {"name": CONTAINER}}


def _gauge_rows(tenant_id: str, start: datetime, end: datetime) -> list[str]:
    """`system.cpu.utilization` and `container.cpu.usage` are Gauges."""
    rows = []
    for at in (start, end):
        for logical_number, busy in (("0", 0.3), ("1", 0.5)):
            for mode, fraction in (("idle", 1.0 - busy), ("user", busy)):
                rows.append(
                    _row(
                        tenant_id,
                        at=at,
                        resource=_HOST_RESOURCE,
                        metric_name=conventions.SYSTEM_CPU_UTILIZATION,
                        unit="1",
                        attributes={"cpu": {"mode": mode, "logical_number": logical_number}},
                        value=fraction,
                    )
                )
        rows.append(
            _row(
                tenant_id,
                at=at,
                resource=_CONTAINER_RESOURCE,
                metric_name=conventions.CONTAINER_CPU_USAGE,
                unit="{cpu}",
                attributes={},
                value=1.5,
            )
        )
    return rows


def _reporter_rows(tenant_id: str, start: datetime, end: datetime) -> list[str]:
    """Three processes of one service, none naming a host — the demo's shape.

    Two are still reporting up to the end of the window. The third stopped
    `SILENCE_SECONDS` earlier, which is what a restart leaves behind: a row
    holding real readings from a process that no longer exists.
    """
    silence = timedelta(seconds=SILENCE_SECONDS)
    observations = {
        FIRST_INSTANCE: (0.2, (start, end)),
        SECOND_INSTANCE: (0.8, (start, end)),
        SILENT_INSTANCE: (0.5, (start - silence, end - silence)),
    }

    rows = []
    for instance, (busy, times) in observations.items():
        resource = {
            "service": {"name": REPORTER, "instance": {"id": instance}},
            "telemetry": {"sdk": {"language": "python"}},
        }
        for at in times:
            for mode, fraction in (("idle", 1.0 - busy), ("user", busy)):
                rows.append(
                    _row(
                        tenant_id,
                        at=at,
                        resource=resource,
                        service_name=REPORTER,
                        metric_name=conventions.SYSTEM_CPU_UTILIZATION,
                        unit="1",
                        attributes={"cpu": {"mode": mode, "logical_number": "0"}},
                        value=fraction,
                    )
                )
    return rows


def _sum_rows(tenant_id: str, start: datetime, end: datetime) -> list[str]:
    """Memory, filesystem and network are UpDownCounters and Counters."""
    rows = []
    for at in (start, end):
        for state, level in (("used", 6e9), ("free", 2e9), ("cached", 2e9)):
            # Written twice under two `service_name` values: one named host, two
            # agents reporting it. The read must still see one series per state.
            for reporting_service in ("otelcol", SECOND_HOST_REPORTER):
                rows.append(
                    _row(
                        tenant_id,
                        at=at,
                        resource=_HOST_RESOURCE,
                        service_name=reporting_service,
                        metric_name=conventions.SYSTEM_MEMORY_USAGE,
                        unit="By",
                        attributes={"system": {"memory": {"state": state}}},
                        value=level,
                    )
                )
        # `/` and `/bind` are one device, so the pair must count once; `/data`
        # is a second device and must be added to it.
        for device, mountpoint, used, free in (
            (ROOT_DEVICE, "/", 30, 70),
            (ROOT_DEVICE, "/bind", 30, 70),
            (DATA_DEVICE, "/data", 10, 90),
        ):
            for state, level in (("used", used), ("free", free)):
                rows.append(
                    _row(
                        tenant_id,
                        at=at,
                        resource=_HOST_RESOURCE,
                        metric_name=conventions.SYSTEM_FILESYSTEM_USAGE,
                        unit="By",
                        attributes={
                            "system": {
                                "device": device,
                                "filesystem": {"state": state, "mountpoint": mountpoint},
                            }
                        },
                        value=level,
                    )
                )
        for metric_name, level in (
            (conventions.CONTAINER_MEMORY_USAGE, 500),
            (conventions.CONTAINER_MEMORY_AVAILABLE, 1_500),
        ):
            rows.append(
                _row(
                    tenant_id,
                    at=at,
                    resource=_CONTAINER_RESOURCE,
                    metric_name=metric_name,
                    unit="By",
                    attributes={},
                    value=level,
                )
            )

    # A counter only rises: 5,000 bytes across the window on each direction of
    # the real interface. Loopback moves nine times as much and must not appear
    # in the answer at all — it never reached a network, and the same bytes are
    # counted on both of its directions.
    for interface, transferred in (("eth0", 5_000), ("lo", 45_000)):
        for direction in ("receive", "transmit"):
            for at, level in ((start, 0), (end, transferred)):
                rows.append(
                    _row(
                        tenant_id,
                        at=at,
                        resource=_HOST_RESOURCE,
                        metric_name=conventions.SYSTEM_NETWORK_IO,
                        unit="By",
                        attributes={
                            "network": {
                                "interface": {"name": interface},
                                "io": {"direction": direction},
                            }
                        },
                        value=level,
                    )
                )

    # The disk, the partition inside it carrying the very same bytes, and a
    # second disk that is genuinely its own.
    for device, transferred in ((DISK, 10_000), (DISK_PARTITION, 10_000), (SECOND_DISK, 1_000)):
        for at, level in ((start, 0), (end, transferred)):
            rows.append(
                _row(
                    tenant_id,
                    at=at,
                    resource=_HOST_RESOURCE,
                    metric_name=conventions.SYSTEM_DISK_IO,
                    unit="By",
                    attributes={
                        "system": {"device": device},
                        "disk": {"io": {"direction": "read"}},
                    },
                    value=level,
                )
            )
    return rows


@pytest.fixture
async def partly_redelivered_fleet(clickhouse: Any) -> AsyncIterator[str]:
    """A fleet where **one** of four CPU readings arrived twice.

    Partial on purpose, and this is the point of the fixture. Duplicating every
    reading leaves an average exactly where it was — the extra copies cancel — so
    `redelivered_fleet` cannot catch a duplicate-weighted mean however hard it
    tries. A real redelivery is partial: on the development store one hour held
    64 duplicate rows out of 3,264, a re-batch that overlapped rather than
    repeated.

    The fleet writes four CPU readings per instant — two cores, idle and user —
    at 0.7, 0.3, 0.5 and 0.5. Their mean is 0.5. Re-writing only the 0.7 pulls a
    duplicate-weighted mean to 0.54.
    """
    tenant_id = str(uuid.uuid4())
    end = datetime.now(UTC).replace(microsecond=0)
    start = end - timedelta(seconds=WINDOW_SECONDS)

    await clickhouse.command(
        _insert("metrics_gauge", _gauge_rows(tenant_id, start, end)),
        settings={"insert_deduplication_token": f"{tenant_id}-first"},
    )
    repeated = [
        _row(
            tenant_id,
            at=at,
            resource=_HOST_RESOURCE,
            metric_name=conventions.SYSTEM_CPU_UTILIZATION,
            unit="1",
            attributes={"cpu": {"mode": "idle", "logical_number": "0"}},
            value=0.7,
        )
        for at in (start, end)
    ]
    await clickhouse.command(
        _insert("metrics_gauge", repeated),
        settings={"insert_deduplication_token": f"{tenant_id}-overlap"},
    )
    try:
        yield tenant_id
    finally:
        delete_after_run("metrics_gauge", tenant_id)


async def test_the_store_really_holds_the_duplicate_this_asserts_on(
    clickhouse: Any, partly_redelivered_fleet: str
) -> None:
    """The companion assertion, for the reason the synthetics suite grew one.

    A regression test for duplication is worthless if the store quietly
    deduplicated the seed — the test would pass against the unfixed reader and
    prove nothing. This fails first if that happens.
    """
    result = await clickhouse.query(
        "SELECT count() AS rows, uniqExact((toJSONString(attrs), timestamp)) AS readings "
        "FROM metrics_gauge WHERE tenant_id = %(tenant)s AND metric_name = %(metric)s",
        parameters={
            "tenant": partly_redelivered_fleet,
            "metric": conventions.SYSTEM_CPU_UTILIZATION,
        },
    )
    rows, readings = result.result_rows[0]

    assert rows > readings, (
        f"the seed stored {rows} rows for {readings} readings — ClickHouse deduplicated it, "
        "so there is no duplicate here for the reader to mishandle"
    )


async def test_a_reading_that_arrived_twice_does_not_weight_the_trend_it_is_in(
    clickhouse: Any, partly_redelivered_fleet: str
) -> None:
    """The sparkline must plot the fleet's utilization, not the store's copies.

    `metricwriting.Absorb` takes a duplicate over a lost point, so the reader owes
    the deduplication — the same debt the run counts and the phase totals already
    pay. An average is the one aggregate where this hides: it looks plausible
    whatever the duplication does to it, and nothing on the screen could say the
    host was busier than it was.
    """
    start, end = _window()

    answer = await service.node_trends(
        _repository(clickhouse, partly_redelivered_fleet),
        project_id=PROJECT,
        from_time=start,
        to_time=end,
    )
    cpu = next(
        trend
        for trend in answer.trends
        if trend.metric_name == conventions.SYSTEM_CPU_UTILIZATION and trend.host == HOST
    )

    assert cpu.points, "the fleet reported CPU in this window"
    for point in cpu.points:
        assert round(point.value, 6) == 0.5, (
            f"the bucket reports {point.value}; the four readings in it are 0.7, 0.3, 0.5 and "
            "0.5, whose mean is 0.5. A value near 0.54 is the 0.7 counted twice."
        )
