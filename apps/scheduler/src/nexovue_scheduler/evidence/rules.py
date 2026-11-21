"""Which fact, if any, is the likely place to look first: fixed rules, pure (spec FR8 to
FR12; design *The rules*).

Pure functions over a `Facts` value, so the rules run on every commit with no store.
The rules run in FR8's order and the first that may name a cause names it; every other
rule that passes is kept in `supported_by`. A rule resting on words in telemetry (a
version seen only in traffic, a new error group) never names a cause, only supports
one (A75, BR7). With no rule passing, nothing is named: abstaining is a result (FR10).

**Every window and threshold below is a guess** (FR8), named here and tuned by the bed
(plan Step 2c). The words are "likely" and "possible"; nothing here says a thing caused
anything (BR6).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Final, Literal
from zoneinfo import ZoneInfo

from nexovue_platform.evidence import (
    DEPLOY_LOOKBACK,
    LIKELY,
    POSSIBLE,
    RULE_DEPENDENCY_DEPLOY,
    RULE_DEPENDENCY_EDGE,
    RULE_DEPENDENCY_FINDING,
    RULE_NEW_ERROR,
    RULE_PROJECT_DEPLOY,
    RULE_PROJECT_SHARED_DEPENDENCY,
    RULE_SEEN_VERSION,
    RULE_SIGNED_DEPLOY,
    RULE_SIGNED_DEPLOY_WEAK,
    RULE_TRAFFIC_ROSE,
    TELEMETRY,
    TELEMETRY_TEXT,
    VERIFIED,
    evidence_id,
)

# A chance below this may be "likely"; below the next, "possible" (FR9).
CHANCE_LIKELY_BELOW: Final = 0.10
CHANCE_POSSIBLE_BELOW: Final = 0.50
# Fewer days of deploy records than this, and a deploy is "possible" at most (FR7).
BASE_RATE_MIN_DAYS: Final = 7
# The comparable windows: 30 minutes long, in the start's hour and one either side,
# on days of the same kind, over the last week (FR9).
BASE_RATE_DAYS: Final = 7
BASE_RATE_WINDOW: Final = timedelta(minutes=30)
BASE_RATE_HOURS_EITHER_SIDE: Final = 1
# An error group that began this long before the start counts as new (FR8, rule 8).
ERROR_LEAD_IN: Final = timedelta(minutes=15)
# Its look-back: a group seen in this hour before is not new (A62).
NEW_ERROR_LOOK_BACK: Final = timedelta(minutes=60)
# An edge needs this many calls in each window to be judged (rules 2, 6).
EDGE_MIN_CALLS: Final = 30
# Failures moved: at least this many times the share before, and this much higher.
EDGE_FAILURE_RISE: Final = 2.0
EDGE_FAILURE_RISE_POINTS: Final = 0.01
# Latency moved: the p95 at least this many times what it was.
EDGE_LATENCY_RISE: Final = 1.5
# Traffic rose before a slowdown: the lead-in's rate this many times the hour's before it.
TRAFFIC_RISE: Final = 1.5
TRAFFIC_LEAD_IN: Final = timedelta(minutes=15)
TRAFFIC_BASELINE: Final = timedelta(minutes=60)
# A project rule: a deploy to at least this share of the finding's services (FR11).
PROJECT_DEPLOY_SHARE: Final = 0.5
# A host reading at or past this share is a project-wide fact (FR52; a guess).
HOST_HIGH_UTILIZATION: Final = 0.90
_MINUTE: Final = timedelta(minutes=1)
_WEEKEND: Final = (5, 6)

Signal = Literal["slower", "failing", "traffic"]


# ------------------------------------------------------------------ the inputs


@dataclass(frozen=True, slots=True)
class FindingInput:
    id: str
    scope: Literal["service", "project"]
    service: str | None
    started_at: datetime
    signals: tuple[Signal, ...]
    # A project finding's services (FR15); empty for a service finding.
    services: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DeployFact:
    """A signed deploy event (F28's `reported`), or a version only seen in traffic."""

    id: str
    service: str
    version: str
    at: datetime
    signed: bool


@dataclass(frozen=True, slots=True)
class DeployHistory:
    """One service's deploys over the base rate's week, and since when it has any."""

    service: str
    times: tuple[datetime, ...]
    # The first signed deploy nexovue holds for it; None when it never sent one.
    records_since: datetime | None


@dataclass(frozen=True, slots=True)
class EdgeFact:
    """This service's calls to one callee, in the finding's window and the one before."""

    callee: str
    calls_before: int
    calls_during: int
    failures_before: int
    failures_during: int
    p95_before: float | None
    p95_during: float | None
    # The attribute a told-only callee was named by (A76); empty when it reported itself.
    callee_attr: str = ""
    callee_kind: str = ""
    # Read from browser-door edges, because the project has no server-door ones (FR12).
    from_browser: bool = False

    @property
    def reported(self) -> bool:
        """Whether the callee reported for itself: a told-only name is in parentheses."""
        return not self.callee.startswith("(")


@dataclass(frozen=True, slots=True)
class OtherFinding:
    id: str
    service: str | None
    started_at: datetime


@dataclass(frozen=True, slots=True)
class Traffic:
    """Requests a minute: the hour before the lead-in, and the lead-in itself."""

    before_per_minute: float | None
    lead_per_minute: float | None


@dataclass(frozen=True, slots=True)
class ErrorGroup:
    group_id: str
    # Kept only if it reads as a code identifier (A47); otherwise None.
    exception_type: str | None
    services: tuple[str, ...]
    count: int
    first_seen: datetime


@dataclass(frozen=True, slots=True)
class CheckChange:
    check_id: str
    name: str
    state: str
    at: datetime


@dataclass(frozen=True, slots=True)
class LinkedIncident:
    id: str
    rule_name: str
    opened_at: datetime


@dataclass(frozen=True, slots=True)
class HostReading:
    host: str
    measure: Literal["disk", "memory"]
    utilization: float
    at: datetime


@dataclass(frozen=True, slots=True)
class Release:
    """One of F28's periods, for the last-deploy line and the 2-hour list (FR49)."""

    release_id: str
    period_id: str
    service: str
    version: str
    started_at: datetime
    signed: bool
    # The finding's service calls this one (`service_edges_1m`).
    called: bool = False


@dataclass(frozen=True, slots=True)
class Facts:
    """Everything one gather read. A read that failed is named in `failed_reads`, and
    its field is empty: never read as "none" (AC64)."""

    finding: FindingInput
    now: datetime
    zone: ZoneInfo
    deploys: tuple[DeployFact, ...] = ()
    histories: Mapping[str, DeployHistory] = field(default_factory=dict)
    # Versions seen in traffic over the week, per service, for rule 5's chance.
    seen_histories: Mapping[str, DeployHistory] = field(default_factory=dict)
    # F28's coverage of the finding's service (`releases.COVERAGES`).
    coverage: str | None = None
    edges: tuple[EdgeFact, ...] = ()
    # Callers' edges for a project finding: (caller, edge).
    project_edges: tuple[tuple[str, EdgeFact], ...] = ()
    dependency_findings: tuple[OtherFinding, ...] = ()
    other_findings: tuple[OtherFinding, ...] = ()
    traffic: Traffic | None = None
    error_groups: tuple[ErrorGroup, ...] = ()
    checks: tuple[CheckChange, ...] = ()
    incidents: tuple[LinkedIncident, ...] = ()
    hosts: tuple[HostReading, ...] = ()
    # Whether any host reading in the project carries `host.name` (FR52).
    host_names_present: bool = True
    last_release: Release | None = None
    nearby_releases: tuple[Release, ...] = ()
    # Install-wide intake refusals in the 15 minutes before (FR11; self-hosted only).
    intake_refused_at: tuple[datetime, ...] = ()
    failed_reads: frozenset[str] = frozenset()
    # Reads that passed a bound and were cut (FR51).
    truncated_reads: frozenset[str] = frozenset()


# ------------------------------------------------------------------ the output


@dataclass(frozen=True, slots=True)
class Cause:
    evidence_id: str
    rule: str
    strength: Literal["likely", "possible"]
    basis: str
    supported_by: tuple[str, ...] = ()
    same_minute: bool = False

    def as_json(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "rule": self.rule,
            "strength": self.strength,
            "basis": self.basis,
            "supported_by": list(self.supported_by),
            "same_minute": self.same_minute,
        }


@dataclass(frozen=True, slots=True)
class Passed:
    """One rule that passed, and the fact it rests on."""

    rule: str
    evidence_id: str
    strength: Literal["likely", "possible"] | None
    basis: str
    same_minute: bool = False


@dataclass(frozen=True, slots=True)
class BaseRate:
    """How usual a deploy at this hour is for one service (FR7, FR9)."""

    deploys_last_7_days: int
    days_of_records: int
    # The share of comparable windows holding a deploy; None with none to compare.
    chance: float | None


# ------------------------------------------------------------------ evidence ids


def name_key(name: str) -> str:
    """A stable short identity for a name: names can be long and are attacker text."""
    return hashlib.sha256(name.encode()).hexdigest()[:16]


def deploy_evidence_id(deploy: DeployFact) -> str:
    return evidence_id("deploy" if deploy.signed else "seen_version", deploy.id)


def dependency_evidence_id(callee: str, caller: str | None = None) -> str:
    identity = name_key(callee) if caller is None else f"{name_key(caller)}.{name_key(callee)}"
    return evidence_id("dependency", identity)


def finding_evidence_id(finding: OtherFinding) -> str:
    return evidence_id("other_finding", finding.id)


def error_evidence_id(group: ErrorGroup) -> str:
    return evidence_id("error_group", group.group_id)


def traffic_evidence_id(service: str) -> str:
    return evidence_id("traffic", name_key(service))


# ------------------------------------------------------------------ base rate


def base_rate(history: DeployHistory | None, at: datetime, zone: ZoneInfo) -> BaseRate:
    """FR9: the share of comparable 30-minute windows that held a deploy. Same kind of
    day, the start's hour and one either side, on the project's clock, over the week
    before, counting only windows nexovue had deploy records for."""
    if history is None or history.records_since is None:
        return BaseRate(deploys_last_7_days=0, days_of_records=0, chance=None)
    week_ago = at - timedelta(days=BASE_RATE_DAYS)
    recent = [moment for moment in history.times if week_ago <= moment < at]
    days = max(0, (at - history.records_since) // timedelta(days=1))
    windows = _comparable_windows(at, zone, covered_from=history.records_since)
    if not windows:
        return BaseRate(len(recent), days, None)
    held = sum(1 for start in windows if any(start <= t < start + BASE_RATE_WINDOW for t in recent))
    return BaseRate(len(recent), days, held / len(windows))


def _comparable_windows(at: datetime, zone: ZoneInfo, *, covered_from: datetime) -> list[datetime]:
    local = at.astimezone(zone)
    weekend = local.weekday() in _WEEKEND
    windows: list[datetime] = []
    for days_back in range(1, BASE_RATE_DAYS + 1):
        day: date = (local - timedelta(days=days_back)).date()
        if (day.weekday() in _WEEKEND) != weekend:
            continue
        for hour in range(
            local.hour - BASE_RATE_HOURS_EITHER_SIDE, local.hour + 1 + BASE_RATE_HOURS_EITHER_SIDE
        ):
            if not 0 <= hour <= 23:
                continue
            for minute in (0, 30):
                start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
                if start >= covered_from:
                    windows.append(start)
    return windows


def deploy_strength(rate: BaseRate) -> Literal["likely", "possible"] | None:
    """FR9's bands, and FR7's short history: "likely" only under 10% with a week of
    records, "possible" under 50%, and nothing at 50% or more."""
    if rate.chance is not None and rate.chance >= CHANCE_POSSIBLE_BELOW:
        return None
    if rate.days_of_records < BASE_RATE_MIN_DAYS:
        return POSSIBLE
    if rate.chance is not None and rate.chance < CHANCE_LIKELY_BELOW:
        return LIKELY
    return POSSIBLE


# ------------------------------------------------------------------ edges


def failures_moved(edge: EdgeFact) -> bool:
    if min(edge.calls_before, edge.calls_during) < EDGE_MIN_CALLS:
        return False
    before = edge.failures_before / edge.calls_before
    during = edge.failures_during / edge.calls_during
    return during >= EDGE_FAILURE_RISE * before and during - before >= EDGE_FAILURE_RISE_POINTS


def latency_moved(edge: EdgeFact) -> bool:
    if min(edge.calls_before, edge.calls_during) < EDGE_MIN_CALLS:
        return False
    if edge.p95_before is None or edge.p95_during is None or edge.p95_before <= 0:
        return False
    return edge.p95_during >= EDGE_LATENCY_RISE * edge.p95_before


def edge_moved(edge: EdgeFact) -> bool:
    return failures_moved(edge) or latency_moved(edge)


def traffic_rose(traffic: Traffic | None) -> bool:
    if traffic is None or traffic.before_per_minute is None or traffic.lead_per_minute is None:
        return False
    if traffic.before_per_minute <= 0:
        return False
    return traffic.lead_per_minute >= TRAFFIC_RISE * traffic.before_per_minute


def new_error_groups(facts: Facts) -> list[ErrorGroup]:
    """Groups first seen from the lead-in on; the read kept only those unseen in the
    hour before (A62)."""
    since = facts.finding.started_at - ERROR_LEAD_IN
    return [group for group in facts.error_groups if group.first_seen >= since]


# ------------------------------------------------------------------ the rules


def in_window(moment: datetime, start: datetime) -> bool:
    """A deploy lines up when it lands in the 30 minutes up to the start's minute end."""
    return start - DEPLOY_LOOKBACK <= moment < _minute(start) + _MINUTE


def _minute(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


def _latest(deploys: Sequence[DeployFact]) -> DeployFact | None:
    return max(deploys, key=lambda deploy: (deploy.at, deploy.id), default=None)


def _service_rules(facts: Facts) -> list[Passed]:
    finding = facts.finding
    start = finding.started_at
    service = finding.service
    passed: list[Passed] = []
    window = [d for d in facts.deploys if in_window(d.at, start)]

    own = _latest([d for d in window if d.signed and d.service == service])
    if own is not None:
        strength = deploy_strength(base_rate(facts.histories.get(own.service), start, facts.zone))
        if strength is not None:
            rule = RULE_SIGNED_DEPLOY if strength == LIKELY else RULE_SIGNED_DEPLOY_WEAK
            passed.append(Passed(rule, deploy_evidence_id(own), strength, VERIFIED))

    callees = {edge.callee: edge for edge in facts.edges}
    findings_by_callee = {f.service: f for f in facts.dependency_findings if f.service in callees}
    start_minute = _minute(start)
    for callee, edge in callees.items():
        if not edge_moved(edge):
            continue
        own_finding = findings_by_callee.get(callee)
        cap: Literal["likely", "possible"] = "possible" if edge.from_browser else "likely"
        if own_finding is not None and _minute(own_finding.started_at) < start_minute:
            passed.append(
                Passed(RULE_DEPENDENCY_FINDING, dependency_evidence_id(callee), cap, TELEMETRY)
            )
        elif own_finding is None or _minute(own_finding.started_at) == start_minute:
            passed.append(
                Passed(
                    RULE_DEPENDENCY_EDGE,
                    dependency_evidence_id(callee),
                    POSSIBLE,
                    TELEMETRY,
                    same_minute=own_finding is not None,
                )
            )

    reported_callees = {callee for callee, edge in callees.items() if edge.reported}
    dependency_deploy = _latest([d for d in window if d.signed and d.service in reported_callees])
    if dependency_deploy is not None:
        rate = base_rate(facts.histories.get(dependency_deploy.service), start, facts.zone)
        if rate.chance is None or rate.chance < CHANCE_POSSIBLE_BELOW:
            passed.append(
                Passed(
                    RULE_DEPENDENCY_DEPLOY,
                    deploy_evidence_id(dependency_deploy),
                    POSSIBLE,
                    VERIFIED,
                )
            )

    seen = _latest([d for d in window if not d.signed and d.service == service])
    if seen is not None:
        rate = base_rate(facts.seen_histories.get(seen.service), start, facts.zone)
        if rate.chance is None or rate.chance < CHANCE_POSSIBLE_BELOW:
            passed.append(Passed(RULE_SEEN_VERSION, deploy_evidence_id(seen), None, TELEMETRY_TEXT))

    if "slower" in finding.signals and traffic_rose(facts.traffic) and service is not None:
        passed.append(Passed(RULE_TRAFFIC_ROSE, traffic_evidence_id(service), POSSIBLE, TELEMETRY))

    for group in new_error_groups(facts):
        passed.append(Passed(RULE_NEW_ERROR, error_evidence_id(group), None, TELEMETRY_TEXT))
    return passed


def _project_rules(facts: Facts) -> list[Passed]:
    finding = facts.finding
    services = set(finding.services)
    passed: list[Passed] = []
    window = [
        d
        for d in facts.deploys
        if d.signed and d.service in services and in_window(d.at, finding.started_at)
    ]
    deployed = {d.service for d in window}
    if services and len(deployed) >= PROJECT_DEPLOY_SHARE * len(services):
        latest = _latest(window)
        assert latest is not None
        passed.append(Passed(RULE_PROJECT_DEPLOY, deploy_evidence_id(latest), POSSIBLE, VERIFIED))

    callers_of: dict[str, set[str]] = {}
    for caller, edge in facts.project_edges:
        if caller in services:
            callers_of.setdefault(edge.callee, set()).add(caller)
    with_finding = {
        f.service for f in facts.dependency_findings if f.started_at < _minute(finding.started_at)
    }
    shared = sorted(
        c for c, callers in callers_of.items() if len(callers) >= 2 and c in with_finding
    )
    if shared:
        passed.append(
            Passed(
                RULE_PROJECT_SHARED_DEPENDENCY,
                dependency_evidence_id(shared[0]),
                POSSIBLE,
                TELEMETRY,
            )
        )
    return passed


# Rules in FR8's order, then FR11's; a support rule never names a cause.
_ORDER: Final = (
    RULE_SIGNED_DEPLOY,
    RULE_DEPENDENCY_FINDING,
    RULE_SIGNED_DEPLOY_WEAK,
    RULE_DEPENDENCY_DEPLOY,
    RULE_SEEN_VERSION,
    RULE_DEPENDENCY_EDGE,
    RULE_TRAFFIC_ROSE,
    RULE_NEW_ERROR,
    RULE_PROJECT_DEPLOY,
    RULE_PROJECT_SHARED_DEPENDENCY,
)


def passed_rules(facts: Facts) -> list[Passed]:
    """Every rule that passed, in FR8's order."""
    found = _project_rules(facts) if facts.finding.scope == "project" else _service_rules(facts)
    return sorted(found, key=lambda each: _ORDER.index(each.rule))


def decide(facts: Facts, *, stopped_rules: frozenset[str] = frozenset()) -> Cause | None:
    """The likely cause, or None. The first rule that may name one names it; the rest
    that passed support it. `stopped_rules` are an organization's (FR54, Step 2)."""
    passed = passed_rules(facts)
    naming = [p for p in passed if p.strength is not None and p.rule not in stopped_rules]
    if not naming:
        return None
    first = naming[0]
    support = tuple(
        dict.fromkeys(p.evidence_id for p in passed if p.evidence_id != first.evidence_id)
    )
    return Cause(
        evidence_id=first.evidence_id,
        rule=first.rule,
        strength=first.strength,  # type: ignore[arg-type]  # filtered above
        basis=first.basis,
        supported_by=support,
        same_minute=first.same_minute,
    )
