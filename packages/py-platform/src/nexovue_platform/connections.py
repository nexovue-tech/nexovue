"""What may cross to a tracker or into an email (F22, D39, D48).

A service name is someone else's words. Before it leaves this server — in an
issue (Step 4) or in the assignment email (FR5) — it becomes its strict form:
letters, digits and `_ / -` only, cut to 64, so it cannot read as a domain, a
mention or a sentence. A name that looks like a person's data — an `@` with text
on both sides, or a run of six or more digits — never leaves at all and reads as
the fixed words instead (AC82). The check runs before the strict form replaces
`@`, or `a@b` would slip through as `a_b`.

Step 3 grows this module with the connection vocabulary, and Step 4 with
`issue_text()`; every outbound rendering must come back here.
"""

from __future__ import annotations

import re
from typing import Final

# What an issue or email says when the name itself must not leave (D48).
A_SERVICE: Final = "a service"

# D39: long enough for real names, short enough that a name is not a payload.
STRICT_NAME_LENGTH: Final = 64

# A run of 6 or more digits reads as an id or a phone number, a guess (FR37).
_LOOKS_PERSONAL: Final = re.compile(r".@.|\d{6}")
_OUTSIDE_STRICT: Final = re.compile(r"[^A-Za-z0-9_/\-]")


def strict_name(service_name: str | None) -> str:
    """A service name as it may leave this server, or the fixed words."""
    if service_name is None or _LOOKS_PERSONAL.search(service_name):
        return A_SERVICE
    cleaned = _OUTSIDE_STRICT.sub("_", service_name)[:STRICT_NAME_LENGTH]
    return cleaned if cleaned.strip("_/") else A_SERVICE
