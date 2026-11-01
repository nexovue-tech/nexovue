"""A finding's life, minute by minute: what opens, raises, joins and closes one
(design, "The statistical method" and "Findings and their state"; spec FR12 to
FR15, FR17, FR32, D20).

Pure: no store, no clock. The project job judges each minute (`method`), and this
decides what the service's one finding does with it, and whether the project as a
whole has stopped sending (FR15). The job writes what this decides.

**One finding per service** (BR2) lists every signal that moved and every
endpoint judged failing. Each of them is a *key*: `traffic`, `failing`, `slower`
or `endpoint:<name>`. A key is clear after 10 judged minutes in a row that read
normal, or no longer past the current sensitivity's minimum change (AC30).
Minutes it cannot be judged pause that count without resetting it (FR11). The
finding closes when every key has recovered or has gone 6 hours unjudged; if any
went unjudged it closes as *could no longer be judged* (D20). Severity only rises
(FR14). A service silent for 24 hours closes as *service gone* (FR32). A key a
person called a false alarm opens nothing until it reads normal again (FR20).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal
from zoneinfo import ZoneInfo

from nexovue_platform.detection import FindingSignal, Signal, finding_signal, normal_band

from nexovue_scheduler.detection.method import (
    CRITICAL_FAILING,
    CRITICAL_SLOWER,
    CRITICAL_TRAFFIC,
    RECOVERY_MINUTES,
    FailingJudgement,
    Mode,
    SlowerJudgement,
    TrafficJudgement,
)

MINUTE: Final = timedelta(minutes=1)
# D20: a key with no judgeable minute for this long stops holding its finding open.
UNJUDGED_CLOSE_MINUTES: Final = 360
# FR32: silent this long, a service's finding closes as *service gone*.
GONE_AFTER: Final = timedelta(hours=24)
# FR15: a project finding needs this many services, and this share of those judged
# for traffic, to have become unusual within `PROJECT_ONSET` of each other.
PROJECT_SERVICES: Final = 3
PROJECT_SHARE: Final = 0.75
PROJECT_ONSET: Final = timedelta(minutes=5)
# While this many services are pending on traffic, one waits up to `PROJECT_WAIT`
# for the project rule, so a collector outage is one finding, not nine (AC21).
PROJECT_PENDING: Final = 2
PROJECT_WAIT: Final = timedelta(minutes=3)

TRAFFIC: Final = "traffic"
FAILING: Final = "failing"
SLOWER: Final = "slower"
_ENDPOINT_PREFIX: Final = "endpoint:"

Status = Literal["unusual", "clear", "held", "unjudged"]
ClosedReason = Literal["recovered", "could_not_be_judged", "service_gone"]

_WEEKDAY_NAMES: Final = (
    "Mondays",
    "Tuesdays",
    "Wednesdays",
    "Thursdays",
    "Fridays",
    "Saturdays",
    "Sundays",
)
_WEEKEND_FROM: Final = 5
_TENS: Final = 10.0
_PERCENT: Final = 100.0
# The sign in "4 times slower" as a title writes it, escaped so it cannot pass for an x.
TIMES_SIGN: Final = "\u00d7"
# How the others read after the leading signal: "…, and is slower".
_ALSO: Final = {TRAFFIC: "losing traffic", FAILING: "failing more", SLOWER: "slower"}


def endpoint_key(name: str) -> str:
    return f"{_ENDPOINT_PREFIX}{name}"


def key_signal(key: str) -> Signal:
    """The signal a key belongs to: an endpoint's is *failing more* (FR2)."""
    if key.startswith(_ENDPOINT_PREFIX):
        return "failing"
    if key == TRAFFIC:
        return "traffic"
    if key == SLOWER:
        return "slower"
    return "failing"


def key_endpoint(key: str) -> str | None:
    return key.removeprefix(_ENDPOINT_PREFIX) if key.startswith(_ENDPOINT_PREFIX) else None


