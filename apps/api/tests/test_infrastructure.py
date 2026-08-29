"""Infrastructure roll-ups, and the tenant predicate that scopes them.

The fake store answers from the values the repository *bound*, never from the
statement text, so a query that lost its tenant predicate finds nothing and the
isolation tests below fail rather than quietly passing. The same technique as
`test_errorgroups.py`, for the same reason.

The roll-up tests carry the weight here for a second reason: **no host has ever
sent a `system.*` metric to our ClickHouse**, so the shaping cannot be checked
against production rows. Each rule is therefore stated as a reading of the
semantic conventions and tested against rows written to match them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_api.data.clickhouse.base import TENANT_PREDICATE, TenantScopedRepository
from nexovue_api.infrastructure import conventions, queries, service

TENANT_A: Final = "org-a"
TENANT_B: Final = "org-b"
PROJECT: Final = "project-1"

HOST_A: Final = "web-01"
HOST_B: Final = "db-01"

WINDOW_END: Final = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
WINDOW_START: Final = WINDOW_END - timedelta(hours=1)

_EARLIEST_NANOS: Final = int(WINDOW_START.timestamp() * 1_000_000_000)
# A hundred seconds of coverage, so a rate divides by a round number.
_LATEST_NANOS: Final = _EARLIEST_NANOS + 100_000_000_000

_NODE_COLUMNS: Final = [
    "host",
    "container",
    "metric_name",
    "metric_unit",
    "attributes_json",
    "latest_value",
    "earliest_value",
    "latest_nanos",
    "earliest_nanos",
    "points",
]

_SIGNAL_COLUMNS: Final = [
    "metric_name",
    "metric_unit",
    "points",
    "series",
    "nodes",
    "latest_nanos",
]


def series(
    metric_name: str,
    *,
    host: str = HOST_A,
    container: str = "",
    unit: str = "1",
    attributes: dict[str, Any] | None = None,
    latest: float,
    earliest: float | None = None,
) -> tuple[Any, ...]:
    """One collapsed series, shaped as ClickHouse returns it."""
    return (
        host,
        container,
        metric_name,
        unit,
        json.dumps(attributes or {}),
        latest,
        earliest if earliest is not None else latest,
        _LATEST_NANOS,
        _EARLIEST_NANOS,
        7,
    )


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


class FakeMetricStore:
    """Serves metric rows, honouring the tenant the repository bound."""

    def __init__(self, rows_by_tenant: dict[str, list[tuple[Any, ...]]]) -> None:
        self._rows_by_tenant = rows_by_tenant
        self.last_parameters: dict[str, Any] = {}

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        bound = dict(parameters or {})
        self.last_parameters = bound
        rows = self._rows_by_tenant.get(bound["tenant_id"], [])
        rows = [row for row in rows if row[2] in set(bound["metric_names"])]

        if sql == queries.SIGNALS_SEEN_SQL:
            return FakeQueryResult(_SIGNAL_COLUMNS, _as_signal_rows(rows))
        return FakeQueryResult(_NODE_COLUMNS, rows)


def _as_signal_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    names = sorted({row[2] for row in rows})
    return [(name, "1", 7, 1, 1, _LATEST_NANOS) for name in names]


def _repository(tenant: str, store: FakeMetricStore) -> TenantScopedRepository:
    return TenantScopedRepository(store, tenant_id=tenant)  # type: ignore[arg-type]


async def _nodes(tenant: str, store: FakeMetricStore) -> Any:
    return await service.list_nodes(
        _repository(tenant, store),
        project_id=PROJECT,
        from_time=WINDOW_START,
        to_time=WINDOW_END,
    )


async def _one_node(rows: list[tuple[Any, ...]]) -> Any:
    store = FakeMetricStore({TENANT_A: rows})
    answer = await _nodes(TENANT_A, store)
    assert len(answer.nodes) == 1
    return answer.nodes[0]


# ------------------------------------------------------------------- the query


def test_every_statement_filters_on_the_tenant() -> None:
    """The repository would refuse one that does not; this says which is missing."""
    for sql in (queries.NODE_READINGS_SQL, queries.SIGNALS_SEEN_SQL):
        assert TENANT_PREDICATE.search(sql), sql


def test_both_instrument_tables_are_read() -> None:
    """A host's signals are split across gauges and sums by instrument kind.

    Reading one table would silently drop memory, filesystem, disk and network,
    leaving a screen that shows only CPU and looks like it is working.
    """
    assert "FROM metrics_gauge" in queries.NODE_READINGS_SQL
    assert "FROM metrics_sum" in queries.NODE_READINGS_SQL


def test_the_read_is_bounded_on_both_clocks() -> None:
    """`timestamp` is the customer's clock and `received_at` is ours (ADR 0067).

    Only the second prunes partitions, so a statement carrying just the first
    reads every partition in the retention period on every request.
    """
    assert "received_at >=" in queries.NODE_READINGS_SQL
    assert "timestamp >=" in queries.NODE_READINGS_SQL


async def test_the_read_asks_only_for_metrics_the_screen_knows() -> None:
    store = FakeMetricStore({TENANT_A: []})
    await _nodes(TENANT_A, store)

    assert store.last_parameters["metric_names"] == list(conventions.INFRASTRUCTURE_METRIC_NAMES)
    assert store.last_parameters["project_id"] == PROJECT


async def test_the_received_at_bounds_are_wider_than_the_asked_window() -> None:
    store = FakeMetricStore({TENANT_A: []})
    await _nodes(TENANT_A, store)

    assert store.last_parameters["received_from"] < WINDOW_START
    assert store.last_parameters["received_to"] > WINDOW_END


# ------------------------------------------------------------------ isolation


async def test_two_organizations_cannot_read_each_others_hosts() -> None:
    store = FakeMetricStore(
        {
            TENANT_A: [series(conventions.SYSTEM_CPU_UTILIZATION, host=HOST_A, latest=0.2)],
            TENANT_B: [series(conventions.SYSTEM_CPU_UTILIZATION, host=HOST_B, latest=0.2)],
        }
    )

    for_a = await _nodes(TENANT_A, store)
    for_b = await _nodes(TENANT_B, store)

    assert [node.host for node in for_a.nodes] == [HOST_A]
    assert [node.host for node in for_b.nodes] == [HOST_B]


async def test_the_tenant_bound_is_the_repositorys_and_never_a_parameter() -> None:
    store = FakeMetricStore({TENANT_A: []})
    await _nodes(TENANT_A, store)

    assert store.last_parameters["tenant_id"] == TENANT_A


# ------------------------------------------------- nothing has arrived, so far


async def test_a_project_with_no_host_metrics_gets_an_empty_list_not_an_error() -> None:
    """The state every project is in today: no `system.*` metric has ever arrived."""
    store = FakeMetricStore({TENANT_A: []})

    answer = await _nodes(TENANT_A, store)

    assert answer.nodes == []
    assert answer.truncated is False


async def test_signals_says_whether_a_collector_is_sending_anything() -> None:
    """An empty fleet and a silent fleet need different advice."""
    silent = FakeMetricStore({TENANT_A: []})
    reporting = FakeMetricStore(
        {TENANT_A: [series(conventions.SYSTEM_CPU_UTILIZATION, latest=0.2)]}
    )

    nothing = await service.list_signals(
        _repository(TENANT_A, silent),
        project_id=PROJECT,
        from_time=WINDOW_START,
        to_time=WINDOW_END,
    )
    something = await service.list_signals(
        _repository(TENANT_A, reporting),
        project_id=PROJECT,
        from_time=WINDOW_START,
        to_time=WINDOW_END,
    )

    assert nothing.signals == []
    assert [signal.metric_name for signal in something.signals] == [
        conventions.SYSTEM_CPU_UTILIZATION
    ]


async def test_application_metrics_are_not_mistaken_for_infrastructure() -> None:
    """What this ClickHouse actually holds today: synthetic checks and demo apps.

    None of it describes a host, and none of it may appear on a host list.
    """
    store = FakeMetricStore(
        {
            TENANT_A: [
                series("synthetic.check.duration", latest=120.0),
                series("nexovue.spans.count", latest=9000.0),
                series("http.server.request.duration", latest=12.0),
            ]
        }
    )

    answer = await _nodes(TENANT_A, store)

    assert answer.nodes == []


# ------------------------------------------------------------------- roll-ups


async def test_cpu_busy_is_the_non_idle_modes_averaged_over_logical_cpus() -> None:
    """The modes of one logical CPU sum to 1, so the busy ones are the answer.

    Two CPUs at 30% and 50% is a host at 40%, not at 80%.
    """
    node = await _one_node(
        [
            _cpu(cpu="0", mode="idle", value=0.7),
            _cpu(cpu="0", mode="user", value=0.2),
            _cpu(cpu="0", mode="system", value=0.1),
            _cpu(cpu="1", mode="idle", value=0.5),
            _cpu(cpu="1", mode="user", value=0.5),
        ]
    )

    assert node.cpu_utilization is not None
    assert round(node.cpu_utilization, 6) == 0.4


async def test_a_collector_reporting_only_idle_is_read_as_its_complement() -> None:
    node = await _one_node([_cpu(cpu="0", mode="idle", value=0.75)])

    assert node.cpu_utilization is not None
    assert round(node.cpu_utilization, 6) == 0.25


async def test_the_superseded_cpu_state_attribute_is_still_understood() -> None:
    """`system.cpu.state` was renamed to `cpu.mode`. An older collector in a
    customer's cluster still sends the old spelling, and reading only the new
    one would report a busy host as idle."""
    node = await _one_node(
        [
            series(
                conventions.SYSTEM_CPU_UTILIZATION,
                attributes={"system.cpu.state": "user", "cpu.logical_number": "0"},
                latest=0.35,
            )
        ]
    )

    assert node.cpu_utilization is not None
    assert round(node.cpu_utilization, 6) == 0.35


async def test_memory_total_is_reconstructed_from_the_states_when_no_limit_is_sent() -> None:
    """`system.memory.usage`'s states partition the whole, so they sum to it."""
    node = await _one_node(
        [
            _memory(state="used", value=6_000_000_000),
            _memory(state="free", value=2_000_000_000),
            _memory(state="cached", value=2_000_000_000),
        ]
    )

    assert node.memory_used_bytes == 6_000_000_000
    assert node.memory_limit_bytes == 10_000_000_000
    assert node.memory_utilization == 0.6


