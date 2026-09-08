"""The one place a time zone name is judged.

**An unvalidated zone is a screen that crashes for one customer.** The name is
stored so a timestamp can be rendered in it, and rendering is `ZoneInfo(name)`.
So the check here is that same call: what is accepted is exactly what can later
be rendered, rather than a list this module keeps in step by hand.

**A missing tz database is not the customer's mistake.** `zoneinfo` reads the
operating system's database, falling back to the `tzdata` package. When neither
is present *every* name fails, and telling somebody that `Asia/Kolkata` is not a
valid time zone would send them looking for a typo that is not there. That case
is separated out and reported as what it is: this deployment cannot tell the
time. `UTC` is the probe because a database that has anything has that.
"""

from __future__ import annotations

from typing import Final, NoReturn
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

from nexovue_api.errors.exceptions import DependencyUnavailableError, InvalidRequestError

_log = structlog.get_logger(__name__)

# Present in every tz database there is, so failing to load it means there is no
# database rather than that the name was wrong.
_PROBE_ZONE: Final = "UTC"

UNKNOWN_TIMEZONE: Final = (
    "{name} is not a time zone this system knows. Choose one from the list, such as Asia/Kolkata."
)

# `ZoneInfo` raises ValueError rather than ZoneInfoNotFoundError for a key that
# is not a relative normalized path — `../../etc/passwd`, `/etc/localtime`. The
# caller gets one message either way; the distinction matters only in that this
# one never reaches the filesystem.
MALFORMED_TIMEZONE: Final = "That is not the shape of a time zone name."

# The caller is told nothing about our deployment; the log names the cause.
NO_TIMEZONE_DATABASE: Final = "Time zones cannot be set right now. Try again shortly."


def stored_timezone(supplied: str | None) -> str | None:
    """The zone as the column holds it: a known IANA name, or NULL when unset.

    `None`, an empty string and whitespace all mean the same thing — no zone
    chosen — which is the column's own NULL. Storing `""` would be a second
    value meaning what NULL already means, exactly as it would for the name.
    """
    if supplied is None:
        return None
    name = supplied.strip()
    if not name:
        return None

    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as unknown:
        _refuse_unknown(name, unknown)
    except ValueError as malformed:
        raise InvalidRequestError(MALFORMED_TIMEZONE) from malformed
    return name


def _refuse_unknown(name: str, unknown: ZoneInfoNotFoundError) -> NoReturn:
    """Say whether the name was wrong or the deployment has no tz database."""
    try:
        ZoneInfo(_PROBE_ZONE)
    except (ZoneInfoNotFoundError, ValueError) as absent:
        _log.error(
            "no tz database is readable, so every time zone would be refused as unknown; "
            "install the operating system's tzdata or add the tzdata package",
            probe=_PROBE_ZONE,
        )
        raise DependencyUnavailableError(NO_TIMEZONE_DATABASE) from absent
    raise InvalidRequestError(UNKNOWN_TIMEZONE.format(name=name)) from unknown
