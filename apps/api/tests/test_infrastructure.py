"""Infrastructure roll-ups, and the tenant predicate that scopes them.

The fake store answers from the values the repository *bound*, never from the
statement text, so a query that lost its tenant predicate finds nothing and the
isolation tests below fail rather than quietly passing. The same technique as
`test_errorgroups.py`, for the same reason.

The roll-up tests carry the weight here, and their rows are now written in the
shape a producer actually sends rather than the shape the specification
describes. The two differ, and taking the specification's word for it was a real
defect: the OpenTelemetry Demo's host metrics arrive with flat `state` and `cpu`
attributes, which the spec-only reading could not name, and an unnamed mode was
being counted as work — reporting a 23%-busy host at 1124%.

Both spellings are tested: the flat one because it is what we receive, the
namespaced one because it is what the specification will make producers send.

The rows a *reporter* node is built from carry no host and no container, which
is the state every host metric in our own store is in. The grouping that keeps
two such reporters apart lives in the SQL, so it is proved in
`test_infrastructure_live.py` against real ClickHouse; what is proved here is
what the service does with the columns once they arrive.
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

_NANOS_PER_SECOND: Final = 1_000_000_000

# A hundred seconds of coverage ending as the window does, so a rate divides by
# a round number and a node built from these series is still reporting.
_LATEST_NANOS: Final = int(WINDOW_END.timestamp() * _NANOS_PER_SECOND)
_SPAN_SECONDS: Final = 100.0

# Eleven points across those hundred seconds: a report every ten seconds, which
# is the cadence the liveness rule reads a node's deadline off.
_POINTS: Final = 11
_INTERVAL_SECONDS: Final = 10.0

_AN_HOUR_IN_NANOS: Final = 3_600 * _NANOS_PER_SECOND

_NODE_COLUMNS: Final = [
    "host",
    "container",
    "reporter",
    "reporter_instance",
    "metric_name",
    "metric_unit",
    "attributes_json",
    "latest_value",
    "earliest_value",
    "latest_nanos",
    "earliest_nanos",
    "points",
]

# Where `metric_name` sits in a node row, so widening the row cannot silently
# start filtering on the wrong column.
_METRIC_NAME: Final = _NODE_COLUMNS.index("metric_name")

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
    reporter: str = "",
    reporter_instance: str = "",
    unit: str = "1",
    attributes: dict[str, Any] | None = None,
    latest: float,
    earliest: float | None = None,
    latest_nanos: int = _LATEST_NANOS,
    span_seconds: float = _SPAN_SECONDS,
    points: int = _POINTS,
) -> tuple[Any, ...]:
    """One collapsed series, shaped as ClickHouse returns it.

    The query blanks the reporter whenever the resource named itself, so a row
    written here carries either a host/container or a reporter, never both.

    `span_seconds` and `points` together are the cadence the liveness rule reads
    a node's deadline off; the defaults are a report every ten seconds arriving
    right up to the end of the window.
    """
    return (
        host,
        container,
        reporter,
        reporter_instance,
        metric_name,
        unit,
        json.dumps(attributes or {}),
        latest,
        earliest if earliest is not None else latest,
        latest_nanos,
        latest_nanos - int(span_seconds * _NANOS_PER_SECOND),
        points,
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
        rows = [row for row in rows if row[_METRIC_NAME] in set(bound["metric_names"])]

        if sql == queries.SIGNALS_SEEN_SQL:
            return FakeQueryResult(_SIGNAL_COLUMNS, _as_signal_rows(rows))
        return FakeQueryResult(_NODE_COLUMNS, rows)


def _as_signal_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    names = sorted({row[_METRIC_NAME] for row in rows})
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
    """`timestamp` is the customer's clock and `received_at` is ours.

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
    """The state most projects are in: nothing is scraping their hosts."""
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


