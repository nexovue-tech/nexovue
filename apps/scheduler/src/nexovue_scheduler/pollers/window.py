"""The stretch of time one poll covers.

A poller reports a value that describes an interval, not an instant: CloudWatch
averages over a period, and `pg_stat_statements` accumulates since the last
reset. OTLP has a field for exactly that — `start_time_unix_nano` alongside
`time_unix_nano` — and both frozen door 4b fixtures set it. A point with only an
end time reads as a spot measurement, which is a different claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

NANOS_PER_SECOND: Final = 1_000_000_000


@dataclass(frozen=True, slots=True)
class PollWindow:
    """The interval a poll's readings describe, as UTC instants."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"poll window ends before it starts: {self.start} -> {self.end}")

    @classmethod
    def ending_now(cls, *, seconds: int, now: datetime | None = None) -> PollWindow:
        end = now or datetime.now(UTC)
        return cls(start=end - timedelta(seconds=seconds), end=end)

    @property
    def start_unix_nano(self) -> int:
        return to_unix_nano(self.start)

    @property
    def end_unix_nano(self) -> int:
        return to_unix_nano(self.end)


def to_unix_nano(moment: datetime) -> int:
    """Nanoseconds since the epoch. Naive datetimes are refused, not assumed UTC.

    A timestamp whose zone was guessed is a reading placed at the wrong point on
    someone's chart, and the guess is invisible afterwards.
    """
    if moment.tzinfo is None:
        raise ValueError(f"timestamp has no timezone: {moment!r}")
    return int(moment.timestamp() * NANOS_PER_SECOND)