# ----------------------------------------------------------------- one minute


@dataclass(frozen=True, slots=True)
class Normal:
    """How a signal's normal was pooled, for the comparison a finding names."""

    mode: Mode
    days: int


@dataclass(frozen=True, slots=True)
class MinuteJudged:
    """One judged minute of one service: each signal's verdict and the normals."""

    minute: datetime
    width: int
    # Requests in the judged minute itself, not the window: silence is per minute.
    requests: int
    traffic: TrafficJudgement
    failing: FailingJudgement
    slower: SlowerJudgement
    traffic_normal: Normal | None = None
    failing_normal: Normal | None = None
    slower_normal: Normal | None = None
    # The p95 the slower verdict compared with; the window's is on its judgement.
    slower_normal_ms: float | None = None

    def status(self, key: str) -> Status:
        """What this minute says of one key of a finding."""
        if key == TRAFFIC:
            return _traffic_status(self.traffic)
        if key == SLOWER:
            return _verdict_status(self.slower.verdict)
        if key == FAILING:
            return _verdict_status(self.failing.verdict)
        name = key_endpoint(key)
        found = next((each for each in self.failing.endpoints if each.name == name), None)
        return "unjudged" if found is None else _verdict_status(found.verdict)

    def unusual_keys(self) -> list[str]:
        keys = [
            key
            for key, verdict in (
                (TRAFFIC, self.traffic.verdict),
                (FAILING, self.failing.verdict),
                (SLOWER, self.slower.verdict),
            )
            if verdict == "unusual"
        ]
        keys += [endpoint_key(e.name) for e in self.failing.endpoints if e.verdict == "unusual"]
        return keys

    def critical(self, key: str) -> bool:
        """The key's window crossed its critical size (FR14, D27)."""
        if key == TRAFFIC:
            return self.traffic.critical
        if key == SLOWER:
            return self.slower.critical
        if key == FAILING:
            return self.failing.critical
        name = key_endpoint(key)
        return any(e.critical for e in self.failing.endpoints if e.name == name)

    def onset(self, key: str) -> datetime:
        """The window's first minute leaning the key's way: when a finding started."""
        calm = {
            TRAFFIC: self.traffic.calm_minutes,
            FAILING: self.failing.calm_minutes,
            SLOWER: self.slower.calm_minutes,
        }.get(key, 0)
        return self.minute - (self.width - 1 - calm) * MINUTE


def _verdict_status(verdict: str) -> Status:
    if verdict == "unusual":
        return "unusual"
    if verdict == "normal":
        return "clear"
    if verdict == "elevated":
        return "held"
    return "unjudged"


def _traffic_status(judged: TrafficJudgement) -> Status:
    """A window with no requests never clears a traffic key, and holds it while the
    service has a normal: a stopped service is not recovered by a quiet hour (FR32)."""
    if judged.verdict == "unusual":
        return "unusual"
    if judged.clears:
        return "clear"
    if judged.judged or (judged.window_requests == 0 and judged.expected is not None):
        return "held"
    return "unjudged"


# -------------------------------------------------------------- the finding


@dataclass(slots=True)
class KeyRecord:
    """One key of an open finding: its values for the finding's `signals`, and how
    long it has been clear or unjudged."""

    normal: float | None
    current: float | None
    worst: float | None
    comparison: str
    endpoint: str | None = None
    clear: int = 0
    unjudged: int = 0
    # How far the worst window went towards critical: which key leads the title.
    weight: float = 0.0
    # The normal's band, from the sensitivity that judged the worst window (FR16).
    band: tuple[float, float] | None = None
    # *Slower*'s evidence endpoint's p95 then and the same window a week before (FR3).
    endpoint_current: float | None = None
    endpoint_last_week: float | None = None


