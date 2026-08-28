"""How telemetry values are spelled on the wire.

Timestamps serialize to millisecond ISO 8601 with a `Z`. Millisecond precision
because that is what a waterfall renders and what a person reads; the store
keeps nanoseconds and the duration fields carry the sub-millisecond detail.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Final

from pydantic import PlainSerializer

MILLISECOND_DIGITS: Final = 3
_MICROSECOND_DIGITS: Final = 6


def _to_iso_millis(value: datetime) -> str:
    """`2026-08-27T10:15:04.123Z` — always UTC, always three decimal places."""
    moment = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return (
        moment.strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{moment.microsecond:0{_MICROSECOND_DIGITS}d}"[:MILLISECOND_DIGITS]
        + "Z"
    )


IsoTimestamp = Annotated[datetime, PlainSerializer(_to_iso_millis, return_type=str)]
