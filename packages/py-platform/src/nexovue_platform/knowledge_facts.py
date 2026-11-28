"""F4: the fact line nexovue composes from a finding's columns alone (design, section 6; AC39).

One place, because two writers copy it: the scheduler's knowledge sweep indexes it for chat
(T5.5, T7.2), and the API's **Save as runbook** copies it, with a note, into a runbook that
outlives the finding (FR41). A name is written only when it passes the strict pattern, else
"a service"; a project finding names up to `MAX_NAMED_SERVICES` of its services by the same
rule. A finding's title, a false alarm's reason, a model's words, a log or a span never reach a
line: the arguments are columns, and nothing here reads free text but a name it checks.

**Which findings make a line** (FR28): one closed as `recovered`, or one whose case a person,
or the tracker a person linked, resolved (`RESOLVED_CASE_RESOLUTIONS`). A case ended as a false
alarm, a new normal, for want of activity, or closed by recovery untouched writes none (AC84):
only a person's "this was fixed" is the evidence "how did we fix this last time?" asks for.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final, Literal

from nexovue_platform.assistant_tools import STRICT_NAME
from nexovue_platform.detection import SIGNALS

# A project finding names at most this many of its services (FR28).
MAX_NAMED_SERVICES: Final = 5
UNNAMED_SERVICE: Final = "a service"
UNNAMED_SERVICES: Final = "several services"
UNKNOWN_SIGNAL: Final = "unknown"

RECOVERED: Final = "recovered"
# The case resolutions that keep a line (open decision 39): a person's resolve, and the
# tracker issue a person linked being closed. Frozen into SQL by `kept_sql`.
RESOLVED_CASE_RESOLUTIONS: Final = ("resolved", "tracker_closed")
# How the line says it ended.
Ending = Literal["recovered", "case resolved"]
CASE_RESOLVED: Final[Ending] = "case resolved"


def strict(name: object) -> str | None:
    return name if isinstance(name, str) and STRICT_NAME.fullmatch(name) else None


def first_signal(signals: object) -> str:
    """The finding's first signal, if it is one detection knows; else "unknown"."""
    if isinstance(signals, list) and signals and isinstance(signals[0], Mapping):
        named = signals[0].get("signal")
        if isinstance(named, str) and named in SIGNALS:
            return named
    return UNKNOWN_SIGNAL


def minute(moment: datetime) -> str:
    """In UTC whatever the connection's zone, as the line says."""
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def fact_line(
    *,
    scope: str,
    fact_service: str | None,
    services: object,
    signal: str,
    started_at: datetime,
    closed_at: datetime,
    ended: Ending = RECOVERED,
) -> str:
    """A finding as one line, from columns alone (design, section 6; AC39)."""
    if scope == "project":
        names = [name for item in _items(services) if (name := strict(item))]
        named = ", ".join(dict.fromkeys(names[:MAX_NAMED_SERVICES])) or UNNAMED_SERVICES
        who = f"services {named}"
    else:
        who = f"service {strict(fact_service) or UNNAMED_SERVICE}"
    return (
        f"{who} | signal {signal} | {minute(started_at)} to {minute(closed_at)} UTC "
        f"| closed: {ended}"
    )


def _items(services: object) -> list[object]:
    return list(services) if isinstance(services, list) else []


def resolved_case_sql(finding: str) -> str:
    """A finding whose case a person resolved, as SQL on the alias `finding`. Never null, so
    `NOT` of it, or of `kept_sql`, is true for a finding with no case."""
    resolutions = ", ".join(f"'{resolution}'" for resolution in RESOLVED_CASE_RESOLUTIONS)
    return (
        f"({finding}.case_state IS NOT DISTINCT FROM 'resolved' "
        f"AND COALESCE({finding}.case_resolution IN ({resolutions}), false))"
    )


def kept_sql(finding: str) -> str:
    """Whether a fact line of this finding is kept: still recovered, or its case resolved."""
    return (
        f"({finding}.closed_reason IS NOT DISTINCT FROM '{RECOVERED}' "
        f"OR {resolved_case_sql(finding)})"
    )
