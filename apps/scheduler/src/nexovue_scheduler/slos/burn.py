"""Which burn tier holds, how severe it is, and how long what is left lasts: pure, no
store, no clock (design, *Burn*; spec FR14 to FR18).

For a tier with long window L and short window S:

- **It can judge** when L holds the minimum events (20, or 10 runs for a check), L is
  at least 90% measured, and S holds at least one event (FR16, FR17).
- **It holds** when it can judge, both windows burn faster than its limit, L holds at
  least 2 failures, and those failures reach the floor: half the budget the tier would
  nominally spend when it fires. At least half failing in both windows skips the floor,
  so a night failing completely still pages (FR14, AC92).
- **The limit** is the tier's rate, capped at 90% of the largest possible (FR15).

`breached` is True when any tier holds, None when none can judge, and False otherwise:
fewer than 2 failures is an answer, not a silence, so a burn can end (AC93).

The windows are summed here from the minutes a judging already reads, one statement per
source (design, *What each job does*): excluded minutes leave a window's events and its
coverage alike, and nexovue's lost minutes lower its coverage (FR17).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final, Literal

from nexovue_platform import slo

from nexovue_scheduler.slos.store import CheckMinute, MinuteCounts

# FR16's guesses, in the spec's register.
REQUEST_EVENTS_TO_JUDGE: Final = 20
CHECK_RUNS_TO_JUDGE: Final = slo.CHECK_RUNS_TO_JUDGE
FAILURES_TO_HOLD: Final = 2
# FR14: at least this share failing in both windows skips the floor (a guess).
SEVERE_SHARE: Final = 0.5
# FR14: a tier's failures must reach this share of the budget it nominally spends.
FLOOR_SHARE_OF_NOMINAL: Final = 0.5
# FR16: a check's short window holds at least this many of its intervals.
CHECK_INTERVALS_IN_SHORT: Final = 2
_HOUR: Final = timedelta(hours=1)
_MINUTE: Final = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class WindowCounts:
    """One window's events and failures, and how much of it was measured. `coverage`
    is None when nothing of the window can be measured yet."""

    events: int
    failures: int
    coverage: float | None


@dataclass(frozen=True, slots=True)
class TierReading:
    long: WindowCounts
    short: WindowCounts


@dataclass(frozen=True, slots=True)
class TierJudgment:
    tier: slo.Tier
    can_judge: bool
    holds: bool
    long_rate: float | None
    short_rate: float | None
    limit: float


@dataclass(frozen=True, slots=True)
class BurnJudgment:
    breached: bool | None
    tiers: tuple[TierJudgment, ...]
    # The fastest tier holding, whose severity and long-window rate are the burn's.
    holding: slo.Tier | None
    rate: float | None
    hours_left: float | None

    @property
    def severity(self) -> Literal["critical", "warning"] | None:
        return self.holding.severity if self.holding is not None else None

    @property
    def chart_tier(self) -> slo.Tier | None:
        """The tier the chart plots (design, *One target*): the one holding, or with
        none holding the slowest able to judge; None when none can."""
        if self.holding is not None:
            return self.holding
        judging = [judged.tier for judged in self.tiers if judged.can_judge]
        return judging[-1] if judging else None

    def stored(self) -> dict[str, Any]:
        """What `slo_status.tiers` keeps: each tier's figures at the minute judged, and
        the tier the chart plots, so the API reads the same numbers (AC109)."""
        chart = self.chart_tier
        return {
            "chart_tier": chart.name if chart is not None else None,
            "rate": self.rate,
            "hours_left": self.hours_left,
            "by_tier": {
                judged.tier.name: {
                    "can_judge": judged.can_judge,
                    "holds": judged.holds,
                    "long_rate": judged.long_rate,
                    "short_rate": judged.short_rate,
                    "limit": judged.limit,
                }
                for judged in self.tiers
            },
        }


def floor_of(tier: slo.Tier, *, objective: Decimal, window_days: int, budget: int | None) -> float:
    """FR14's floor: half the budget the tier nominally spends when it fires. While
    nothing is counted the budget is unknown, and the floor is FR16's 2 (AC103)."""
    if not budget:
        return FAILURES_TO_HOLD
    window = timedelta(days=window_days)
    nominal = slo.tier_limit(tier, objective, window_days) * (tier.long / window)
    return nominal * budget * FLOOR_SHARE_OF_NOMINAL


def short_window(tier: slo.Tier, *, interval_seconds: int | None) -> timedelta:
    """A tier's short window; a check's holds at least two of its runs (FR16, AC100)."""
    if interval_seconds is None:
        return tier.short
    return max(tier.short, timedelta(seconds=interval_seconds * CHECK_INTERVALS_IN_SHORT))


