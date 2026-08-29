"""The arithmetic every per-minute rollup read repeats.

`endpoint_stats_1m` and `service_edges_1m` are the same shape: counters summed
per minute plus one `quantiles(0.5, 0.95, 0.99)` state. Rate, error rate and
the unpacking of that state are therefore written once, here, rather than twice.

It sits beside `window.py` rather than inside either domain package: a helper
two packages share is not the property of whichever one needed it first, and
`servicemap` importing from `services` would be a dependency between two screens
that have nothing to do with each other.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

# Exactly the levels the stored `AggregateFunction(quantiles(...), Float64)` was
# built with. `quantilesMerge` refuses a state whose levels differ, so these are
# not a preference — they are the column's own definition, repeated.
QUANTILE_LEVELS: Final = (0.5, 0.95, 0.99)
QUANTILE_ARGUMENTS: Final = ", ".join(str(level) for level in QUANTILE_LEVELS)

# ClickHouse arrays are 1-based, and `quantilesMerge` returns them in the order
# above.
P50_INDEX: Final = 1
P95_INDEX: Final = 2
P99_INDEX: Final = 3

SECONDS_PER_MINUTE: Final = 60.0


def window_minutes(from_time: datetime, to_time: datetime) -> float:
    """How long the window is, in minutes. Never zero: `resolve_window` refuses one."""
    return (to_time - from_time).total_seconds() / SECONDS_PER_MINUTE


def per_minute(count: int, minutes: float) -> float:
    """A rate, so two windows of different lengths can be compared."""
    return count / minutes if minutes > 0 else 0.0


def failure_ratio(errors: int, total: int) -> float:
    """The share of calls that failed, as a fraction. Nothing measured is 0.0."""
    return errors / total if total > 0 else 0.0


class Latency:
    """The three quantiles a merged rollup row carries, in milliseconds.

    ClickHouse hands back one array; naming its positions here keeps the index
    arithmetic out of every caller.
    """

    __slots__ = ("p50_ms", "p95_ms", "p99_ms")

    def __init__(self, quantiles: object) -> None:
        values = list(quantiles) if isinstance(quantiles, list | tuple) else []
        self.p50_ms = _at(values, P50_INDEX)
        self.p95_ms = _at(values, P95_INDEX)
        self.p99_ms = _at(values, P99_INDEX)


def _at(values: list[object], one_based_index: int) -> float:
    """One quantile, or zero when the group held no rows to merge."""
    position = one_based_index - 1
    if position >= len(values):
        return 0.0
    value = values[position]
    return float(value) if isinstance(value, int | float) else 0.0
