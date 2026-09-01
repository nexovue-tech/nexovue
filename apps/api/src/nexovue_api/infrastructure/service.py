"""Turning per-series metric readings into a row per node.

No SQL text and no HTTP. The work here is the roll-up, and it is here rather
than in ClickHouse for one reason: every rule below is a reading of the
semantic conventions, and a reading can be wrong. In Python each one is a named
function with a test against representative rows; in SQL it would be a
`multiIf` nobody could check.

**The rules, and why each is the honest one.**

*CPU.* `system.cpu.utilization` is a fraction per logical CPU per `cpu.mode`,
and the modes of one logical CPU sum to 1. So a host's busy fraction is the sum
of the non-idle modes on each CPU, averaged over CPUs. When a collector reports
only `idle`, the same number is `1 - idle`.

*Memory and filesystem totals.* `system.memory.usage` is split by state and the
states partition the total, so their sum is the limit when `system.memory.limit`
is absent. Filesystem figures are summed across mountpoints: a host with five
volumes gets one used-over-capacity pair, which is what a fleet list shows.

*Disk and network.* These are counters that only rise, so a level is
meaningless and the reading is a rate: each series' own delta over its own
elapsed time, summed across interfaces and directions. A fall means the
exporter restarted, and that series contributes nothing rather than a negative
rate.

A measurement nobody sent stays `None`. Substituting zero would report an
unmonitored host as a healthy one.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.infrastructure import conventions, queries
from nexovue_api.infrastructure.schemas import (
    NodeKind,
    NodeListResponse,
    NodeSummary,
    SignalListResponse,
    SignalSummary,
)
from nexovue_api.window import receive_window

# The ceiling on series one read may collapse. A hundred hosts with sixteen
# CPUs, eight CPU modes, a handful of mounts and two interfaces is roughly
# 15,000 series, so this holds a fleet of that size whole and stops one
# mislabelled attribute from turning a screen into a scan.
MAX_SERIES: Final = 50_000


@dataclass(frozen=True, slots=True)
class NodeKey:
    """What distinguishes one node from another in a project.

    A resource that named neither a host nor a container is identified by the
    process that reported it; the query blanks `reporter` whenever the resource
    named itself, so exactly one of the two identities is ever set. The cost is
    an over-counted inventory — three instrumented services on one machine are
    three nodes — and it is the lesser evil, because merging them averaged
    unrelated machines into one row of meaningless numbers.
    """

    host: str
    container: str
    reporter: str
    reporter_instance: str

    @property
    def kind(self) -> NodeKind:
        if self.container:
            return "container"
        if self.host:
            return "host"
        # Not a host: nothing in the resource said where these readings came from.
        return "reporter"


@dataclass(frozen=True, slots=True)
class Reading:
    """One series, collapsed to its first and last point inside the window."""

    metric_name: str
    unit: str
    attributes: dict[str, Any]
    earliest_value: float
    latest_value: float
    first_seen: datetime
    last_seen: datetime

    def attribute(self, keys: Sequence[str]) -> str:
        """The first of these attribute spellings this series carries."""
        for key in keys:
            found = _attribute_text(self.attributes, key)
            if found:
                return found
        return ""

    @property
    def elapsed_seconds(self) -> float:
        return (self.last_seen - self.first_seen).total_seconds()


async def list_nodes(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
) -> NodeListResponse:
    """Every host, container and reporting process that sent one of these metrics."""
    rows = await repository.query(
        queries.NODE_READINGS_SQL,
        _read_parameters(project_id, from_time, to_time) | {"max_series": MAX_SERIES},
    )

    by_node: dict[NodeKey, list[Reading]] = defaultdict(list)
    for row in rows:
        by_node[_node_key(row)].append(_to_reading(row))

    return NodeListResponse(
        nodes=[
            _summarize(key, readings) for key, readings in sorted(by_node.items(), key=_ordered)
        ],
        truncated=len(rows) >= MAX_SERIES,
    )


async def list_signals(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
) -> SignalListResponse:
    """Which infrastructure metrics have arrived, and how much of each."""
    rows = await repository.query(
        queries.SIGNALS_SEEN_SQL, _read_parameters(project_id, from_time, to_time)
    )
    return SignalListResponse(
        signals=[
            SignalSummary(
                metric_name=values.text(row["metric_name"]),
                unit=values.text(row["metric_unit"]),
                points=int(row["points"]),
                series=int(row["series"]),
                nodes=int(row["nodes"]),
                last_seen=values.nanos_to_datetime(row["latest_nanos"]),
            )
            for row in rows
        ]
    )


def _read_parameters(project_id: str, from_time: datetime, to_time: datetime) -> dict[str, Any]:
    received_from, received_to = receive_window(from_time, to_time)
    return {
        "project_id": project_id,
        "metric_names": list(conventions.INFRASTRUCTURE_METRIC_NAMES),
        "from_time": from_time,
        "to_time": to_time,
        "received_from": received_from,
        "received_to": received_to,
    }


def _attribute_text(attributes: dict[str, Any], key: str) -> str:
    """One attribute as text — flat or nested, string or number.

    Two shapes have to be handled and `values.attribute` handles neither alone.
    ClickHouse's JSON type reads a dot as a path separator, so a dotted OTLP key
    comes back nested; and a logical CPU number arrives as a JSON integer, which
    a string-only read drops — collapsing a twelve-CPU host into one group whose
    modes sum to twelve rather than to one.
    """
    found: Any = attributes.get(key)
    if found is None:
        found = attributes
        for part in key.split("."):
            if not isinstance(found, dict):
                return ""
            found = found.get(part)

    # `bool` is an `int` in Python, and "True" is not an attribute value.
    if found is None or isinstance(found, bool) or not isinstance(found, str | int | float):
        return ""
    return str(found)


def _node_key(row: dict[str, Any]) -> NodeKey:
    return NodeKey(
        host=values.text(row["host"]),
        container=values.text(row["container"]),
        reporter=values.text(row["reporter"]),
        reporter_instance=values.text(row["reporter_instance"]),
    )


def _ordered(entry: tuple[NodeKey, list[Reading]]) -> tuple[str, str, str, str]:
    key = entry[0]
    return key.host, key.container, key.reporter, key.reporter_instance


def _to_reading(row: dict[str, Any]) -> Reading:
    return Reading(
        metric_name=values.text(row["metric_name"]),
        unit=values.text(row["metric_unit"]),
        attributes=values.json_object(row["attributes_json"]),
        earliest_value=float(row["earliest_value"]),
        latest_value=float(row["latest_value"]),
        first_seen=values.nanos_to_datetime(row["earliest_nanos"]),
        last_seen=values.nanos_to_datetime(row["latest_nanos"]),
    )


# ------------------------------------------------------------------- roll-ups


def _summarize(key: NodeKey, readings: Sequence[Reading]) -> NodeSummary:
    memory_used = _memory_used_bytes(readings)
    memory_limit = _memory_limit_bytes(readings)
    filesystem_used = _sum_of(readings, conventions.SYSTEM_FILESYSTEM_USAGE, _used_filesystem)
    filesystem_limit = _filesystem_limit_bytes(readings)

    return NodeSummary(
        kind=key.kind,
        host=key.host,
        container=key.container,
        reporter=key.reporter,
        reporter_instance=key.reporter_instance,
        cpu_utilization=_cpu_utilization(readings),
        cpu_cores_used=_sum_of(readings, conventions.CONTAINER_CPU_USAGE),
        memory_used_bytes=_as_bytes(memory_used),
        memory_limit_bytes=_as_bytes(memory_limit),
        memory_utilization=_memory_utilization(readings, memory_used, memory_limit),
        filesystem_used_bytes=_as_bytes(filesystem_used),
        filesystem_limit_bytes=_as_bytes(filesystem_limit),
        filesystem_utilization=_ratio(filesystem_used, filesystem_limit),
        disk_bytes_per_second=_byte_rate(
            readings, conventions.SYSTEM_DISK_IO, conventions.CONTAINER_DISK_IO
        ),
        network_bytes_per_second=_byte_rate(
            readings, conventions.SYSTEM_NETWORK_IO, conventions.CONTAINER_NETWORK_IO
        ),
        first_seen=min(reading.first_seen for reading in readings),
        last_seen=max(reading.last_seen for reading in readings),
        signals=sorted({reading.metric_name for reading in readings}),
    )


def _named(readings: Iterable[Reading], metric_name: str) -> list[Reading]:
    return [reading for reading in readings if reading.metric_name == metric_name]


def _cpu_utilization(readings: Sequence[Reading]) -> float | None:
    """The fraction of this node's CPUs in use, averaged over logical CPUs."""
    per_cpu: dict[str, list[Reading]] = defaultdict(list)
    for reading in _named(readings, conventions.SYSTEM_CPU_UTILIZATION):
        per_cpu[reading.attribute(conventions.CPU_NUMBER_ATTRIBUTES)].append(reading)

    busy = [_busy_fraction(group) for group in per_cpu.values()]
    measured = [fraction for fraction in busy if fraction is not None]
    return sum(measured) / len(measured) if measured else None