async def test_the_flat_attribute_spelling_producers_actually_send_is_read() -> None:
    """The shape our own ClickHouse holds: `{"cpu": 0, "state": "idle"}`.

    Semconv v1.27.0 renamed these keys, but neither the Collector's hostmetrics
    cpuscraper nor the Python `SystemMetricsInstrumentor` followed, so reading
    only the specification's spelling matched no real traffic at all. Note the
    CPU number is a JSON integer here, not a string — reading only strings
    collapses every CPU of a host into one group.
    """
    node = await _one_node(
        [
            series(
                conventions.SYSTEM_CPU_UTILIZATION,
                attributes={"cpu": 0, "state": "idle"},
                latest=0.70,
            ),
            series(
                conventions.SYSTEM_CPU_UTILIZATION,
                attributes={"cpu": 0, "state": "user"},
                latest=0.30,
            ),
            series(
                conventions.SYSTEM_CPU_UTILIZATION,
                attributes={"cpu": 1, "state": "idle"},
                latest=0.90,
            ),
            series(
                conventions.SYSTEM_CPU_UTILIZATION,
                attributes={"cpu": 1, "state": "user"},
                latest=0.10,
            ),
        ]
    )

    # Two CPUs at 30% and 10%, so the host is at 20% — not at 40%.
    assert node.cpu_utilization is not None
    assert round(node.cpu_utilization, 6) == 0.2


async def test_a_cpu_mode_we_cannot_name_is_not_counted_as_work() -> None:
    """The defect this replaced reported an idle host as saturated.

    Every mode of one CPU sums to 1 and idle is most of it, so adding an
    uninterpretable point to the busy side is not a cautious default — it is an
    invented measurement. Against real rows it turned a 23%-busy host into
    1124%. "Not measured" is the only honest reading of a point we cannot name.
    """
    node = await _one_node(
        [
            series(
                conventions.SYSTEM_CPU_UTILIZATION,
                attributes={"cpu": 0, "mood": "idle"},
                latest=0.97,
            ),
            series(
                conventions.SYSTEM_CPU_UTILIZATION,
                attributes={"cpu": 0, "mood": "user"},
                latest=0.03,
            ),
        ]
    )

    assert node.cpu_utilization is None


async def test_memory_states_are_read_under_the_flat_spelling_too() -> None:
    """Both producers send `state`, not `system.memory.state`."""
    node = await _one_node(
        [
            series(
                conventions.SYSTEM_MEMORY_USAGE,
                unit="By",
                attributes={"state": "used"},
                latest=6_000_000_000,
            ),
            series(
                conventions.SYSTEM_MEMORY_USAGE,
                unit="By",
                attributes={"state": "free"},
                latest=4_000_000_000,
            ),
        ]
    )

    assert node.memory_used_bytes == 6_000_000_000
    assert node.memory_limit_bytes == 10_000_000_000


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


async def test_filesystem_figures_are_summed_across_separate_volumes() -> None:
    node = await _one_node(
        [
            _filesystem(device="/dev/vda1", mountpoint="/", state="used", value=30),
            _filesystem(device="/dev/vda1", mountpoint="/", state="free", value=70),
            _filesystem(device="/dev/vdb1", mountpoint="/data", state="used", value=10),
            _filesystem(device="/dev/vdb1", mountpoint="/data", state="free", value=90),
        ]
    )

    assert node.filesystem_used_bytes == 40
    assert node.filesystem_limit_bytes == 200
    assert node.filesystem_utilization == 0.2


# ------------------------------------- a sum needs the series to be independent
#
# Three of these metrics split on an attribute whose values overlap, so summing
# every series of them counts the same bytes twice. Each rule below is a reading
# of a producer's naming rather than of the specification, which settles none of
# it; `independence.py` says which way each errs.


async def test_a_partition_is_not_added_to_the_disk_that_contains_it() -> None:
    """The live defect: `/proc/diskstats` lists `vda1` beside `vda`.

    Measured on the demo's own store over thirty minutes, `vda` and `vda1`
    carried identical read and write counters — one is a partition of the other,
    not a second disk — and the screen reported twice the real traffic.
    """
    node = await _one_node(
        [
            _disk(device="vda", direction="read", earliest=0, latest=10_000),
            _disk(device="vda1", direction="read", earliest=0, latest=10_000),
            _disk(device="vdb", direction="read", earliest=0, latest=1_000),
        ]
    )

    # 100 bytes/s from vda and 10 from vdb. Counting vda1 as well would say 200.
    assert node.disk_bytes_per_second == 110.0