async def test_a_declared_memory_limit_wins_over_the_reconstructed_one() -> None:
    node = await _one_node(
        [
            _memory(state="used", value=4_000_000_000),
            series(conventions.SYSTEM_MEMORY_LIMIT, unit="By", latest=16_000_000_000),
        ]
    )

    assert node.memory_limit_bytes == 16_000_000_000
    assert node.memory_utilization == 0.25


async def test_filesystem_figures_are_summed_across_mountpoints() -> None:
    node = await _one_node(
        [
            _filesystem(mountpoint="/", state="used", value=30),
            _filesystem(mountpoint="/", state="free", value=70),
            _filesystem(mountpoint="/data", state="used", value=10),
            _filesystem(mountpoint="/data", state="free", value=90),
        ]
    )

    assert node.filesystem_used_bytes == 40
    assert node.filesystem_limit_bytes == 200
    assert node.filesystem_utilization == 0.2


async def test_network_and_disk_are_reported_as_rates_not_totals() -> None:
    """They are counters that only rise; a level says nothing about traffic."""
    node = await _one_node(
        [
            _counter(conventions.SYSTEM_NETWORK_IO, direction="receive", earliest=0, latest=5_000),
            _counter(
                conventions.SYSTEM_NETWORK_IO, direction="transmit", earliest=1_000, latest=6_000
            ),
            _counter(conventions.SYSTEM_DISK_IO, direction="read", earliest=0, latest=1_000),
        ]
    )

    # 100 seconds of coverage: 50 + 50 bytes/s across the two directions.
    assert node.network_bytes_per_second == 100.0
    assert node.disk_bytes_per_second == 10.0


