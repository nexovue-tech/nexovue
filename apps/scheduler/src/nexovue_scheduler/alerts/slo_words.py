"""How a reliability target's burn and notices are said (F19 design, *Messages*).

Every value comes from the incident's `detail`: numbers, ids, the names a rollup or a
check already holds, and the project's zone. A target's own name, description and an
exclusion's reason are never there (FR33). Each kind uses its own word (FR12, AC89): a
latency target speaks of slow requests, a check of failed runs and never of requests.
Names from telemetry pass through `one_line`, and each channel escapes them after.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

from nexovue_platform import slo

from nexovue_scheduler.alerts.outbox import one_line

_HOURS_IN_A_DAY: Final = 24
# "about 2 days" from 48 hours: under two days, hours read better than a fraction.
_DAYS_FROM_HOURS: Final = 2 * _HOURS_IN_A_DAY
_FALLBACK_ZONE: Final = "UTC"


@dataclass(frozen=True, slots=True)
class Said:
    happening: str
    still: str
    recovered: str
    facts: tuple[str, ...]


def said(detail: dict[str, Any], *, at: datetime) -> Said:
    """A burn's words, or a notice's, from its detail."""
    kind = str(detail.get("kind") or slo.SERVICE_AVAILABILITY)
    noun = slo.failure_noun(kind)
    notice = detail.get("notice")
    if notice == "exhausted":
        return Said(
            happening=f"has no allowed {noun} left",
            still=f"still has no allowed {noun} left",
            recovered=f"has allowed {noun} again",
            facts=(
                f"{slo.allowance_words(kind)}: 0 of {_count(detail.get('allowed'))}, with "
                f"{_count(detail.get('failures'))} {noun} in the window.",
                f"It recovers as old {noun} leave the window.",
            ),
        )
    if notice == "blind":
        since = datetime.fromisoformat(str(detail.get("since")))
        return Said(
            happening="are not being judged",
            still="are still not being judged",
            recovered="are being judged again",
            facts=(
                f"Not judged since {_when(since, _zone(detail), at)}. A burn would not reach you.",
            ),
        )
    if notice == "excluded":
        return Said(
            happening="had time left out",
            still="had time left out",
            recovered="",
            facts=(_excluded(detail),),
        )
    rate = detail.get("rate")
    fast = (
        f"{math.floor(rate)} times faster than its target allows"
        if isinstance(rate, int | float)
        else "faster than its target allows"
    )
    return Said(
        happening=f"is using its allowed {noun} {fast}",
        still=f"is still using its allowed {noun} too fast",
        recovered=f"is no longer using its allowed {noun} too fast",
        facts=_burn_facts(detail, kind, at=at),
    )


def source_silent(detail: dict[str, Any]) -> str:
    """FR17's ending: a burn whose source sent nothing for the longest window."""
    hours = int(slo.LONGEST_TIER_WINDOW / timedelta(hours=1))
    return (
        f"No longer judged: {_source(detail)} has sent nothing for {hours} hours. "
        "The burn may not be over."
    )


def _burn_facts(detail: dict[str, Any], kind: str, *, at: datetime) -> tuple[str, ...]:
    facts = []
    since = detail.get("since")
    zone = _zone(detail)
    if since:
        where = _source(detail)
        on = f", on {where}" if detail.get("service") else ""
        facts.append(f"Since {_when(datetime.fromisoformat(str(since)), zone, at)}{on}.")
    objective = detail.get("objective_percent")
    window = detail.get("window_days")
    if objective and window:
        facts.append(f"Target: {objective}% of {slo.event_noun(kind)} over {window} days.")
    hours_left = detail.get("hours_left")
    if isinstance(hours_left, int | float):
        facts.append(_lasts(float(hours_left)))
    if detail.get("allowed") is not None:
        facts.append(
            f"{slo.allowance_words(kind)}: {_count(detail.get('left'))} of "
            f"{_count(detail.get('allowed'))}."
        )
    return tuple(facts)


def _lasts(hours: float) -> str:
    if hours <= 0:
        return "Nothing is left."
    if hours < 1:
        return "At this rate, what is left lasts under an hour."
    if hours < _DAYS_FROM_HOURS:
        return f"At this rate, what is left lasts about {math.floor(hours)} hours."
    return f"At this rate, what is left lasts about {math.floor(hours / _HOURS_IN_A_DAY)} days."


def _excluded(detail: dict[str, Any]) -> str:
    """FR29's notice: who, how long, from which target, with no reason and no person."""
    zone = _zone(detail)
    starts = datetime.fromisoformat(str(detail.get("starts_at")))
    ends = datetime.fromisoformat(str(detail.get("ends_at")))
    minutes = int(detail.get("minutes") or (ends - starts) / timedelta(minutes=1))
    hours, rest = divmod(minutes, 60)
    length = f"{hours} h {rest} min" if hours and rest else f"{hours} h" if hours else f"{rest} min"
    return (
        f"Someone left {length} out of it, from {_clock(starts, zone)} to "
        f"{_clock(ends, zone)}. See the target's page."
    )


def _source(detail: dict[str, Any]) -> str:
    service = one_line(str(detail.get("service") or "the check"))
    endpoint = detail.get("endpoint")
    return f"{service} {one_line(str(endpoint))}" if endpoint else service


def _zone(detail: dict[str, Any]) -> ZoneInfo:
    try:
        return ZoneInfo(str(detail.get("zone") or _FALLBACK_ZONE))
    except (ValueError, KeyError):
        return ZoneInfo(_FALLBACK_ZONE)


def _when(moment: datetime, zone: ZoneInfo, now: datetime) -> str:
    """FR13: the project's zone, and the date when not today: "02:41 IST", "23:10
    yesterday (IST)", "23:10 on 3 Nov (IST)"."""
    local, today = moment.astimezone(zone), now.astimezone(zone).date()
    name = local.strftime("%Z")
    if local.date() == today:
        return f"{local:%H:%M} {name}"
    if local.date() == today - timedelta(days=1):
        return f"{local:%H:%M} yesterday ({name})"
    return f"{local:%H:%M} on {local.day} {local:%b} ({name})"


def _clock(moment: datetime, zone: ZoneInfo) -> str:
    local = moment.astimezone(zone)
    return f"{local:%H:%M} on {local.day} {local:%b} ({local:%Z})"


def _count(value: object) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, Decimal | float):
        return f"{int(value):,}"
    return "—"