async def test_an_nvme_partition_is_recognised_and_a_second_array_is_not() -> None:
    """The kernel inserts `p` when a disk's own name ends in a digit.

    That single rule is what keeps `nvme0n1p1` a partition while `md11` stays a
    separate array rather than a partition of `md1` — the failure a bare
    "name plus digits" test would produce.
    """
    node = await _one_node(
        [
            _disk(device="nvme0n1", direction="read", earliest=0, latest=10_000),
            _disk(device="nvme0n1p1", direction="read", earliest=0, latest=10_000),
            _disk(device="md1", direction="read", earliest=0, latest=1_000),
            _disk(device="md11", direction="read", earliest=0, latest=1_000),
        ]
    )

    # nvme0n1 at 100 B/s, plus md1 and md11 at 10 each — two arrays, not one.
    assert node.disk_bytes_per_second == 120.0


async def test_the_twenty_seventh_disk_is_not_read_as_a_partition_of_the_first() -> None:
    """`sdaa` follows `sdz`, and it is a disk. The suffix has to be a number."""
    node = await _one_node(
        [
            _disk(device="sda", direction="read", earliest=0, latest=10_000),
            _disk(device="sdaa", direction="read", earliest=0, latest=10_000),
        ]
    )

    assert node.disk_bytes_per_second == 200.0


async def test_a_partition_whose_disk_was_not_reported_is_still_counted() -> None:
    """Nothing here duplicates it, and dropping what a customer sent is worse."""
    node = await _one_node([_disk(device="sda1", direction="read", earliest=0, latest=10_000)])

    assert node.disk_bytes_per_second == 100.0


async def test_loopback_is_not_network_traffic_and_is_left_out() -> None:
    """It never reached a network, and its bytes are reported twice over.

    On the demo's store `lo` showed 170.8 KiB transmitted and the same 170.8 KiB
    received — one set of bytes, counted on both directions of one interface.
    """
    node = await _one_node(
        [
            _network(device="eth0", direction="receive", earliest=0, latest=5_000),
            _network(device="eth0", direction="transmit", earliest=0, latest=5_000),
            _network(device="lo", direction="receive", earliest=0, latest=9_000),
            _network(device="lo", direction="transmit", earliest=0, latest=9_000),
        ]
    )

    assert node.network_bytes_per_second == 100.0


async def test_a_bsd_loopback_interface_is_left_out_too() -> None:
    node = await _one_node(
        [
            _network(device="en0", direction="receive", earliest=0, latest=5_000),
            _network(device="lo0", direction="receive", earliest=0, latest=9_000),
        ]
    )

    assert node.network_bytes_per_second == 50.0


async def test_one_filesystem_mounted_twice_is_counted_once() -> None:
    """A bind mount, or `/` and `/var` on one device: the same bytes reported
    under two mountpoints. Summing them reports twice the storage the machine
    has, and the Collector's own scraper keys on (mountpoint, device) — so it
    emits exactly this."""
    node = await _one_node(
        [
            _filesystem(device="/dev/vda1", mountpoint="/", state="used", value=30),
            _filesystem(device="/dev/vda1", mountpoint="/", state="free", value=70),
            _filesystem(device="/dev/vda1", mountpoint="/var", state="used", value=30),
            _filesystem(device="/dev/vda1", mountpoint="/var", state="free", value=70),
        ]
    )

    assert node.filesystem_used_bytes == 30
    assert node.filesystem_limit_bytes == 100
    assert node.filesystem_utilization == 0.3


async def test_mounts_that_name_no_device_stay_separate_filesystems() -> None:
    """Two tmpfs mounts are genuinely two, and a blank device says nothing."""
    node = await _one_node(
        [
            _filesystem(device="", mountpoint="/run", state="used", value=10),
            _filesystem(device="", mountpoint="/dev/shm", state="used", value=10),
        ]
    )

    assert node.filesystem_used_bytes == 20


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


