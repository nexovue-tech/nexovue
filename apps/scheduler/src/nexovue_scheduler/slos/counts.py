"""A target's local day from its source's minutes: pure, no store, no clock (design,
*Status and days*; spec FR9, FR11, BR4).

- **Counts** are what the source counted, minute by minute. A minute with no row adds
  nothing: nothing missing is ever counted as good (BR4).
- **A quiet minute** had no requests and was not lost. It is real, and neither good
  nor bad (FR11).
- **A lost minute** is one nexovue's own intake failed in (`detection/holds.py`). It
  is not quiet; it lowers coverage. Whatever did arrive in it still counts.
- **Stretches.** Each day keeps its leading, trailing and longest quiet stretch, so a
  stretch from 22:00 to 02:00 reads as 4 hours from Postgres alone
  (`nexovue_platform.slo.longest_quiet`, which the API's months read too).

A day counts only from the source's first event and up to the settled minute.

**Each kind's minutes** become one source before a day is counted: a latency target's
failures are its slow requests (FR12); a service availability target's left-out
endpoints are subtracted minute by minute, only where the service has a row, so a
minute the service cap dropped never goes negative (FR34). **A check** is counted by
runs, not minutes: what its interval expected, what never came, and what ran in a
maintenance window (FR11). It has no quiet minutes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from nexovue_platform import slo

from nexovue_scheduler.slos.store import CheckMinute, LatencyMinute, MinuteCounts

_MINUTE: Final = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class DayRow:
    """`slo_daily_counts`' counted columns for one target and one local day."""

    good: int
    total: int
    failures: int
    quiet_minutes: int
    lost_minutes: int
    quiet_leading_minutes: int
    quiet_trailing_minutes: int
    longest_quiet_minutes: int
    # A check's runs (FR11); None for a request kind.
    expected_runs: int | None = None
    missed_runs: int | None = None
    maintenance_runs: int | None = None
    # Minutes a person left out (FR29, BR6): neither good nor bad, nor quiet, nor lost.
    excluded_minutes: int = 0

    @property
    def held_an_event(self) -> bool:
        return self.total > 0


def day_row(
    source: Mapping[datetime, MinuteCounts],
    *,
    day_start: datetime,
    day_end: datetime,
    through: datetime,
    first_event_at: datetime,
    lost: frozenset[datetime],
    excluded: frozenset[datetime] = frozenset(),
) -> DayRow:
    """One day of one source, over [day_start, day_end), counted from the first event
    and up to `through` (the settled minute, exclusive). An excluded minute adds nothing
    and ends a quiet stretch, as an event does (BR6)."""
    first = max(day_start, _floor_minute(first_event_at))
    end = min(day_end, through)
    good = total = quiet = lost_count = excluded_count = 0
    runs: list[int] = []
    run = 0
    leading: int | None = None
    minute = first
    while minute < end:
        if minute in excluded:
            excluded_count += 1
            if leading is None:
                leading = run
            runs.append(run)
            run = 0
            minute += _MINUTE
            continue
        counts = source.get(minute)
        requests = counts.requests if counts is not None else 0
        if requests > 0:
            total += requests
            good += requests - (counts.failures if counts is not None else 0)
        if minute in lost:
            lost_count += 1
        if requests == 0 and minute not in lost:
            quiet += 1
            run += 1
        else:
            if leading is None:
                leading = run
            runs.append(run)
            run = 0
        minute += _MINUTE
    runs.append(run)
    return DayRow(
        good=good,
        total=total,
        failures=total - good,
        quiet_minutes=quiet,
        lost_minutes=lost_count,
        quiet_leading_minutes=run if leading is None else leading,
        quiet_trailing_minutes=run,
        longest_quiet_minutes=max(runs),
        excluded_minutes=excluded_count,
    )


def day_rows_by_local_day(
    source: Mapping[datetime, MinuteCounts],
    *,
    days: Iterable[date],
    zone: ZoneInfo,
    through: datetime,
    first_event_at: datetime,
    lost: frozenset[datetime],
) -> dict[date, DayRow]:
    """A row for each local day in `days` that the window has reached."""
    rows: dict[date, DayRow] = {}
    for day in days:
        start, end = slo.day_bounds(day, zone)
        if end <= first_event_at or start >= through:
            continue
        rows[day] = day_row(
            source,
            day_start=start,
            day_end=end,
            through=through,
            first_event_at=first_event_at,
            lost=lost,
        )
    return rows


