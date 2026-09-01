"""Whether a node is still reporting, and what "still" means.

`last_seen` on its own carries no meaning. A host reporting every ten seconds
and one reporting every ten minutes are not equally late at the same age, so a
fixed threshold is either deaf to the first or hysterical about the second —
which is the criticism Prometheus's own fixed five-minute lookback attracts. The
threshold here is therefore read off **the cadence the node's own data shows**.

Lateness is measured against the *end of the requested window*, never against
wall-clock now. For a live window the two are the same; for a window that ended
yesterday, "was this node still reporting when the window closed" is the only
form of the question that has an answer.

This cannot distinguish a machine that is down from one that merely stopped
sending, and it does not pretend to. It answers "we have not heard from this",
which is the honest reading of silence.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Final

# Silence for this many of a node's own reporting intervals is a pattern rather
# than a hiccup: at least two scheduled reports did not arrive. Prometheus waits
# about two scrape intervals before marking a target's series stale, and it has
# service discovery telling it the target is gone; silence is all we have, so we
# sit one interval above that.
STALE_AFTER_INTERVALS: Final = 3

# Under a minute, silence is scheduling jitter and one slow export. Without this
# floor a node reporting every second would be called stale by a GC pause.
MINIMUM_STALE_AGE: Final = timedelta(seconds=60)

# The fallback when the window holds too few points to show a cadence at all.
# Sixty seconds is both the SDK's default metric export interval and the
# Collector's default host-metrics collection interval, so five of them is the
# same order of lateness the measured rule allows, and it is the fixed staleness
# horizon Prometheus settled on for the same reason.
UNMEASURED_CADENCE_STALE_AGE: Final = timedelta(minutes=5)


def reporting_interval_seconds(series_intervals: Iterable[float]) -> float | None:
    """The cadence a node reports at, from the intervals of its own series.

    The median rather than the shortest: every system metric of one process
    leaves on one export timer, so the series agree, and taking the minimum
    would let a single jittery series shorten the whole node's deadline. None
    when no series held two points to measure between.
    """
    measured = [interval for interval in series_intervals if interval > 0]
    return statistics.median(measured) if measured else None


def has_stopped_reporting(
    *, last_seen: datetime, as_of: datetime, interval_seconds: float | None
) -> bool:
    """Whether this node went quiet before the window it was asked about ended."""
    return as_of - last_seen > _allowed_silence(interval_seconds)


def _allowed_silence(interval_seconds: float | None) -> timedelta:
    if interval_seconds is None:
        return UNMEASURED_CADENCE_STALE_AGE
    return max(timedelta(seconds=interval_seconds * STALE_AFTER_INTERVALS), MINIMUM_STALE_AGE)