# ------------------------------------------------------- has it stopped reporting?
#
# `last_seen` on its own says nothing: a host reporting every ten seconds and one
# reporting every ten minutes are not equally late at the same age. The threshold
# is therefore read off each node's own cadence, and the question is asked against
# the end of the requested window rather than wall-clock now — so a window that
# closed yesterday still has an answer.


async def test_a_nodes_reporting_cadence_is_measured_from_its_own_points() -> None:
    node = await _one_node([_cpu(cpu="0", mode="user", value=0.4)])

    assert node.reporting_interval_seconds == _INTERVAL_SECONDS
    assert node.stale is False


async def test_a_node_that_went_quiet_for_several_of_its_intervals_is_stale() -> None:
    """The live defect: two rows styled identically, one of them long gone.

    `recommendation` restarted, and the replaced process's row sat beside the
    live one saying only "57 min ago" — which is meaningless without the cadence
    it broke.
    """
    node = await _one_node([_quiet_since(_LATEST_NANOS - _AN_HOUR_IN_NANOS)])

    assert node.reporting_interval_seconds == _INTERVAL_SECONDS
    assert node.stale is True


async def test_lateness_is_judged_against_the_cadence_and_not_a_fixed_age() -> None:
    """Five minutes of silence is nothing from a node reporting every ten of
    them, and it is three missed reports from a node reporting every minute.
    A fixed threshold has to be wrong about one of the two."""
    five_minutes_ago = _LATEST_NANOS - 300 * _NANOS_PER_SECOND

    # Three points twenty minutes apart: a report every ten minutes.
    unhurried = await _one_node([_quiet_since(five_minutes_ago, span_seconds=1_200, points=3)])
    # Eleven points a hundred seconds apart: a report every ten seconds.
    brisk = await _one_node([_quiet_since(five_minutes_ago)])

    assert unhurried.reporting_interval_seconds == 600.0
    assert unhurried.stale is False
    assert brisk.reporting_interval_seconds == _INTERVAL_SECONDS
    assert brisk.stale is True


async def test_a_node_with_no_measurable_cadence_falls_back_to_a_fixed_age() -> None:
    """One point in the window shows no interval, and inventing one would be a
    guess. The fallback is stated where it lives, not implied here."""
    recent = await _one_node([_quiet_since(_LATEST_NANOS, points=1)])
    long_ago = await _one_node([_quiet_since(_LATEST_NANOS - _AN_HOUR_IN_NANOS, points=1)])

    assert recent.reporting_interval_seconds is None
    assert recent.stale is False
    assert long_ago.stale is True


async def test_a_node_that_stopped_reporting_is_still_listed_with_its_readings() -> None:
    """A host that vanished is the most important thing this screen can say, and
    dropping it is how that gets lost. The row stays, marked."""
    store = FakeMetricStore(
        {
            TENANT_A: [
                series(
                    conventions.SYSTEM_CPU_UTILIZATION,
                    host="",
                    reporter="recommendation",
                    reporter_instance="gone",
                    attributes={"cpu": 0, "state": "user"},
                    latest=0.054,
                    latest_nanos=_LATEST_NANOS - _AN_HOUR_IN_NANOS,
                ),
                series(
                    conventions.SYSTEM_CPU_UTILIZATION,
                    host="",
                    reporter="recommendation",
                    reporter_instance="live",
                    attributes={"cpu": 0, "state": "user"},
                    latest=0.066,
                ),
            ]
        }
    )

    answer = await _nodes(TENANT_A, store)

    assert [node.reporter_instance for node in answer.nodes] == ["gone", "live"]
    assert [node.stale for node in answer.nodes] == [True, False]
    # Its last known readings are kept: they were measured, and erasing them
    # would be the same lie as a zero.
    assert [node.cpu_utilization for node in answer.nodes] == [0.054, 0.066]


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


