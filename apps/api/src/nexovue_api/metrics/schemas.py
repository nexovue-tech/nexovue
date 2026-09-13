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


class HistogramLayout(BaseModel):
    """One bucket layout's distribution over the window.

    A layout is a whole answer on its own. More than one of these means the
    metric name arrived with incompatible bucket boundaries, and they are
    reported side by side rather than added together — see `queries.py`.
    """

    # The upper bounds, ascending. `bucket_counts` has one more entry: the last
    # counts everything above the last bound, which is why an estimate at the
    # top of the distribution can only be a floor.
    bounds: list[float] = Field(default_factory=list)
    bucket_counts: list[int] = Field(default_factory=list)
    # Observations in the window, which is the difference between the last
    # cumulative point and the first — not the number of rows read.
    count: int
    # None when the exporter sent no sum. A histogram is not required to carry
    # one, and inventing it from bucket midpoints would be a guess presented as
    # a measurement.
    sum: float | None = None
    # The exporter's own extremes, when it sent them. Exact where the estimates
    # are not, which is why they are worth carrying separately.
    min: float | None = None
    max: float | None = None
    # Which services reported this layout. The actionable half of a conflict:
    # two layouts means these services disagree about their own buckets.
    services: list[str] = Field(default_factory=list)
    series: int
    # **Estimates.** A percentile read off buckets is interpolated within the
    # bucket the rank falls in; it is as precise as the boundaries allow and no
    # more. The screen labels them as estimates for that reason.
    p50: float | None = None
    p75: float | None = None
    p90: float | None = None
    p95: float | None = None
    p99: float | None = None


class HistogramResponse(BaseModel):
    """One metric's distribution, per bucket layout."""

    metric_name: str
    unit: str
    description: str = ""
    window_start: IsoTimestamp
    window_end: IsoTimestamp
    layouts: list[HistogramLayout] = Field(default_factory=list)
    # False when the name arrived with more than one bucket layout, so nothing
    # was merged. The screen says so rather than drawing one chart that is the
    # sum of two incompatible ones.
    merged: bool = True