def first_event(source: Mapping[datetime, MinuteCounts]) -> datetime | None:
    """The earliest minute with a request."""
    return min((minute for minute, counts in source.items() if counts.requests > 0), default=None)


def last_event(source: Mapping[datetime, MinuteCounts]) -> datetime | None:
    """The latest minute with a request."""
    return max((minute for minute, counts in source.items() if counts.requests > 0), default=None)


# ----------------------------------------------------------- each kind's source


def without_left_out(
    service: Mapping[datetime, MinuteCounts], left_out: Iterable[Mapping[datetime, MinuteCounts]]
) -> dict[datetime, MinuteCounts]:
    """A service's minutes less its left-out endpoints' (FR34): the platform's rule, which
    the API's chart reads too. Left-out requests are not activity, so a minute of probes
    alone is quiet."""
    counted = slo.without_left_out(
        {minute: (counts.requests, counts.failures) for minute, counts in service.items()},
        [
            {minute: (counts.requests, counts.failures) for minute, counts in endpoint.items()}
            for endpoint in left_out
        ],
    )
    return {minute: MinuteCounts(*tally) for minute, tally in counted.items()}


def latency_minutes(
    minutes: Mapping[datetime, LatencyMinute], boundary_index: int
) -> dict[datetime, MinuteCounts]:
    """A latency target's minutes: every request, and as failures the slow ones, above
    the boundary (FR12). A fast failure counts as good (FR4)."""
    counted: dict[datetime, MinuteCounts] = {}
    for minute, reading in minutes.items():
        good = min(reading.requests, slo.at_or_under(reading.buckets, boundary_index))
        counted[minute] = MinuteCounts(reading.requests, reading.requests - good)
    return counted


def check_day_row(
    runs: Mapping[datetime, CheckMinute],
    *,
    day_start: datetime,
    day_end: datetime,
    through: datetime,
    first_event_at: datetime,
    interval_seconds: int,
    locations: int,
    excluded: frozenset[datetime] = frozenset(),
) -> DayRow:
    """One day of one check's runs, over [day_start, day_end), from its first run and up
    to `through`. Expected runs are what its interval asks of each location it runs at;
    a run that is neither counted nor in maintenance is missed, so a stopped prober, an
    unmeasured outcome or a lost reading lowers coverage (FR11, AC67)."""
    first = max(day_start, _floor_minute(first_event_at))
    end = min(day_end, through)
    inside = [
        reading
        for minute, reading in runs.items()
        if first <= minute < end and minute not in excluded
    ]
    counted = sum(reading.counted for reading in inside)
    maintenance = sum(reading.maintenance for reading in inside)
    left_out = len([minute for minute in excluded if first <= minute < end])
    seconds = max(0, int((end - first).total_seconds()) - left_out * _MINUTE.seconds)
    expected = seconds // interval_seconds * locations
    good = sum(reading.up for reading in inside)
    return DayRow(
        good=good,
        total=counted,
        failures=counted - good,
        quiet_minutes=0,
        lost_minutes=0,
        quiet_leading_minutes=0,
        quiet_trailing_minutes=0,
        longest_quiet_minutes=0,
        expected_runs=expected,
        missed_runs=max(0, expected - counted - maintenance),
        maintenance_runs=maintenance,
        excluded_minutes=left_out,
    )


def first_run(runs: Mapping[datetime, CheckMinute]) -> datetime | None:
    """The earliest minute with a confirmed run."""
    return min((minute for minute, reading in runs.items() if reading.seen > 0), default=None)


def last_run(runs: Mapping[datetime, CheckMinute]) -> datetime | None:
    """The latest minute with a confirmed run: what **No recent runs** is measured from."""
    return max((minute for minute, reading in runs.items() if reading.seen > 0), default=None)


def _floor_minute(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)