async def test_a_counter_that_fell_is_an_exporter_restart_and_is_left_out() -> None:
    """Its delta describes the restart, not the traffic. A negative rate would
    subtract from the interfaces that are working."""
    node = await _one_node(
        [
            _counter(conventions.SYSTEM_NETWORK_IO, direction="receive", earliest=0, latest=5_000),
            _counter(
                conventions.SYSTEM_NETWORK_IO, direction="transmit", earliest=9_000, latest=100
            ),
        ]
    )

    assert node.network_bytes_per_second == 50.0


async def test_a_measurement_nobody_sent_is_null_and_never_zero() -> None:
    """Zero would render an unmonitored host as an idle, healthy one."""
    node = await _one_node([_cpu(cpu="0", mode="user", value=0.4)])

    assert node.cpu_utilization == 0.4
    assert node.memory_used_bytes is None
    assert node.memory_utilization is None
    assert node.filesystem_used_bytes is None
    assert node.disk_bytes_per_second is None
    assert node.network_bytes_per_second is None
    assert node.signals == [conventions.SYSTEM_CPU_UTILIZATION]


async def test_a_container_is_its_own_node_and_reports_cores_not_a_fraction() -> None:
    """`container.cpu.usage` is CPU cores, `system.cpu.utilization` a fraction.
    Rendering the two in one column would misreport both."""
    store = FakeMetricStore(
        {
            TENANT_A: [
                _cpu(cpu="0", mode="user", value=0.4),
                series(
                    conventions.CONTAINER_CPU_USAGE,
                    host=HOST_A,
                    container="checkout",
                    unit="{cpu}",
                    latest=1.5,
                ),
                series(
                    conventions.CONTAINER_MEMORY_USAGE,
                    host=HOST_A,
                    container="checkout",
                    unit="By",
                    latest=500,
                ),
                series(
                    conventions.CONTAINER_MEMORY_AVAILABLE,
                    host=HOST_A,
                    container="checkout",
                    unit="By",
                    latest=1_500,
                ),
            ]
        }
    )

    answer = await _nodes(TENANT_A, store)
    kinds = {node.kind: node for node in answer.nodes}

    assert set(kinds) == {"host", "container"}
    assert kinds["host"].cpu_utilization == 0.4
    assert kinds["host"].cpu_cores_used is None
    assert kinds["container"].cpu_cores_used == 1.5
    assert kinds["container"].cpu_utilization is None
    assert kinds["container"].memory_limit_bytes == 2_000
    assert kinds["container"].memory_utilization == 0.25


# ------------------------------------------------------------------ row shapes


def _cpu(*, cpu: str, mode: str, value: float) -> tuple[Any, ...]:
    return series(
        conventions.SYSTEM_CPU_UTILIZATION,
        attributes={"cpu": {"mode": mode, "logical_number": cpu}},
        latest=value,
    )


def _memory(*, state: str, value: float) -> tuple[Any, ...]:
    return series(
        conventions.SYSTEM_MEMORY_USAGE,
        unit="By",
        attributes={"system": {"memory": {"state": state}}},
        latest=value,
    )


def _filesystem(*, mountpoint: str, state: str, value: float) -> tuple[Any, ...]:
    return series(
        conventions.SYSTEM_FILESYSTEM_USAGE,
        unit="By",
        attributes={"system": {"filesystem": {"state": state, "mountpoint": mountpoint}}},
        latest=value,
    )


def _counter(
    metric_name: str, *, direction: str, earliest: float, latest: float
) -> tuple[Any, ...]:
    return series(
        metric_name,
        unit="By",
        attributes={"direction": direction},
        earliest=earliest,
        latest=latest,
    )
