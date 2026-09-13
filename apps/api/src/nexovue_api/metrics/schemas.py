"""What the metrics endpoints put on the wire."""

from __future__ import annotations

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp


class MetricSummary(BaseModel):
    """One metric name, and what arrived under it."""

    metric_name: str
    # Every shape this name arrived in, sorted. More than one is a
    # misconfiguration worth seeing rather than an error worth hiding: nothing
    # stops a service sending `queue.depth` as a gauge and a sum, and a
    # catalogue that picked one would hide the half it discarded.
    shapes: list[str] = Field(default_factory=list)
    unit: str
    # Distinct attribute sets, which is what a series is.
    series: int
    # Distinct (series, instant) pairs, not rows — a metric row is not unique,
    # and a redelivered batch would otherwise inflate this.
    points: int
    last_seen: IsoTimestamp
    # Whether this console can draw this shape yet. False lists the metric with
    # its counts and says so: a metric a customer is paying to store should
    # appear even where we cannot yet read it.
    readable: bool


class MetricCatalogueResponse(BaseModel):
    """Everything this project reported, busiest first."""

    window_start: IsoTimestamp
    window_end: IsoTimestamp
    metrics: list[MetricSummary] = Field(default_factory=list)
    # True when the read hit its ceiling: a prefix of the catalogue, not the
    # catalogue.
    truncated: bool = False
