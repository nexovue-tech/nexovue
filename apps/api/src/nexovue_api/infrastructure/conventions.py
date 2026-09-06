"""The OpenTelemetry metric names an infrastructure screen is built from.

We do not invent names. A host reports through the collector's `hostmetrics`
receiver or an SDK's system-metrics instrumentation, and the *metric names* both
send are the semantic conventions' — so those are the only names read here.
Anything else is application telemetry and belongs on a different screen.

**Attribute keys are a different matter, and this is where reading only the
specification produced a wrong answer.** Semconv v1.27.0 renamed
`system.cpu.state` to `cpu.mode` and moved the sibling keys under namespaces,
but no producer followed: the Collector's `hostmetrics` cpuscraper and the
OpenTelemetry Python `SystemMetricsInstrumentor` both still emit a flat `state`,
a flat `cpu`, and flat `device`/`direction` (sources in
`docs/architecture/decisions` and verified against our own store below). Reading
only the namespaced spelling matched *no real traffic*, so every attribute below
is a tuple: the spelling producers actually send, then the spec's, then the
superseded one. First match wins.

**What our ClickHouse actually holds, checked 2026-08-30.** The
OpenTelemetry Demo run (P1-62) landed genuine host metrics under project
`01a051fa-…1018`: `system.cpu.utilization` (672 points, 48 series),
`system.memory.usage`, `system.memory.utilization`, `system.disk.io` and
`system.network.io`. Their attributes are `{"cpu": 3, "state": "idle"}`,
`{"state": "used"}` and `{"device": …, "direction": …}` — the flat spellings.
No resource carried a `host.name`; re-checked 2026-09-01 and still none did, so
those readings were identified by the process that reported them instead
(`queries.py`, "Node identity"). **That changed on 2026-09-06**: the demo now
runs a host-metrics collector of its own
(`tools/otel-demo/host-metrics-collector.yaml`) which sets `host.name`, and both
identities are visible side by side — a real host row, and the
`recommendation` service still falling through to reporter identity because the
Python SDK's instrumentation names no host. The fallback is not a bug and the
screen says so.

**No filesystem reading had ever arrived** either, and the reason was the
producer rather than the store: the Python `SystemMetricsInstrumentor` carries a
`# TODO Add _get_system_filesystem_usage` where that scraper would be. The
collector above has one, so `system.filesystem.usage` now arrives — it needs the
filesystems themselves bind-mounted, not only `/proc`, or every `statfs` fails.

Seven of the thirteen names below now arrive from that collector:
`system.cpu.utilization`, `system.memory.usage`, `system.memory.limit`,
`system.memory.utilization`, `system.filesystem.usage`, `system.disk.io` and
`system.network.io`. `system.filesystem.limit` is not one the scraper emits, and
the five `container.*` names still have no producer at all.
The Collector's `filesystemscraper` does send it, which is why `independence.py`
still has to deduplicate its mountpoints unverified against live rows.

**Container names, measured 2026-09-06 rather than assumed.** `docker_stats`
emits none of the specification's `container.*` names, so both spellings are
read here and the specification's are kept — `kubeletstats` sends three of them,
and a customer on Kubernetes should not need us to change.

The mapping was settled by looking at values, not names, and one of them is a
trap: **`container.cpu.utilization` is CPU cores, not a fraction.** `flagd`
reported 8.1664 of it while `docker stats` showed 955.86% for the same
container, and a fraction cannot exceed 1. It is therefore the same quantity as
the specification's `container.cpu.usage` under a misleading name, and it feeds
`cpu_cores_used`. Reading it into `cpu_utilization` would put an 8 in a column
that means 0 to 1.

`container.memory.usage.limit` is the exception that is not a synonym: it is the
limit itself, where the specification's `container.memory.available` is what
remains. Both reach `memory_limit_bytes`, the limit directly and the available
by adding it to what is used.

What is deliberately *not* done is renaming at ingest. A stored row keeps the
name its producer chose; only this read layer knows both vocabularies.

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

# ---- Docker's spellings for the same five quantities, from dockerstatsreceiver.
# Measured, not read off a name: see the header. `container.cpu.utilization` is
# cores despite the word, so it is a synonym of CONTAINER_CPU_USAGE and not of
# any utilization.
DOCKER_CONTAINER_CPU_USAGE: Final = "container.cpu.utilization"
DOCKER_CONTAINER_MEMORY_USAGE: Final = "container.memory.usage.total"
# The limit itself, where CONTAINER_MEMORY_AVAILABLE is what remains of it.
DOCKER_CONTAINER_MEMORY_LIMIT: Final = "container.memory.usage.limit"
# Cumulative bytes, carrying device_major/device_minor and an `operation` of
# read or write. There is no device *name*, so the whole-disk deduplication that
# `system.disk.io` needs finds nothing to do and every series is counted once.
DOCKER_CONTAINER_DISK_IO: Final = "container.blockio.io_service_bytes_recursive"
# Two metrics where `system.network.io` is one with a `direction` attribute.
# Summing both is what the system metric's own roll-up already does.
DOCKER_CONTAINER_NETWORK_RX: Final = "container.network.io.usage.rx_bytes"
DOCKER_CONTAINER_NETWORK_TX: Final = "container.network.io.usage.tx_bytes"

# Every spelling of one quantity, for the roll-ups to read. A node speaks one
# vocabulary or the other, never both, so these never double-count.
CONTAINER_CPU_USAGE_NAMES: Final = (CONTAINER_CPU_USAGE, DOCKER_CONTAINER_CPU_USAGE)
CONTAINER_MEMORY_USAGE_NAMES: Final = (CONTAINER_MEMORY_USAGE, DOCKER_CONTAINER_MEMORY_USAGE)

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
    DOCKER_CONTAINER_CPU_USAGE,
    DOCKER_CONTAINER_MEMORY_USAGE,
    DOCKER_CONTAINER_MEMORY_LIMIT,
    DOCKER_CONTAINER_DISK_IO,
    DOCKER_CONTAINER_NETWORK_RX,
    DOCKER_CONTAINER_NETWORK_TX,
)

# The counters whose stored value only rises. Their reading is a rate, and a
# fall between two points means the process restarted rather than that traffic
# went backwards.
CUMULATIVE_BYTE_COUNTERS: Final = frozenset(
    {
        SYSTEM_DISK_IO,
        SYSTEM_NETWORK_IO,
        CONTAINER_DISK_IO,
        CONTAINER_NETWORK_IO,
        DOCKER_CONTAINER_DISK_IO,
        DOCKER_CONTAINER_NETWORK_RX,
        DOCKER_CONTAINER_NETWORK_TX,
    }
)

# ------------------------------------------------------------ attribute names

# Each of these is an ordered list of spellings for one attribute, most likely
# first: what producers send, then what the specification says, then what the
# specification used to say. The first key a row carries wins.
#
# The order is deliberate and was wrong in the other direction until real host
# metrics arrived. A CPU point reads `{"cpu": 3, "state": "idle"}`, and against
# the spec-only spelling every mode came back unnamed — which made an idle host
# report as fully busy. The producer's spelling leads because the producer is
# what we receive.

# Which slice of CPU time a point measures.
CPU_MODE_ATTRIBUTES: Final[tuple[str, ...]] = ("state", "cpu.mode", "system.cpu.state")
CPU_IDLE_MODE: Final = "idle"

# Which logical CPU a point belongs to. Absent when the producer aggregates.
# Not comparable across producers: the Collector numbers from 0, the Python SDK
# from 1. That only matters for grouping, which needs distinctness and not order.
CPU_NUMBER_ATTRIBUTES: Final[tuple[str, ...]] = (
    "cpu",
    "cpu.logical_number",
    "system.cpu.logical_number",
)

MEMORY_STATE_ATTRIBUTES: Final[tuple[str, ...]] = ("state", "system.memory.state")
MEMORY_USED_STATE: Final = "used"

FILESYSTEM_STATE_ATTRIBUTES: Final[tuple[str, ...]] = ("state", "system.filesystem.state")
FILESYSTEM_USED_STATE: Final = "used"
FILESYSTEM_MOUNTPOINT_ATTRIBUTES: Final[tuple[str, ...]] = (
    "mountpoint",
    "system.filesystem.mountpoint",
)

# Which block device a disk or filesystem reading belongs to. `service` needs it
# to tell a partition from the disk that contains it, and one filesystem from the
# same one mounted twice — without it those series are added to themselves.
DEVICE_ATTRIBUTES: Final[tuple[str, ...]] = ("device", "system.device")

# Which network interface a reading belongs to. The Collector and the Python SDK
# both send `device`; semconv v1.27.0 named it `network.interface.name`.
NETWORK_INTERFACE_ATTRIBUTES: Final[tuple[str, ...]] = ("device", "network.interface.name")
