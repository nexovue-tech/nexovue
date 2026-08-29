"""What the infrastructure endpoints put on the wire."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp

NodeKind = Literal["host", "container"]


class NodeSummary(BaseModel):
    """One host or container, as of the end of the requested window.

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

    # Fraction of the node's CPUs in use, 0 to 1. Hosts only: a container reports
    # cores consumed instead, which is a different quantity.
    cpu_utilization: float | None = None
    cpu_cores_used: float | None = None

    memory_used_bytes: int | None = None
    memory_limit_bytes: int | None = None
    memory_utilization: float | None = None

    # Summed across every mounted filesystem the node reported.
    filesystem_used_bytes: int | None = None
    filesystem_limit_bytes: int | None = None
    filesystem_utilization: float | None = None

    # Rates, because the underlying metrics are counters that only rise. Read
    # across the window, so a short window gives a spikier number than a long
    # one — that is a property of the question, not of the node.
    disk_bytes_per_second: float | None = None
    network_bytes_per_second: float | None = None

    first_seen: IsoTimestamp
    last_seen: IsoTimestamp
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
