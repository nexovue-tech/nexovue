"""Quiet hours on a channel (FR15, BR4, D7): when warnings wait and criticals do not.

One shape, validated by the API when a channel is saved and read by the scheduler
when a delivery is due, so the two cannot disagree about whether now is quiet.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

# Monday is 0, as `datetime.weekday` counts.
Weekday = Annotated[int, Field(ge=0, le=6)]


class QuietHours(BaseModel):
    """Every chosen day, from `start` to `end` in `timezone`. An end before the start
    runs past midnight into the next day: 22:00 to 07:00 is a night, not an error."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timezone: str
    days: Annotated[list[Weekday], Field(min_length=1, max_length=7)]
    start: time
    end: time

    @field_validator("timezone")
    @classmethod
    def _known(cls, name: str) -> str:
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as unknown:
            raise ValueError(f"{name!r} is not a time zone") from unknown
        return name

    @field_validator("end")
    @classmethod
    def _not_empty(cls, end: time, info: ValidationInfo) -> time:
        start = info.data.get("start")
        if start is not None and start == end:
            raise ValueError("quiet hours cannot start and end at the same time")
        return end


def quiet_until(hours: QuietHours | None, now: datetime) -> datetime | None:
    """When the quiet period `now` falls in ends, or None if `now` is not quiet."""
    if hours is None:
        return None
    zone = ZoneInfo(hours.timezone)
    local = now.astimezone(zone)
    # A window that began yesterday evening may still be running this morning.
    for days_back in (0, 1):
        began_on = local.date() - timedelta(days=days_back)
        if began_on.weekday() not in hours.days:
            continue
        start = datetime.combine(began_on, hours.start, tzinfo=zone)
        end_day = began_on if hours.end > hours.start else began_on + timedelta(days=1)
        end = datetime.combine(end_day, hours.end, tzinfo=zone)
        # Against `now`, not `local`: datetimes sharing a zone compare by wall clock,
        # which disagrees with the instant returned when the clock skips an hour.
        if start <= now < end:
            return end.astimezone(now.tzinfo)
    return None


def parse_quiet_hours(raw: object) -> QuietHours | None:
    return None if raw is None else QuietHours.model_validate(raw)
