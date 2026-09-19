"""The opaque page marker for the audit log: the last entry of the previous page.

Keyset on `(at, id)`, newest first, over `ix_audit_log_org_id_at_id`. The value is
not secret — it names a position in rows the caller may already read, and the
tenant filter applies regardless of what it holds.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nexovue_api.errors.exceptions import InvalidRequestError

_SEPARATOR: Final = "|"
_ENCODING: Final = "ascii"

MALFORMED_CURSOR_DETAIL: Final = "That cursor is not one this endpoint issued."


@dataclass(frozen=True, slots=True)
class AuditPageCursor:
    at: datetime
    entry_id: uuid.UUID

    def encode(self) -> str:
        raw = f"{self.at.isoformat()}{_SEPARATOR}{self.entry_id}".encode(_ENCODING)
        return base64.urlsafe_b64encode(raw).decode(_ENCODING).rstrip("=")


def decode_cursor(value: str) -> AuditPageCursor:
    """Read a cursor, or reject it. A malformed one is a client bug, not a page."""
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode(_ENCODING)).decode(_ENCODING)
        at, separator, entry_id = raw.partition(_SEPARATOR)
        if not separator:
            raise ValueError(raw)
        moment = datetime.fromisoformat(at)
        if moment.tzinfo is None:
            raise ValueError(at)
        return AuditPageCursor(at=moment, entry_id=uuid.UUID(entry_id))
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        raise InvalidRequestError(MALFORMED_CURSOR_DETAIL) from error