def _busy_fraction(one_cpu: Sequence[Reading]) -> float | None:
    """How much of one logical CPU is not idle.

    The modes of a CPU sum to 1, so the busy modes are the answer directly. A
    producer reporting only `idle` is answered by its complement instead.

    **A reading whose mode we cannot name counts for nothing.** Treating an
    unnamed mode as busy is not a conservative default, it is a fabricated one:
    idle is most of the 1.0 the modes sum to, so counting it as work reported a
    3%-busy host as fully saturated. Answering "not measured" is the only honest
    reading of a point we cannot interpret.
    """
    named = [(reading, _cpu_mode(reading)) for reading in one_cpu]
    interpreted = [(reading, mode) for reading, mode in named if mode]
    if not interpreted:
        return None

    working = [
        reading.latest_value for reading, mode in interpreted if mode != conventions.CPU_IDLE_MODE
    ]
    if working:
        return sum(working)

    idle = [
        reading.latest_value for reading, mode in interpreted if mode == conventions.CPU_IDLE_MODE
    ]
    return 1.0 - sum(idle)


def _cpu_mode(reading: Reading) -> str:
    """Which slice of CPU time a point measures, under any known spelling."""
    return reading.attribute(conventions.CPU_MODE_ATTRIBUTES)


def _memory_used_bytes(readings: Sequence[Reading]) -> float | None:
    """Memory in use, from whichever of the two conventions this node speaks."""
    system = _sum_of(readings, conventions.SYSTEM_MEMORY_USAGE, _used_memory)
    if system is not None:
        return system
    return _sum_of(readings, conventions.CONTAINER_MEMORY_USAGE)


