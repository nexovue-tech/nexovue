"""Turning stored ClickHouse values into the ones the API contract names.

Decoding only — no SQL, no tenancy. It lives beside the chokepoint because the
encodings it undoes (`FixedString` padding, nanosecond integers, JSON columns
that render dotted keys as nesting) are properties of the store, not of any one
domain.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Final

NANOSECONDS_PER_SECOND: Final = 1_000_000_000
NANOSECONDS_PER_MICROSECOND: Final = 1_000
NANOSECONDS_PER_MILLISECOND: Final = 1_000_000

# A trace id is 32 hex characters and a span id is 16; both columns are
# `FixedString` and will not compare against anything else, so the shape is
# checked before a query runs rather than after it returns nothing.
TRACE_ID_LENGTH: Final = 32
SPAN_ID_LENGTH: Final = 16
_HEX_DIGITS: Final = frozenset("0123456789abcdefABCDEF")

# OTLP's "no id here". A `FixedString` is never zero-length, so an absent id
# arrives either NUL-padded or as all ASCII zeros depending on the writer.
ABSENT_TRACE_ID: Final = "0" * TRACE_ID_LENGTH
ABSENT_SPAN_ID: Final = "0" * SPAN_ID_LENGTH


def text(value: object) -> str:
    """FixedString columns arrive as bytes; everything on the wire is text."""
    if isinstance(value, bytes):
        return value.rstrip(b"\x00").decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def nanos_to_datetime(nanos: int) -> datetime:
    """Nanoseconds since the epoch, without going through a lossy float."""
    seconds, remainder = divmod(int(nanos), NANOSECONDS_PER_SECOND)
    return datetime.fromtimestamp(seconds, UTC) + timedelta(
        microseconds=remainder // NANOSECONDS_PER_MICROSECOND
    )


def is_trace_id(value: str) -> bool:
    return len(value) == TRACE_ID_LENGTH and all(character in _HEX_DIGITS for character in value)


def present_trace_id(value: object) -> str:
    """The trace id, or an empty string when the record carried no trace context."""
    decoded = text(value)
    return "" if decoded == ABSENT_TRACE_ID else decoded


def present_span_id(value: object) -> str:
    """The span id, or an empty string when the record carried no span context."""
    decoded = text(value)
    return "" if decoded == ABSENT_SPAN_ID else decoded


def json_object(raw: object) -> dict[str, Any]:
    """A JSON column's text as a dict. Anything unreadable is no attributes."""
    try:
        parsed = json.loads(text(raw))
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def json_array(raw: object) -> list[Any]:
    """A JSON column's text as a list. Anything unreadable is no elements."""
    try:
        parsed = json.loads(text(raw))
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def attribute(attributes: dict[str, Any], dotted_key: str) -> str:
    """One attribute by its OTLP name, whichever shape the store rendered.

    ClickHouse's JSON type reads a dot as a path separator, so the flat key
    `exception.stacktrace` an SDK sends comes back nested. Both spellings are
    tried, because which one a row holds depends on how it was written.
    """
    direct = attributes.get(dotted_key)
    if isinstance(direct, str):
        return direct

    walked: Any = attributes
    for part in dotted_key.split("."):
        if not isinstance(walked, dict):
            return ""
        walked = walked.get(part)
    return walked if isinstance(walked, str) else ""
