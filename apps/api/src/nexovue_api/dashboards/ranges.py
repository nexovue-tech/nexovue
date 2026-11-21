"""The live windows a dashboard may name (F20, FR10, FR52).

One constant for both a definition's `default_range` and the batch read's `range`, so a
saved default and a read cannot disagree about what "the last hour" is. The console's
`shared/selection.ts` offers the same ids, and 30 days besides while a dashboard is open;
`test_dashboard_definition.py` holds the two lists together (AC141).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final, Literal

from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.window import resolve_window

RangeId = Literal["15m", "1h", "6h", "24h", "7d", "30d"]

RANGES: Final[dict[str, timedelta]] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def window_for(
    range_id: RangeId | None, from_time: datetime | None, to_time: datetime | None, *, now: datetime
) -> tuple[datetime, datetime]:
    """The window a dashboard read names: a live range, or fixed times.

    A live range's end is rounded up to the next minute, so everyone on one dashboard
    in one minute asks for one window and shares its scans (FR10, AC130). Fixed times
    are used as given, as on `/services/overview`.
    """
    if range_id is None:
        return resolve_window(from_time, to_time)
    if from_time is not None or to_time is not None:
        raise InvalidRequestError("Name a `range` or fixed `from` and `to`, not both.")
    end = _next_minute(now)
    return end - RANGES[range_id], end


def _next_minute(moment: datetime) -> datetime:
    floor = moment.replace(second=0, microsecond=0)
    return floor if floor == moment else floor + timedelta(minutes=1)