@dataclass(slots=True)
class OpenFinding:
    """A service's open finding as the lifecycle keeps it."""

    severity: str
    started_at: datetime
    keys: dict[str, KeyRecord]
    muted: bool = False
    dirty: bool = False

    @property
    def lead(self) -> str:
        return max(self.keys, key=lambda key: self.keys[key].weight)

    def ordered(self) -> list[str]:
        """The leading key first, then the rest in the order they joined."""
        lead = self.lead
        return [lead, *(key for key in self.keys if key != lead)]

    def signals(self) -> list[FindingSignal]:
        return [
            finding_signal(
                key_signal(key),
                normal=record.normal,
                band=record.band,
                current=record.current,
                worst=record.worst,
                comparison=record.comparison,
                endpoint=record.endpoint,
                endpoint_current=record.endpoint_current,
                endpoint_last_week=record.endpoint_last_week,
            )
            for key, record in ((key, self.keys[key]) for key in self.ordered())
        ]


@dataclass(frozen=True, slots=True)
class Change:
    """What one minute did to a service's finding."""

    opened: bool = False
    joined: tuple[str, ...] = ()
    raised: bool = False
    closed: ClosedReason | None = None
    # Keys whose worst window this minute is, new keys included: where evidence is read.
    worsened: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Context:
    """What the project says of this service's traffic this minute (FR15)."""

    # The project's finding holds this service's traffic: it never opens or joins
    # the service's own finding.
    traffic_absorbed: bool = False
    # Wait for the project rule: more services are pending on traffic.
    traffic_waits: bool = False
    # The last minute this service had requests, for *service gone*.
    last_request: datetime | None = None
    # Keys a false alarm holds (FR20): they open and join nothing yet.
    held: frozenset[str] = frozenset()


def advance_holds(holds: Mapping[str, int], judged: MinuteJudged) -> dict[str, int]:
    """FR20: a false alarm's keys open and join nothing until each has read normal for
    the recovery time. Counted as a finding's keys are: an unusual or elevated minute
    starts the count again, one that cannot be judged pauses it. Returns the holds
    still in force, each with its count of clear minutes."""
    kept = {}
    for key, clear in holds.items():
        status = judged.status(key)
        if status == "clear":
            clear += 1
        elif status in ("unusual", "held"):
            clear = 0
        if clear < RECOVERY_MINUTES:
            kept[key] = clear
    return kept


def adoptable(finding: OpenFinding, *, since: datetime, minute: datetime, after: timedelta) -> bool:
    """FR21, D22: whether an open finding has been open, with the same signals, long
    enough for its level to become the normal. Never one that is critical, never a
    rise in failures, whether the service's or an endpoint's, and never a service
    that has stopped (FR32): those stay findings until someone acts or they end."""
    if finding.severity == "critical":
        return False
    if any(key_signal(key) == FAILING for key in finding.keys):
        return False
    traffic = finding.keys.get(TRAFFIC)
    if traffic is not None and not traffic.current:
        return False
    return minute - finding.started_at >= after and minute - since >= after


def step(
    finding: OpenFinding | None,
    judged: MinuteJudged,
    context: Context,
    *,
    zone: ZoneInfo,
) -> tuple[OpenFinding | None, Change]:
    """One minute of one service's finding. A closed finding is returned as it
    closed, with the change saying how."""
    keys = [
        key
        for key in judged.unusual_keys()
        if key not in context.held
        and (key != TRAFFIC or not (context.traffic_absorbed or context.traffic_waits))
    ]
    if finding is None:
        if not keys:
            return None, Change()
        return _open(judged, keys, zone), Change(opened=True, worsened=tuple(keys))
    silent_since = context.last_request or finding.started_at
    if judged.requests == 0 and judged.minute - silent_since >= GONE_AFTER:
        return finding, Change(closed="service_gone")
    joined = tuple(key for key in keys if key not in finding.keys)
    raised = False
    worsened = list(joined)
    for key in keys:
        record = finding.keys.get(key)
        if record is None:
            finding.keys[key] = _record(judged, key, zone)
        else:
            if _worsen(record, judged, key, zone):
                worsened.append(key)
            record.clear = record.unjudged = 0
        if judged.critical(key) and finding.severity != "critical":
            finding.severity, raised = "critical", True
    for key, record in finding.keys.items():
        if key in keys:
            continue
        _follow(record, judged, key)
        status = judged.status(key)
        if status == "clear":
            record.clear, record.unjudged = record.clear + 1, 0
        elif status == "held":
            record.clear = record.unjudged = 0
        elif status == "unjudged":
            record.unjudged += 1
        else:
            # Unusual but kept out of the finding (absorbed, or waiting): not clear.
            record.clear = record.unjudged = 0
    finding.dirty = True
    return finding, Change(
        joined=joined, raised=raised, closed=_closing(finding), worsened=tuple(worsened)
    )


