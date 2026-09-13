"""What the infrastructure endpoints put on the wire."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp

# A `reporter` is not a machine and must never be presented as one: it is the
# process whose resource carried neither `host.name` nor `container.name`, named
# so its readings stay attached to something instead of merging with every other
# unnamed resource in the project.
NodeKind = Literal["host", "container", "reporter"]


class NodeSummary(BaseModel):
    """One host, container or reporting process, as of the end of the window.

    Every measurement is optional and null means *not reported*, never zero. A
    collector runs the scrapers it was configured with, so a host sending CPU
    and nothing else is ordinary, and a zero there would read as "idle" when the
    truth is "not measured". `signals` names what this node actually sent, so
    the screen can tell the two apart without guessing.
    """

    kind: NodeKind
    # Empty when the resource carried no `host.name`. A container running under
    # a collector that sets both carries both.
    host: str
    container: str

    # Who sent the readings, filled only when `kind` is "reporter" — the
    # resource's `service.name` and `service.instance.id`. The instance id
    # identifies a process, not a machine, and the Python SDK generates a fresh
    # one per start, so a restarted service becomes a new row.
    reporter: str = ""
    reporter_instance: str = ""

    # Fraction of the node's CPUs in use, 0 to 1. Hosts only: a container reports
    # cores consumed instead, which is a different quantity.
    cpu_utilization: float | None = None
    cpu_cores_used: float | None = None

    memory_used_bytes: int | None = None
    memory_limit_bytes: int | None = None
    memory_utilization: float | None = None

    # Summed across the node's volumes, counting a device mounted twice once.
    filesystem_used_bytes: int | None = None
    filesystem_limit_bytes: int | None = None
    filesystem_utilization: float | None = None

    # Rates, because the underlying metrics are counters that only rise. Read
    # across the window, so a short window gives a spikier number than a long
    # one — that is a property of the question, not of the node. Each disk and
    # each interface is counted once: see `independence.py`.
    disk_bytes_per_second: float | None = None
    network_bytes_per_second: float | None = None

    first_seen: IsoTimestamp
    last_seen: IsoTimestamp

    # How often this node reported, measured from its own points. It is what
    # gives `last_seen` a meaning: a minute of silence is nothing from a host
    # scraped every ten minutes and three missed reports from one scraped every
    # ten seconds. Null when the window held too few points to show a cadence.
    reporting_interval_seconds: float | None = None

    # True when this node went quiet before the requested window ended — more
    # than `liveness.STALE_AFTER_INTERVALS` of its own intervals with nothing.
    # It never means "down": telemetry cannot tell a dead machine from one that
    # stopped sending, and this says only that we have not heard from it.
    stale: bool
    # The metric names this node sent inside the window, in name order.
    signals: list[str] = Field(default_factory=list)


class NodeListResponse(BaseModel):
    """Every node that reported an infrastructure metric inside the window.

    An empty list means no collector is sending host metrics for this project.
    `/infrastructure/signals` distinguishes that from a fleet that has gone
    quiet.
    """

    nodes: list[NodeSummary] = Field(default_factory=list)
    # True when the read hit its series ceiling, so the list is a prefix rather
    # than the fleet. Reported rather than silently truncated.
    truncated: bool = False


class SignalSummary(BaseModel):
    """One infrastructure metric name, and how much of it has arrived."""

    metric_name: str
    unit: str
    points: int
    series: int
    nodes: int
    last_seen: IsoTimestamp


class SignalListResponse(BaseModel):
    """Which infrastructure metrics this project has ever sent in the window.

    The empty-state answer. Nothing here means the collector has no
    `hostmetrics` or `docker_stats` receiver enabled, which is a setup problem
    rather than an outage.
    """

    signals: list[SignalSummary] = Field(default_factory=list)


class NodeTrendPoint(BaseModel):
    """One bucket of one metric on one node."""

    bucket: IsoTimestamp
    # The mean within the bucket, not the reading at its edge: a host reporting every
    # fifteen seconds writes four points to a minute, and the last one is not the minute.
    value: float


class NodeTrend(BaseModel):
    """One metric's history on one node, oldest first.

    Identified by the same four fields `NodeSummary` carries, so a caller builds the same
    key for both rather than trusting an id to mean the same thing in two responses.
    """

    host: str
    container: str
    reporter: str
    reporter_instance: str
    metric_name: str
    points: list[NodeTrendPoint]


class NodeTrendResponse(BaseModel):
    window_start: IsoTimestamp
    window_end: IsoTimestamp
    bucket_seconds: int
    trends: list[NodeTrend] = Field(default_factory=list)
    # A fleet larger than one read can collapse. Reported rather than silently truncated,
    # for the same reason `NodeListResponse` reports it.
    truncated: bool = False
