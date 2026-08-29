"""The OpenTelemetry metric names an infrastructure screen is built from.

We do not invent names. A host reports through the collector's `hostmetrics`
receiver and a container through `docker_stats`, and both follow the semantic
conventions — so the only names this package recognises are the ones those
conventions define. Anything else is application telemetry and belongs on a
different screen.

Names and instrument kinds checked against the specification on 2026-08-30:
`system.*` in semconv/system/system-metrics, `container.*` in
semconv/system/container-metrics. Every one of them is still marked
*Development* upstream, which is why they are listed here rather than assumed:
when a name changes, this module is the one file that changes with it.

**No such metric has ever reached our ClickHouse.** A distinct on `metric_name`
across all five metric tables on 2026-08-30 returned only synthetic-check,
demo-app and our own derived counters — nothing `system.*`, `container.*`,
`host.*` or `process.*`, and no resource carried a `host.name`. The endpoints
therefore return an empty list today. That is the correct answer to "which hosts
are reporting", not an error and not fabricated rows.

Kubernetes pod and node views are Phase 2 and deliberately absent.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------- host signals

# Gauge, fraction of one logical CPU. Split by `cpu.mode`, and the modes of one
# logical CPU sum to 1 — which is what lets `service` derive a busy fraction.
SYSTEM_CPU_UTILIZATION: Final = "system.cpu.utilization"

# UpDownCounter, bytes, split by `system.memory.state`.
SYSTEM_MEMORY_USAGE: Final = "system.memory.usage"
# UpDownCounter, bytes. Total memory available, unsplit.
SYSTEM_MEMORY_LIMIT: Final = "system.memory.limit"
# Gauge, fraction, split by `system.memory.state`.
SYSTEM_MEMORY_UTILIZATION: Final = "system.memory.utilization"

# UpDownCounter, bytes, split by mountpoint and `system.filesystem.state`.
SYSTEM_FILESYSTEM_USAGE: Final = "system.filesystem.usage"
SYSTEM_FILESYSTEM_LIMIT: Final = "system.filesystem.limit"

# Monotonic counters, bytes. A rate is the only readable form of these.
SYSTEM_DISK_IO: Final = "system.disk.io"
SYSTEM_NETWORK_IO: Final = "system.network.io"

# ----------------------------------------------------------- container signals

# Gauge, `{cpu}` — CPU cores consumed, not a fraction. Kept as a separate field
# from the host's utilization because the two are not the same quantity and
# rendering them in one column would misreport both.
CONTAINER_CPU_USAGE: Final = "container.cpu.usage"

# UpDownCounter, bytes.
CONTAINER_MEMORY_USAGE: Final = "container.memory.usage"
# UpDownCounter, bytes. Memory still available to the container.
CONTAINER_MEMORY_AVAILABLE: Final = "container.memory.available"

# Monotonic counters, bytes.
CONTAINER_DISK_IO: Final = "container.disk.io"
CONTAINER_NETWORK_IO: Final = "container.network.io"

# Every name the read asks ClickHouse for. Bound as a query parameter, never
# spliced into the statement.
INFRASTRUCTURE_METRIC_NAMES: Final[tuple[str, ...]] = (
    SYSTEM_CPU_UTILIZATION,
    SYSTEM_MEMORY_USAGE,
    SYSTEM_MEMORY_LIMIT,
    SYSTEM_MEMORY_UTILIZATION,
    SYSTEM_FILESYSTEM_USAGE,
    SYSTEM_FILESYSTEM_LIMIT,
    SYSTEM_DISK_IO,
    SYSTEM_NETWORK_IO,
    CONTAINER_CPU_USAGE,
    CONTAINER_MEMORY_USAGE,
    CONTAINER_MEMORY_AVAILABLE,
    CONTAINER_DISK_IO,
    CONTAINER_NETWORK_IO,
)

# The counters whose stored value only rises. Their reading is a rate, and a
# fall between two points means the process restarted rather than that traffic
# went backwards.
CUMULATIVE_BYTE_COUNTERS: Final = frozenset(
    {SYSTEM_DISK_IO, SYSTEM_NETWORK_IO, CONTAINER_DISK_IO, CONTAINER_NETWORK_IO}
)

# ------------------------------------------------------------ attribute names

# Which slice of CPU time a point measures. Renamed upstream from
# `system.cpu.state`; both spellings are read, because an older collector in a
# customer's cluster still sends the old one and reading only the new one would
# report every such host as idle.
CPU_MODE_ATTRIBUTE: Final = "cpu.mode"
LEGACY_CPU_STATE_ATTRIBUTE: Final = "system.cpu.state"
CPU_IDLE_MODE: Final = "idle"

# Which logical CPU a point belongs to. Absent when the collector aggregates.
CPU_NUMBER_ATTRIBUTE: Final = "cpu.logical_number"

MEMORY_STATE_ATTRIBUTE: Final = "system.memory.state"
MEMORY_USED_STATE: Final = "used"

FILESYSTEM_STATE_ATTRIBUTE: Final = "system.filesystem.state"
FILESYSTEM_USED_STATE: Final = "used"