def _closing(finding: OpenFinding) -> ClosedReason | None:
    done = all(
        record.clear >= RECOVERY_MINUTES or record.unjudged >= UNJUDGED_CLOSE_MINUTES
        for record in finding.keys.values()
    )
    if not done:
        return None
    unjudged = any(
        record.clear < RECOVERY_MINUTES and record.unjudged >= UNJUDGED_CLOSE_MINUTES
        for record in finding.keys.values()
    )
    return "could_not_be_judged" if unjudged else "recovered"


def _open(judged: MinuteJudged, keys: Sequence[str], zone: ZoneInfo) -> OpenFinding:
    critical = any(judged.critical(key) for key in keys)
    return OpenFinding(
        severity="critical" if critical else "warning",
        started_at=min(judged.onset(key) for key in keys),
        keys={key: _record(judged, key, zone) for key in keys},
        dirty=True,
    )


def _record(judged: MinuteJudged, key: str, zone: ZoneInfo) -> KeyRecord:
    normal, current, normal_of = _values(judged, key)
    return KeyRecord(
        normal=normal,
        current=current,
        worst=current,
        comparison=_comparison_of(normal_of, judged.minute, zone),
        endpoint=_endpoint_of(judged, key),
        weight=weight(key, normal, current),
        band=normal_band(key_signal(key), normal, judged.width),
    )


def _worsen(record: KeyRecord, judged: MinuteJudged, key: str, zone: ZoneInfo) -> bool:
    """An unusual minute: the worst value, its normal, band and comparison follow the
    worst window (FR17). Returns whether this minute is the new worst."""
    normal, current, normal_of = _values(judged, key)
    record.current = current
    reached = weight(key, normal, current)
    if reached <= record.weight:
        return False
    record.weight, record.worst, record.normal = reached, current, normal
    record.band = normal_band(key_signal(key), normal, judged.width)
    record.comparison = _comparison_of(normal_of, judged.minute, zone)
    record.endpoint = _endpoint_of(judged, key)
    return True


def _follow(record: KeyRecord, judged: MinuteJudged, key: str) -> None:
    _, current, _ = _values(judged, key)
    if current is not None:
        record.current = current


def _values(judged: MinuteJudged, key: str) -> tuple[float | None, float | None, Normal | None]:
    """A key's normal and current values in its signal's unit: ms, % or requests a
    minute; and how its normal was pooled."""
    width = judged.width
    if key == TRAFFIC:
        traffic = judged.traffic
        normal = None if traffic.expected is None else traffic.expected / width
        return normal, traffic.window_requests / width, judged.traffic_normal
    if key == SLOWER:
        return judged.slower_normal_ms, judged.slower.window_p95_ms, judged.slower_normal
    failing = judged.failing
    if key == FAILING:
        normal = None if failing.normal_rate is None else failing.normal_rate * _PERCENT
        current = failing.rate * _PERCENT if failing.window_requests else None
        return normal, current, judged.failing_normal
    name = key_endpoint(key)
    found = next((e for e in failing.endpoints if e.name == name), None)
    if found is None:
        return None, None, judged.failing_normal
    return found.normal_rate * _PERCENT, found.rate * _PERCENT, judged.failing_normal


