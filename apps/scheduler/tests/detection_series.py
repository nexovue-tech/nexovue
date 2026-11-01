"""Synthetic minute series for the detection tests: seeded, so every run judges the
same minutes, and noisy the way real traffic is, so a quiet result means something.

The scheduler has no numpy, so counts are drawn here: Poisson by Knuth's method
below 30, and by its normal approximation above, which is within a count or two
there and keeps a week of minutes fast.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from nexovue_scheduler.detection.method import BUCKET_COUNT, Minute

MINUTE: Final = timedelta(minutes=1)
# A steady latency shape over 11 adjacent buckets, in percent: its p95 is near bucket 27.
SHAPE: Final = (1, 3, 8, 15, 22, 20, 14, 9, 5, 2, 1)
FIRST_BUCKET: Final = 20
_KNUTH_BELOW: Final = 30.0


def poisson(rng: random.Random, mean: float) -> int:
    if mean <= 0:
        return 0
    if mean < _KNUTH_BELOW:
        limit, count, product = math.exp(-mean), 0, rng.random()
        while product > limit:
            count += 1
            product *= rng.random()
        return count
    return max(0, round(rng.gauss(mean, math.sqrt(mean))))


def binomial(rng: random.Random, trials: int, share: float) -> int:
    """Failures among `trials` requests; Poisson where the share is small."""
    if trials <= 0 or share <= 0:
        return 0
    if share >= 1:
        return trials
    return min(trials, poisson(rng, trials * share))


def buckets(requests: int, slower_by: int = 0) -> tuple[int, ...]:
    """The steady shape over `requests`, moved up `slower_by` buckets."""
    found = [0] * BUCKET_COUNT
    placed = 0
    for offset, percent in enumerate(SHAPE):
        count = requests * percent // 100
        found[FIRST_BUCKET + offset + slower_by] += count
        placed += count
    found[FIRST_BUCKET + 4 + slower_by] += requests - placed
    return tuple(found)


def minute(
    requests: int,
    failures: int = 0,
    *,
    slower_by: int = 0,
    endpoints: Mapping[str, tuple[int, int]] | None = None,
) -> Minute:
    return Minute(requests, failures, buckets(requests, slower_by), dict(endpoints or {}))


def series(
    first: datetime,
    count: int,
    rate: Callable[[datetime], float],
    *,
    failing: float = 0.01,
    seed: int = 1,
) -> dict[datetime, Minute]:
    """`count` minutes from `first`, each Poisson at `rate(minute)` requests, with
    `failing` of them failing."""
    rng = random.Random(seed)
    found = {}
    for index in range(count):
        at = first + index * MINUTE
        requests = poisson(rng, rate(at))
        found[at] = minute(requests, binomial(rng, requests, failing))
    return found


def daily(
    zone: ZoneInfo,
    *,
    peak: float,
    weekend: float = 1.0,
    night: float = 0.2,
) -> Callable[[datetime], float]:
    """A business day by the local clock: low at night, highest in the afternoon, and
    `weekend` of a weekday's level on Saturday and Sunday."""

    def rate(at: datetime) -> float:
        local = at.astimezone(zone)
        hours = local.hour + local.minute / 60
        shape = night + (1 - night) * max(0.0, math.sin(math.pi * (hours - 6) / 16))
        return peak * shape * (weekend if local.weekday() >= 5 else 1.0)

    return rate


def window(history: Mapping[datetime, Minute], end: datetime, width: int) -> list[Minute]:
    return [history.get(end - back * MINUTE, Minute.empty()) for back in range(width - 1, -1, -1)]


def span(first: datetime, last: datetime) -> Iterable[datetime]:
    at = first
    while at <= last:
        yield at
        at += MINUTE