async def test_a_resource_naming_no_machine_is_named_by_the_process_that_reported_it() -> None:
    """It is not a host, and must never be presented as one.

    `service.instance.id` identifies a process. Calling that a host would invent
    a machine nobody told us about — the mistake already rejected for IP-named
    peers — so the kind says `reporter` and the readings stay attached to it.
    """
    node = await _one_node(
        [
            series(
                conventions.SYSTEM_CPU_UTILIZATION,
                host="",
                reporter="recommendation",
                reporter_instance="4d6173c8-7e68-45bd-a79e-80d4ce39ea3a",
                attributes={"cpu": 0, "state": "user"},
                latest=0.23,
            )
        ]
    )

    assert node.kind == "reporter"
    assert node.host == ""
    assert node.container == ""
    assert node.reporter == "recommendation"
    assert node.reporter_instance == "4d6173c8-7e68-45bd-a79e-80d4ce39ea3a"
    # The reading is a host's fraction and is kept: dropping what a customer
    # sent is the worst failure this screen has.
    assert node.cpu_utilization == 0.23


async def test_two_processes_reporting_without_a_host_name_are_two_nodes() -> None:
    """Merging them averaged unrelated machines into one row of nonsense.

    Our own store holds exactly this: `recommendation` reported under two
    `service.instance.id` values, and under the old `(host, container)` key both
    collapsed into one node whose CPU and memory came from different processes.
    """
    store = FakeMetricStore(
        {
            TENANT_A: [
                series(
                    conventions.SYSTEM_CPU_UTILIZATION,
                    host="",
                    reporter="recommendation",
                    reporter_instance="first",
                    attributes={"cpu": 0, "state": "user"},
                    latest=0.2,
                ),
                series(
                    conventions.SYSTEM_CPU_UTILIZATION,
                    host="",
                    reporter="recommendation",
                    reporter_instance="second",
                    attributes={"cpu": 0, "state": "user"},
                    latest=0.8,
                ),
            ]
        }
    )

    answer = await _nodes(TENANT_A, store)

    assert [node.reporter_instance for node in answer.nodes] == ["first", "second"]
    assert [node.cpu_utilization for node in answer.nodes] == [0.2, 0.8]


async def test_a_named_host_is_still_grouped_by_its_name_and_not_by_its_reporter() -> None:
    """A machine that named itself is one node however many processes report it.

    The query blanks the reporter for such rows; if it ever stopped, one host
    would split into a row per collector and each row would hold half its fleet
    readings.
    """
    node = await _one_node(
        [
            _memory(state="used", value=6_000_000_000),
            _memory(state="free", value=4_000_000_000),
        ]
    )

    assert node.kind == "host"
    assert node.host == HOST_A
    assert node.reporter == ""
    assert node.memory_used_bytes == 6_000_000_000


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


def _quiet_since(
    latest_nanos: int, *, span_seconds: float = _SPAN_SECONDS, points: int = _POINTS
) -> tuple[Any, ...]:
    """One CPU series whose last point landed at a chosen moment."""
    return series(
        conventions.SYSTEM_CPU_UTILIZATION,
        attributes={"cpu": 0, "state": "user"},
        latest=0.4,
        latest_nanos=latest_nanos,
        span_seconds=span_seconds,
        points=points,
    )


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


def _filesystem(*, device: str, mountpoint: str, state: str, value: float) -> tuple[Any, ...]:
    return series(
        conventions.SYSTEM_FILESYSTEM_USAGE,
        unit="By",
        attributes={
            "device": device,
            "system": {"filesystem": {"state": state, "mountpoint": mountpoint}},
        },
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


def _disk(*, device: str, direction: str, earliest: float, latest: float) -> tuple[Any, ...]:
    """The flat spelling both the Collector and the Python SDK send."""
    return series(
        conventions.SYSTEM_DISK_IO,
        unit="By",
        attributes={"device": device, "direction": direction},
        earliest=earliest,
        latest=latest,
    )


def _network(*, device: str, direction: str, earliest: float, latest: float) -> tuple[Any, ...]:
    return series(
        conventions.SYSTEM_NETWORK_IO,
        unit="By",
        attributes={"device": device, "direction": direction},
        earliest=earliest,
        latest=latest,
    )
