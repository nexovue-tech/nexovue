"""A target's local day from its source's minutes: pure, no store, no clock (design,
*Status and days*; spec FR9, FR11, BR4).

- **Counts** are what the source counted, minute by minute. A minute with no row adds
  nothing: nothing missing is ever counted as good (BR4).
- **A quiet minute** had no requests and was not lost. It is real, and neither good
  nor bad (FR11).
- **A lost minute** is one nexovue's own intake failed in (`detection/holds.py`). It
  is not quiet; it lowers coverage. Whatever did arrive in it still counts.
- **Stretches.** Each day keeps its leading, trailing and longest quiet stretch, so a
  stretch from 22:00 to 02:00 reads as 4 hours from Postgres alone.

A day counts only from the source's first event and up to the settled minute.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from nexovue_platform import slo

from nexovue_scheduler.slos.store import MinuteCounts

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
) -> DayRow:
    """One day of one source, over [day_start, day_end), counted from the first event
    and up to `through` (the settled minute, exclusive)."""
    first = max(day_start, _floor_minute(first_event_at))
    end = min(day_end, through)
    good = total = quiet = lost_count = 0
    runs: list[int] = []
    run = 0
    leading: int | None = None
    minute = first
    while minute < end:
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


def longest_quiet(days: Sequence[DayRow]) -> int:
    """The longest quiet stretch across consecutive days, joining one day's trailing
    stretch to the next day's leading one, so a stretch can cross midnight."""
    longest = carried = 0
    for row in days:
        if (
            row.quiet_leading_minutes == row.quiet_minutes
            and not row.held_an_event
            and (row.lost_minutes == 0)
        ):
            carried += row.quiet_minutes
            longest = max(longest, carried)
            continue
        longest = max(longest, row.longest_quiet_minutes, carried + row.quiet_leading_minutes)
        carried = row.quiet_trailing_minutes
    return longest


def first_event(source: Mapping[datetime, MinuteCounts]) -> datetime | None:
    """The earliest minute with a request."""
    return min((minute for minute, counts in source.items() if counts.requests > 0), default=None)


def last_event(source: Mapping[datetime, MinuteCounts]) -> datetime | None:
    """The latest minute with a request."""
    return max((minute for minute, counts in source.items() if counts.requests > 0), default=None)


def _floor_minute(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)