def _memory_limit_bytes(readings: Sequence[Reading]) -> float | None:
    """Total memory, reported directly or reconstructed from the states.

    `system.memory.usage`'s states partition the whole, so their sum is the
    total when the collector does not send `system.memory.limit`. For a
    container the same holds of used plus available.
    """
    declared = _sum_of(readings, conventions.SYSTEM_MEMORY_LIMIT)
    if declared is not None:
        return declared

    from_states = _sum_of(readings, conventions.SYSTEM_MEMORY_USAGE)
    if from_states is not None:
        return from_states

    used = _sum_of(readings, conventions.CONTAINER_MEMORY_USAGE)
    available = _sum_of(readings, conventions.CONTAINER_MEMORY_AVAILABLE)
    return None if used is None or available is None else used + available


def _memory_utilization(
    readings: Sequence[Reading], used: float | None, limit: float | None
) -> float | None:
    """The reported fraction, or the one the two totals imply."""
    reported = _sum_of(readings, conventions.SYSTEM_MEMORY_UTILIZATION, _used_memory)
    return reported if reported is not None else _ratio(used, limit)


def _filesystem_limit_bytes(readings: Sequence[Reading]) -> float | None:
    declared = _sum_of(readings, conventions.SYSTEM_FILESYSTEM_LIMIT)
    if declared is not None:
        return declared
    # Same reasoning as memory: the states of `usage` partition the volume.
    return _sum_of(readings, conventions.SYSTEM_FILESYSTEM_USAGE)


def _used_memory(reading: Reading) -> bool:
    return reading.attribute(conventions.MEMORY_STATE_ATTRIBUTES) == conventions.MEMORY_USED_STATE


def _used_filesystem(reading: Reading) -> bool:
    return (
        reading.attribute(conventions.FILESYSTEM_STATE_ATTRIBUTES)
        == conventions.FILESYSTEM_USED_STATE
    )


def _sum_of(
    readings: Sequence[Reading],
    metric_name: str,
    matching: Callable[[Reading], bool] | None = None,
) -> float | None:
    """The latest values of one metric, added up. None when none were sent."""
    selected = _named(readings, metric_name)
    if matching is not None:
        selected = [reading for reading in selected if matching(reading)]
    return sum(reading.latest_value for reading in selected) if selected else None


def _byte_rate(readings: Sequence[Reading], *metric_names: str) -> float | None:
    """Bytes per second across every series of these counters.

    Each series is divided by its own elapsed time before the sum, because two
    interfaces need not have been observed over the same span. A series whose
    value fell saw an exporter restart and is left out — its delta describes the
    restart, not the traffic.
    """
    rates = [
        (reading.latest_value - reading.earliest_value) / reading.elapsed_seconds
        for metric_name in metric_names
        for reading in _named(readings, metric_name)
        if reading.elapsed_seconds > 0 and reading.latest_value >= reading.earliest_value
    ]
    return sum(rates) if rates else None


def _ratio(part: float | None, whole: float | None) -> float | None:
    if part is None or whole is None or whole <= 0:
        return None
    return part / whole


def _as_bytes(value: float | None) -> int | None:
    """Byte counts go on the wire as integers; the store keeps them as floats."""
    return None if value is None else int(value)