def weight(key: str, normal: float | None, current: float | None) -> float:
    """How far a value went from its normal towards its signal's critical size: 0 at
    normal, 1 at critical, so the signals compare."""
    if normal is None or current is None:
        return 0.0
    if key == TRAFFIC:
        return (1 - current / normal) / (1 - CRITICAL_TRAFFIC) if normal > 0 else 0.0
    if key == SLOWER:
        return (current / normal - 1) / (CRITICAL_SLOWER - 1) if normal > 0 else 0.0
    return (current - normal) / (CRITICAL_FAILING * _PERCENT)


def _endpoint_of(judged: MinuteJudged, key: str) -> str | None:
    """FR3's evidence endpoint for *failing more*; an endpoint key names itself. The
    project job reads *slower*'s from the store, which this module never touches."""
    if key == FAILING:
        return judged.failing.evidence_endpoint
    return key_endpoint(key)


def _comparison_of(normal: Normal | None, minute: datetime, zone: ZoneInfo) -> str:
    return "" if normal is None else comparison(normal, minute, zone)


# ------------------------------------------------------------------- titles


def title(service: str, finding: OpenFinding, zone: ZoneInfo) -> str:
    """FR17: subject, what changed, magnitude, since when, in the project's zone; with
    several signals the worst leads: "…, and is slower"."""
    ordered = finding.ordered()
    lead = ordered[0]
    since = local_clock(finding.started_at, zone)
    record = finding.keys[lead]
    sentence = _lead_sentence(service, lead, record, since)
    others = []
    for key in ordered[1:]:
        also = _ALSO[key_signal(key)]
        if also not in others and key_signal(key) != key_signal(lead):
            others.append(also)
    if others:
        sentence += ", and is " + " and ".join(others)
    return sentence


def _lead_sentence(service: str, key: str, record: KeyRecord, since: str) -> str:
    worst, normal = record.worst, record.normal
    if key == SLOWER:
        ratio = (worst or 0.0) / normal if normal else 0.0
        return f"{service} is {times(ratio)} slower than normal since {since}"
    if key == TRAFFIC:
        if not worst:
            return f"{service} stopped receiving requests at {since}"
        share = round(_PERCENT * worst / normal) if normal else 0
        return f"{service} is receiving {share}% of its normal traffic since {since}"
    subject = "requests" if key == FAILING else f"{record.endpoint} requests"
    return (
        f"{service} is failing {percent(worst)} of {subject}, "
        f"against {percent(normal)} normally, since {since}"
    )


def project_title(stopped: int, judged: int, since: datetime, zone: ZoneInfo) -> str:
    """FR15's sentence for a project finding. It names nexovue's intake too: when the
    install-wide guard can blame the intake no finding opens, so one that does open
    could not rule it out (design, the settled minute, item 3)."""
    return (
        f"Traffic stopped arriving from {stopped} of {judged} services at "
        f"{local_clock(since, zone)}. Check your collector, your network or nexovue's intake"
    )


def times(ratio: float) -> str:
    """1.6, 4 and 12, each with the sign: one decimal below ten, none from ten."""
    if ratio >= _TENS:
        return f"{ratio:.0f}{TIMES_SIGN}"
    return f"{ratio:.1f}".removesuffix(".0") + TIMES_SIGN


def percent(value: float | None) -> str:
    """3.2%, 0.4%, 0.04% and 25%: a rate as a rate, never a multiple (FR17)."""
    if value is None:
        return "an unknown share"
    if value >= _TENS:
        return f"{value:.0f}%"
    if value >= 1:
        return f"{value:.1f}".removesuffix(".0") + "%"
    if value <= 0:
        return "0%"
    digits = max(1, 1 - math.floor(math.log10(value)))
    return f"{value:.{digits}f}".rstrip("0").rstrip(".") + "%"


