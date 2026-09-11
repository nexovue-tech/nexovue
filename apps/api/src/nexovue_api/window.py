"""The time window and page size a telemetry read is bounded by.

Shared by every domain that reads a window out of ClickHouse, so "the last
hour" means the same thing on every screen and the clamp is written once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from nexovue_api.errors.exceptions import InvalidRequestError

DEFAULT_WINDOW: Final = timedelta(hours=1)

# Every telemetry table is partitioned on `received_at`, our own clock, while
# callers ask questions about `timestamp`, the customer's. ClickHouse
# cannot relate the two, so a bound on one prunes nothing on the other. A query
# that wants partition pruning therefore widens its window by this much and
# bounds `received_at` with it as well.
#
# One day is one partition at each end. It covers ordinary ingest lag, a
# batching exporter, a retry backlog, and the arrival gap between two signals
# that travel independent pipelines. Telemetry that arrives more than a day
# outside its own window is not found by a windowed query — the alternative is
# reading every partition in the retention period on every request.
RECEIVE_CLOCK_SLACK: Final = timedelta(days=1)


def resolve_window(
    from_time: datetime | None, to_time: datetime | None
) -> tuple[datetime, datetime]:
    """The requested window, defaulting to the last hour."""
    end = to_time or datetime.now(UTC)
    start = from_time or end - DEFAULT_WINDOW
    if start >= end:
        raise InvalidRequestError("`from` must be earlier than `to`.")
    return start, end


def receive_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """The `received_at` range that can hold everything stamped within a window."""
    return start - RECEIVE_CLOCK_SLACK, end + RECEIVE_CLOCK_SLACK


def clamp_limit(requested: int | None, *, default: int, maximum: int) -> int:
    """The page size, never zero and never past what the endpoint will serve."""
    return default if requested is None else min(max(1, requested), maximum)
