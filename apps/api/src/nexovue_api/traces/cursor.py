"""The opaque page marker for the trace list.

Keyset, not offset: `OFFSET 10000` makes ClickHouse produce and discard ten
thousand grouped rows, and a trace arriving mid-scroll shifts every later page
by one. A key the next page starts *after* has neither problem.

The value is opaque to the client but not secret — it names a position in a
result set the caller is already entitled to read, and the tenant and project
filters are applied regardless of what it contains.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Final

from nexovue_api.errors.exceptions import InvalidRequestError

_SEPARATOR: Final = ":"
_ENCODING: Final = "ascii"

MALFORMED_CURSOR_DETAIL: Final = "That cursor is not one this endpoint issued."


@dataclass(frozen=True, slots=True)
class TracePageCursor:
    """Where the next page starts: the last trace of the previous one."""

    start_time_nanos: int
    trace_id: str

    def encode(self) -> str:
        raw = f"{self.start_time_nanos}{_SEPARATOR}{self.trace_id}".encode(_ENCODING)
        return base64.urlsafe_b64encode(raw).decode(_ENCODING).rstrip("=")


def decode_cursor(value: str) -> TracePageCursor:
    """Read a cursor, or reject it. A malformed one is a client bug, not a page."""
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode(_ENCODING)).decode(_ENCODING)
        nanos, separator, trace_id = raw.partition(_SEPARATOR)
        if not separator or not trace_id:
            raise ValueError(raw)
        return TracePageCursor(start_time_nanos=int(nanos), trace_id=trace_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        raise InvalidRequestError(MALFORMED_CURSOR_DETAIL) from error
