"""Turning five tables of points into one catalogue."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.metrics import queries
from nexovue_api.metrics.schemas import (
    HistogramLayout,
    HistogramResponse,
    MetricCatalogueResponse,
    MetricSummary,
)
from nexovue_api.window import receive_window

# A catalogue is a list of names, not a report. Past a few hundred it stops
# being readable anyway, and the cap keeps the group-by across five tables
# bounded however many names a customer invents.
MAX_METRICS: Final = 500


async def list_metrics(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
) -> MetricCatalogueResponse:
    """Every metric name this project reported in the window, busiest first.

    Busiest first because a catalogue is read to find something, and the thing
    somebody is looking for is almost never the quietest name in it.
    """
    received_from, received_to = receive_window(from_time, to_time)
    rows = await repository.query(
        queries.CATALOGUE_SQL,
        {
            "project_id": project_id,
            "from_time": from_time,
            "to_time": to_time,
            "received_from": received_from,
            "received_to": received_to,
            # One past the cap, so a full catalogue is told apart from a cut one.
            "limit": MAX_METRICS + 1,
        },
    )

    return MetricCatalogueResponse(
        window_start=from_time,
        window_end=to_time,
        metrics=[_to_summary(row) for row in rows[:MAX_METRICS]],
        truncated=len(rows) > MAX_METRICS,
    )


def _to_summary(row: dict[str, Any]) -> MetricSummary:
    shapes = [values.text(shape) for shape in row["shapes"]]
    return MetricSummary(
        metric_name=values.text(row["metric_name"]),
        shapes=shapes,
        unit=values.text(row["metric_unit"]),
        series=int(row["series"]),
        points=int(row["points"]),
        last_seen=values.nanos_to_datetime(row["latest_nanos"]),
        # Readable when *every* shape it arrived in can be drawn. A name sent as
        # both a histogram and a summary cannot be drawn as one thing, and
        # calling it readable would promise a reader something the screen cannot
        # keep.
        readable=bool(shapes) and all(shape in queries.READABLE_SHAPES for shape in shapes),
    )


# The quantiles the screen draws. Fixed rather than requested: a histogram's
# precision comes from its buckets, and letting a caller ask for p99.9 off
# fifteen bounds would answer with a confidence the data cannot support.
ESTIMATED_QUANTILES: Final = (0.50, 0.75, 0.90, 0.95, 0.99)


async def read_histogram(
    repository: TenantScopedRepository,
    *,
    project_id: str,
    metric_name: str,
    from_time: datetime,
    to_time: datetime,
) -> HistogramResponse:
    """One histogram metric's distribution over the window, per bucket layout.

    More than one layout is returned as more than one distribution. They are
    never added: see `queries.HISTOGRAM_SQL` for the millisecond-plus-second
    case that happens in practice.
    """
    received_from, received_to = receive_window(from_time, to_time)
    bindings = {
        "project_id": project_id,
        "metric_name": metric_name,
        "from_time": from_time,
        "to_time": to_time,
        "received_from": received_from,
        "received_to": received_to,
    }
    rows = await repository.query(queries.HISTOGRAM_SQL, bindings)
    identity = await repository.query(
        queries.HISTOGRAM_IDENTITY_SQL,
        {"project_id": project_id, "metric_name": metric_name},
    )

    layouts = [_to_layout(row) for row in rows]
    return HistogramResponse(
        metric_name=metric_name,
        unit=values.text(identity[0]["metric_unit"]) if identity else "",
        description=values.text(identity[0]["metric_description"]) if identity else "",
        window_start=from_time,
        window_end=to_time,
        layouts=layouts,
        merged=len(layouts) <= 1,
    )


def _to_layout(row: dict[str, Any]) -> HistogramLayout:
    bounds = [float(bound) for bound in row["bounds"]]
    # Clamped at zero: a subtraction across a window can go slightly negative
    # when a series was re-exported out of order, and a negative count in a
    # distribution is not a reading anyone can act on.
    counts = [max(int(count), 0) for count in row["bucket_counts"]]
    total = max(int(row["total_count"]), 0)
    estimates = estimate_quantiles(bounds, counts, total)

    return HistogramLayout(
        bounds=bounds,
        bucket_counts=counts,
        count=total,
        sum=_optional_float(row["total_sum"]),
        min=_optional_float(row["low"]),
        max=_optional_float(row["high"]),
        services=[values.text(service) for service in row["services"]],
        series=int(row["series"]),
        p50=estimates[0],
        p75=estimates[1],
        p90=estimates[2],
        p95=estimates[3],
        p99=estimates[4],
    )


def _optional_float(value: Any) -> float | None:
    """A `Nullable(Float64)` as it is, never as a zero.

    `sum`, `min` and `max` are optional in OTLP. Zero is a reading a customer
    can act on and absence is not, so the two must not collapse into each other.
    """
    return None if value is None else float(value)


def estimate_quantiles(
    bounds: Sequence[float], counts: Sequence[int], total: int
) -> tuple[float | None, ...]:
    """Percentiles read off buckets, interpolated inside the bucket they land in.

    **Every one of these is an estimate and the response says so.** The true
    value is known only to lie within one bucket; this places it linearly inside
    that bucket, which is the same thing Prometheus' `histogram_quantile` does
    and carries the same caveat — the answer is exactly as precise as whoever
    chose the boundaries made it.

    Two edges are worth naming. The first bucket has no lower bound, and is read
    as starting at zero: every histogram this product reads is a duration or a
    size, and neither is negative. The last bucket has no upper bound, so a rank
    that falls in it can only be reported as the last boundary — a floor, not an
    estimate. Saying "at least 10s" is honest where interpolating to infinity is
    not.
    """
    if total <= 0 or not counts:
        return (None,) * len(ESTIMATED_QUANTILES)

    # Running total up to and including each bucket, so a rank can be located
    # with one pass per quantile rather than re-summing.
    cumulative: list[int] = []
    seen = 0
    for count in counts:
        seen += count
        cumulative.append(seen)

    # The buckets may not add up to `count`: they are separate columns, and a
    # subtraction across a window can leave them slightly apart. The ranks are
    # taken against what the buckets actually hold, so a quantile cannot fall
    # past the end of them.
    held = cumulative[-1]
    if held <= 0:
        return (None,) * len(ESTIMATED_QUANTILES)

    return tuple(_quantile(bounds, cumulative, held, quantile) for quantile in ESTIMATED_QUANTILES)


def _quantile(
    bounds: Sequence[float], cumulative: Sequence[int], held: int, quantile: float
) -> float:
    rank = quantile * held
    index = bisect_left(cumulative, rank)
    index = min(index, len(cumulative) - 1)

    # Above the last boundary, and there is nothing above it to interpolate
    # towards. The boundary is the most that can be claimed.
    if index >= len(bounds):
        return float(bounds[-1]) if bounds else 0.0

    upper = float(bounds[index])
    lower = float(bounds[index - 1]) if index > 0 else 0.0
    within = cumulative[index] - (cumulative[index - 1] if index > 0 else 0)
    if within <= 0:
        return upper

    already = cumulative[index - 1] if index > 0 else 0
    return lower + (upper - lower) * ((rank - already) / within)