def local_clock(moment: datetime, zone: ZoneInfo) -> str:
    """15:04 IST: the time in the project's zone, with the zone's abbreviation."""
    return moment.astimezone(zone).strftime("%H:%M %Z")


def comparison(normal: Normal, minute: datetime, zone: ZoneInfo) -> str:
    """What the minute was compared with: "Tuesdays around 15:00, last 3 weeks"."""
    local = minute.astimezone(zone)
    clock = local.strftime("%H:%M")
    if normal.mode == "weekly":
        return f"{_WEEKDAY_NAMES[local.weekday()]} around {clock}, last {normal.days} weeks"
    kind = "weekend days" if local.weekday() >= _WEEKEND_FROM else "weekdays"
    return f"{kind.capitalize()} around {clock}, last {normal.days} {kind}"


# ------------------------------------------------------------- the project


@dataclass(frozen=True, slots=True)
class TrafficSnapshot:
    """One service's traffic at the project's latest judged minute (FR15)."""

    judged: bool
    unusual: bool
    # When its current run of unusual traffic began.
    unusual_since: datetime | None
    # Judged minutes in a row its traffic has been clear.
    clear_minutes: int
    # Its own finding already holds traffic: it is not pending.
    in_own_finding: bool
    last_request: datetime | None
    expected_rpm: float | None = None
    current_rpm: float | None = None


@dataclass(frozen=True, slots=True)
class ProjectDecision:
    """What the project finding does this minute, and whom it holds."""

    open: bool = False
    close: ClosedReason | None = None
    # The services its traffic holds, after this minute.
    involved: frozenset[str] = frozenset()
    judged: int = 0
    # Services pending on traffic without a finding of their own (FR15's wait).
    pending: int = 0


def project_rule(
    snapshots: Mapping[str, TrafficSnapshot],
    *,
    involved: frozenset[str] | None,
    minute: datetime,
) -> ProjectDecision:
    """FR15. `involved` is the open project finding's services, or None when none is
    open. It opens when at least 3 services, and 75% of those judged for traffic,
    became unusual within 5 minutes. It closes once fewer than half of its services
    are still unusual for the recovery time, or as *service gone* after 24 hours
    in which none of them sent anything."""
    judged = sum(1 for each in snapshots.values() if each.judged)
    pending = sum(1 for each in snapshots.values() if each.unusual and not each.in_own_finding)
    if involved is None:
        recent = frozenset(
            name
            for name, each in snapshots.items()
            if each.unusual
            and each.unusual_since is not None
            and minute - each.unusual_since < PROJECT_ONSET
        )
        if len(recent) >= PROJECT_SERVICES and len(recent) >= PROJECT_SHARE * judged:
            return ProjectDecision(open=True, involved=recent, judged=judged, pending=pending)
        return ProjectDecision(judged=judged, pending=pending)
    joined = involved | frozenset(
        name for name, each in snapshots.items() if each.unusual and not each.in_own_finding
    )
    members = [snapshots[name] for name in joined if name in snapshots]
    if members and all(
        each.last_request is None or minute - each.last_request >= GONE_AFTER for each in members
    ):
        return ProjectDecision(close="service_gone", involved=joined, judged=judged)
    still = sum(1 for each in members if each.clear_minutes < RECOVERY_MINUTES)
    if still < len(joined) / 2:
        return ProjectDecision(close="recovered", involved=joined, judged=judged)
    return ProjectDecision(involved=joined, judged=judged, pending=pending)


def traffic_waits(pending_since: datetime, minute: datetime, pending: int) -> bool:
    """FR15's wait: while 2 or more services are pending on traffic, a service's own
    traffic finding waits up to 3 minutes for the project rule to fire first."""
    return pending >= PROJECT_PENDING and minute - pending_since < PROJECT_WAIT
