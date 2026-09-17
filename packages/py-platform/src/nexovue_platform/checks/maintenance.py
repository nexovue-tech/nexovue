"""Whether a moment falls inside a maintenance window.

One definition, because two readers need it: the scheduler, which marks a run
taken during a window and changes no state for it, and the API, which says
"in maintenance" on the check page. If they disagreed a check could be shown in
maintenance while its failure was being recorded as an outage.

Recurrence is fixed intervals in UTC — daily or weekly from the first
occurrence. A window at 02:00 local time moves by an hour across a daylight
saving change; that is stated rather than hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

Recurrence = Literal["none", "daily", "weekly"]
RECURRENCES: Final[tuple[Recurrence, ...]] = ("none", "daily", "weekly")

_PERIOD: Final[dict[str, timedelta]] = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}


@dataclass(frozen=True, slots=True)
class WindowSchedule:
    starts_at: datetime
    ends_at: datetime
    recurrence: Recurrence = "none"
    # The last moment a recurring window may begin. None repeats indefinitely.
    repeat_until: datetime | None = None


def is_active(window: WindowSchedule, at: datetime) -> bool:
    if at < window.starts_at:
        return False
    if window.recurrence == "none":
        return at < window.ends_at

    period = _PERIOD[window.recurrence]
    duration = window.ends_at - window.starts_at
    occurrence_start = window.starts_at + period * ((at - window.starts_at) // period)
    if window.repeat_until is not None and occurrence_start > window.repeat_until:
        return False
    return at < occurrence_start + duration


def any_active(windows: list[WindowSchedule], at: datetime) -> bool:
    return any(is_active(window, at) for window in windows)