def judge(
    readings: Mapping[slo.TierName, TierReading],
    *,
    objective: Decimal,
    window_days: int,
    budget: int | None,
    left: int | None,
    is_check: bool,
) -> BurnJudgment:
    """Every tier, at one minute. `budget` is the failures the window allows on what is
    counted so far; `left`, what of it remains, for how long it lasts."""
    minimum = CHECK_RUNS_TO_JUDGE if is_check else REQUEST_EVENTS_TO_JUDGE
    judged = tuple(
        _judge_tier(
            tier,
            readings[tier.name],
            objective=objective,
            window_days=window_days,
            floor=floor_of(tier, objective=objective, window_days=window_days, budget=budget),
            minimum=minimum,
        )
        for tier in slo.TIERS
    )
    holding = next((each for each in judged if each.holds), None)
    if holding is not None:
        breached: bool | None = True
    elif any(each.can_judge for each in judged):
        breached = False
    else:
        breached = None
    return BurnJudgment(
        breached=breached,
        tiers=judged,
        holding=holding.tier if holding is not None else None,
        rate=holding.long_rate if holding is not None else None,
        hours_left=(
            _hours_left(readings[holding.tier.name].long, holding.tier, left)
            if holding is not None
            else None
        ),
    )


def _judge_tier(
    tier: slo.Tier,
    reading: TierReading,
    *,
    objective: Decimal,
    window_days: int,
    floor: float,
    minimum: int,
) -> TierJudgment:
    long, short = reading.long, reading.short
    limit = slo.tier_limit(tier, objective, window_days)
    long_rate = slo.burn_rate(long.failures, long.events, objective)
    short_rate = slo.burn_rate(short.failures, short.events, objective)
    can_judge = (
        long.events >= minimum
        and long.coverage is not None
        and long.coverage >= slo.COVERAGE_TO_JUDGE
        and short.events > 0
    )
    severe = _share(long) >= SEVERE_SHARE and _share(short) >= SEVERE_SHARE
    holds = (
        can_judge
        and long_rate is not None
        and short_rate is not None
        and long_rate > limit
        and short_rate > limit
        and long.failures >= FAILURES_TO_HOLD
        and (long.failures >= floor or severe)
    )
    return TierJudgment(
        tier=tier,
        can_judge=can_judge,
        holds=holds,
        long_rate=long_rate,
        short_rate=short_rate,
        limit=limit,
    )


def _share(counts: WindowCounts) -> float:
    return counts.failures / counts.events if counts.events else 0.0


def _hours_left(long: WindowCounts, tier: slo.Tier, left: int | None) -> float | None:
    """What is left over the failures per hour the holding tier saw (FR18)."""
    if left is None:
        return None
    if left <= 0:
        return 0.0
    per_hour = long.failures / (tier.long / _HOUR)
    return left / per_hour if per_hour > 0 else None


# ------------------------------------------------------------ windows from minutes


def window_counts(
    minutes: Mapping[datetime, MinuteCounts],
    *,
    end: datetime,
    length: timedelta,
    lost: frozenset[datetime],
    excluded: frozenset[datetime],
    since: datetime | None,
) -> WindowCounts:
    """A request window over [end - length, end), counted from `since` (the source's
    first event). An excluded minute adds nothing and is not considered; a lost minute
    is considered and not measured (FR11, FR17)."""
    start = end - length if since is None else max(end - length, _floor(since))
    events = failures = considered = measured = 0
    minute = start
    while minute < end:
        if minute not in excluded:
            considered += 1
            if minute not in lost:
                measured += 1
            counts = minutes.get(minute)
            if counts is not None:
                events += counts.requests
                failures += counts.failures
        minute += _MINUTE
    coverage = measured / considered if considered else None
    return WindowCounts(events=events, failures=failures, coverage=coverage)


def check_window_counts(
    runs: Mapping[datetime, CheckMinute],
    *,
    end: datetime,
    length: timedelta,
    interval_seconds: int,
    locations: int,
    excluded: frozenset[datetime],
    since: datetime | None,
) -> WindowCounts:
    """A check's window: its counted runs and their failures, and its coverage as runs
    measured over runs its interval expected outside maintenance (FR11)."""
    start = end - length if since is None else max(end - length, _floor(since))
    inside = [
        reading
        for minute, reading in runs.items()
        if start <= minute < end and minute not in excluded
    ]
    counted = sum(reading.counted for reading in inside)
    up = sum(reading.up for reading in inside)
    maintenance = sum(reading.maintenance for reading in inside)
    seconds = max(0, int((end - start).total_seconds())) - _MINUTE.seconds * len(
        {minute for minute in excluded if start <= minute < end}
    )
    expected = max(0, seconds) // interval_seconds * locations - maintenance
    coverage = min(1.0, counted / expected) if expected > 0 else None
    return WindowCounts(events=counted, failures=counted - up, coverage=coverage)


def _floor(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)
