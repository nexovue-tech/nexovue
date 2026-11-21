"""The facts as stored, and the sentences code writes about them: pure (spec FR4 to FR7,
FR13, FR44, FR49; design *Words, recorded once*).

Every sentence is segments: plain text, a customer's name, or a time the console
formats in the project's zone (design, *Segments*). A name passes through
`display_name` and is never nexovue's own words. **No sentence says a thing caused
anything** (BR6): "before", "lines up", "calls to", never the words `BANNED_WORDS`
lists, which `test_evidence_rules.py` holds every sentence to.

The fact list is ordered as FR44 says, and bounded (FR51): what passes
`MAX_EVIDENCE_ITEMS` becomes one `truncated` absence.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform.evidence import (
    BASES,
    COULD_NOT_READ,
    LIKELY,
    MAX_EVIDENCE_ITEMS,
    NO_DEPENDENCY_TRACES,
    NO_DEPLOY_INFORMATION,
    NO_HOST_NAMES,
    READS,
    TELEMETRY,
    TELEMETRY_TEXT,
    TRUNCATED,
    UNSHOWABLE_TYPE,
    VERIFIED,
    Segment,
    at_segment,
    deploy_rate_words,
    evidence_id,
    name_segment,
    text_segment,
)
from nexovue_platform.releases import COVERAGE_NONE, VERSIONS_ONLY

from nexovue_scheduler.evidence.rules import (
    HOST_HIGH_UTILIZATION,
    BaseRate,
    Cause,
    DeployFact,
    EdgeFact,
    ErrorGroup,
    Facts,
    OtherFinding,
    Release,
    base_rate,
    dependency_evidence_id,
    deploy_evidence_id,
    error_evidence_id,
    failures_moved,
    finding_evidence_id,
    in_window,
    name_key,
    traffic_evidence_id,
)

# Words no code-written sentence may hold (BR6, FR37's list; spec, *Must not be claimed*).
BANNED_WORDS: Final = (
    "caused",
    "because",
    "root cause",
    "due to",
    "triggered",
    "led to",
    "resulted",
    "responsible for",
    "culprit",
    "at fault",
)
# Coverage values that mean the service never sent a signed deploy (F28, AC12).
_NO_SIGNED_DEPLOYS: Final = (COVERAGE_NONE, VERSIONS_ONLY)
_READ_WORDS: Final = {
    "deploys": "deploys",
    "dependencies": "calls to other services",
    "traffic": "traffic",
    "errors": "recent errors",
    "checks": "checks",
    "incidents": "linked alerts",
    "findings": "other findings",
    "hosts": "host readings",
    "intake": "the intake record",
    "releases": "releases",
}
assert set(READS) <= set(_READ_WORDS)


@dataclass(frozen=True, slots=True)
class Record:
    """What one gather stores, before aliases and the fingerprint."""

    items: list[dict[str, Any]]
    summary: dict[str, Any]
    reads_failed: int


# ------------------------------------------------------------------ words


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def gap_words(earlier: datetime, later: datetime) -> str:
    """ "4 minutes", "2 h 10 min", "3 days": how long one moment was before another."""
    minutes = max(0, int((later - earlier) // timedelta(minutes=1)))
    if minutes < 1:
        return "under a minute"
    if minutes == 1:
        return "1 minute"
    if minutes < 60:
        return f"{minutes} minutes"
    hours, rest = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {rest} min" if rest else f"{hours} h"
    return f"{hours // 24} days"


def share_words(share: float) -> str:
    """0.4% and 6%: a failure share as a percentage."""
    percent = share * 100
    if percent >= 10:
        return f"{percent:.0f}%"
    return f"{percent:.1f}".removesuffix(".0") + "%"


def rate_words(rate: BaseRate) -> str:
    """FR7: the base rate, never claiming a week it does not have."""
    return deploy_rate_words(rate.deploys_last_7_days, rate.days_of_records)


def short_rate_words(rate: BaseRate) -> str:
    """The card's short form: "3 in 7 days", or "3 in 2 days"."""
    days = 7 if rate.days_of_records >= 7 else max(rate.days_of_records, 1)
    return f"{rate.deploys_last_7_days} in {days} day{'s' if days != 1 else ''}"


def _number(value: float) -> str:
    return f"{value:.0f}" if value >= 10 else f"{value:.1f}".removesuffix(".0")


# ------------------------------------------------------------------ items


def _item(
    kind: str,
    identity: str,
    basis: str | None,
    sentence: list[Segment],
    values: dict[str, Any],
    *,
    role: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": identity,
        "kind": kind,
        "basis": basis,
        "sentence": sentence,
        "values": values,
    }
    if role is not None:
        item["role"] = role
    return item


def deploy_item(deploy: DeployFact, facts: Facts, *, dependency: bool) -> dict[str, Any]:
    start = facts.finding.started_at
    rate = base_rate(
        (facts.histories if deploy.signed else facts.seen_histories).get(deploy.service),
        start,
        facts.zone,
    )
    lead = "Deploy of " if deploy.signed else "Version "
    middle = " deployed " if deploy.signed else " first seen in telemetry "
    sentence = [
        text_segment(lead),
        name_segment(deploy.service),
        text_segment(" "),
        name_segment(deploy.version),
        text_segment(f"{middle}at "),
        at_segment(_iso(deploy.at)),
        text_segment(f", {gap_words(deploy.at, start)} before this. {rate_words(rate)}."),
    ]
    return _item(
        "deploy",
        deploy_evidence_id(deploy),
        VERIFIED if deploy.signed else TELEMETRY_TEXT,
        sentence,
        {
            "service": deploy.service,
            "version": deploy.version,
            "at": _iso(deploy.at),
            "signed": deploy.signed,
            "dependency": dependency,
            "minutes_before": int((start - deploy.at) // timedelta(minutes=1)),
            "deploys_last_7_days": rate.deploys_last_7_days,
            "days_of_records": rate.days_of_records,
            "chance": rate.chance,
        },
    )


def dependency_item(edge: EdgeFact, caller: str | None, *, keyed_by_caller: bool) -> dict[str, Any]:
    sentence: list[Segment] = [text_segment("Calls to "), name_segment(edge.callee)]
    if caller is not None and keyed_by_caller:
        sentence[:0] = [name_segment(caller), text_segment(": ")]
    before = edge.failures_before / edge.calls_before if edge.calls_before else None
    during = edge.failures_during / edge.calls_during if edge.calls_during else None
    parts: list[str] = []
    if failures_moved(edge) and before is not None and during is not None:
        parts.append(f"failures {share_words(before)} → {share_words(during)}")
    if edge.p95_before is not None and edge.p95_during is not None:
        parts.append(f"p95 {_number(edge.p95_before)} ms → {_number(edge.p95_during)} ms")
    if not parts and before is not None and during is not None:
        parts.append(f"failures {share_words(before)} → {share_words(during)}")
    detail = ": " + ", ".join(parts) if parts else ""
    sentence.append(text_segment(f"{detail}, {edge.calls_during} calls in this window."))
    return _item(
        "dependency",
        dependency_evidence_id(edge.callee, caller if keyed_by_caller else None),
        TELEMETRY,
        sentence,
        {
            "caller": caller,
            "callee": edge.callee,
            "callee_attr": edge.callee_attr,
            "callee_kind": edge.callee_kind,
            "reported": edge.reported,
            "calls_before": edge.calls_before,
            "calls_during": edge.calls_during,
            "failure_share_before": before,
            "failure_share_during": during,
            "p95_before": edge.p95_before,
            "p95_during": edge.p95_during,
            "from_browser": edge.from_browser,
        },
    )


def finding_item(other: OtherFinding, start: datetime) -> dict[str, Any]:
    subject = (
        [name_segment(other.service)]
        if other.service is not None
        else [text_segment("The project")]
    )
    when = "earlier" if other.started_at < start else "later"
    sentence = [
        *subject,
        text_segment(" has its own finding, open since "),
        at_segment(_iso(other.started_at)),
        text_segment(
            f", {gap_words(min(other.started_at, start), max(other.started_at, start))} {when}."
        ),
    ]
    return _item(
        "other_finding",
        finding_evidence_id(other),
        TELEMETRY,
        sentence,
        {"finding_id": other.id, "service": other.service, "started_at": _iso(other.started_at)},
    )


def error_item(group: ErrorGroup) -> dict[str, Any]:
    named = (
        [name_segment(group.exception_type)]
        if group.exception_type is not None
        else [text_segment(UNSHOWABLE_TYPE)]
    )
    sentence = [
        text_segment("New error: "),
        *named,
        text_segment(", first at "),
        at_segment(_iso(group.first_seen)),
        text_segment(f", {group.count} times. Not seen in the hour before."),
    ]
    return _item(
        "error_group",
        error_evidence_id(group),
        TELEMETRY_TEXT,
        sentence,
        {
            "group_id": group.group_id,
            "exception_type": group.exception_type,
            "services": list(group.services),
            "count": group.count,
            "first_seen": _iso(group.first_seen),
        },
    )


def release_item(release: Release, start: datetime, *, role: str) -> dict[str, Any]:
    """FR49: the last deploy at any age, and F28's 2-hour list. Fact only."""
    if role == "last_before":
        sentence = [
            text_segment("Last deploy: "),
            name_segment(release.version),
            text_segment(f", {gap_words(release.started_at, start)} before this."),
        ]
    else:
        sentence = [
            name_segment(release.version),
            text_segment(" of "),
            name_segment(release.service),
            text_segment(f", {gap_words(release.started_at, start)} before this"),
            text_segment(" (this service calls it)." if release.called else "."),
        ]
    return _item(
        "deploy",
        evidence_id("release", release.period_id),
        VERIFIED if release.signed else TELEMETRY_TEXT,
        sentence,
        {
            "release_id": release.release_id,
            "period_id": release.period_id,
            "service": release.service,
            "version": release.version,
            "at": _iso(release.started_at),
            "signed": release.signed,
            "called": release.called,
        },
        role=role,
    )


def absence_item(
    reason: str, *, read: str | None = None, service: str | None = None
) -> dict[str, Any]:
    if reason == NO_DEPLOY_INFORMATION:
        sentence = [text_segment("No deploy information for this service.")]
    elif reason == NO_DEPENDENCY_TRACES:
        sentence = [
            text_segment("No traces for "),
            name_segment(service or ""),
            text_segment("'s calls to other services in this window."),
        ]
    elif reason == NO_HOST_NAMES:
        sentence = [text_segment("No host names in this project's readings, so no host facts.")]
    elif reason == COULD_NOT_READ:
        sentence = [
            text_segment(f"Could not read {_READ_WORDS.get(read or '', 'this')} this time.")
        ]
    else:
        sentence = [text_segment("More facts than one finding keeps; the rest were left out.")]
    identity = evidence_id("absence", f"{reason}:{read}" if read else reason)
    return _item("absence", identity, None, sentence, {"reason": reason, "read": read})


def _service_items(facts: Facts) -> list[dict[str, Any]]:
    finding = facts.finding
    start = finding.started_at
    service = finding.service
    items: list[dict[str, Any]] = []
    callees = {edge.callee for edge in facts.edges if edge.reported}
    for deploy in facts.deploys:
        if in_window(deploy.at, start) and (deploy.service == service or deploy.service in callees):
            items.append(deploy_item(deploy, facts, dependency=deploy.service != service))
    for edge in facts.edges:
        items.append(dependency_item(edge, service, keyed_by_caller=False))
    for other in facts.dependency_findings:
        items.append(finding_item(other, start))
    if facts.traffic is not None and service is not None:
        items.append(_traffic_item(facts, service))
    if facts.last_release is not None:
        items.append(release_item(facts.last_release, start, role="last_before"))
    for release in facts.nearby_releases:
        if facts.last_release is None or release.period_id != facts.last_release.period_id:
            items.append(release_item(release, start, role="near"))
    if "deploys" not in facts.failed_reads and facts.coverage in _NO_SIGNED_DEPLOYS:
        items.append(absence_item(NO_DEPLOY_INFORMATION))
    if "dependencies" not in facts.failed_reads and not facts.edges:
        items.append(absence_item(NO_DEPENDENCY_TRACES, service=service))
    return items


def _project_items(facts: Facts) -> list[dict[str, Any]]:
    finding = facts.finding
    start = finding.started_at
    items: list[dict[str, Any]] = []
    for deploy in facts.deploys:
        if deploy.signed and deploy.service in finding.services and in_window(deploy.at, start):
            items.append(deploy_item(deploy, facts, dependency=False))
    seen: set[str] = set()
    for _caller, edge in facts.project_edges:
        if edge.callee not in seen:
            seen.add(edge.callee)
            items.append(dependency_item(edge, None, keyed_by_caller=False))
    for other in facts.dependency_findings:
        items.append(finding_item(other, start))
    for refused in facts.intake_refused_at:
        items.append(
            _item(
                "infrastructure",
                evidence_id("intake", _iso(refused)),
                VERIFIED,
                [
                    text_segment("This install's intake refused data at "),
                    at_segment(_iso(refused)),
                    text_segment(", for every project on it."),
                ],
                {"measure": "intake_refused", "at": _iso(refused)},
            )
        )
    return items


def _traffic_item(facts: Facts, service: str) -> dict[str, Any]:
    traffic = facts.traffic
    assert traffic is not None
    before = traffic.before_per_minute
    lead = traffic.lead_per_minute
    if before is None or lead is None:
        words = "Requests a minute before this could not be compared."
    else:
        words = (
            f" took {_number(lead)} requests a minute in the 15 minutes before this, "
            f"against {_number(before)} in the hour before that."
        )
    sentence = (
        [name_segment(service), text_segment(words)]
        if before is not None and lead is not None
        else [text_segment(words)]
    )
    return _item(
        "traffic",
        traffic_evidence_id(service),
        TELEMETRY,
        sentence,
        {"service": service, "before_per_minute": before, "lead_per_minute": lead},
    )


def _shared_items(facts: Facts) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for group in facts.error_groups:
        items.append(error_item(group))
    for change in facts.checks:
        items.append(
            _item(
                "check",
                evidence_id("check", f"{change.check_id}:{change.state}:{_iso(change.at)}"),
                VERIFIED,
                [
                    text_segment("Check "),
                    name_segment(change.name),
                    text_segment(f" went {change.state} at "),
                    at_segment(_iso(change.at)),
                    text_segment(" (a check is project-wide)."),
                ],
                {
                    "check_id": change.check_id,
                    "name": change.name,
                    "state": change.state,
                    "at": _iso(change.at),
                },
            )
        )
    for incident in facts.incidents:
        items.append(
            _item(
                "incident",
                evidence_id("incident", incident.id),
                TELEMETRY,
                [
                    text_segment("Linked alert "),
                    name_segment(incident.rule_name),
                    text_segment(", opened at "),
                    at_segment(_iso(incident.opened_at)),
                    text_segment("."),
                ],
                {
                    "incident_id": incident.id,
                    "rule_name": incident.rule_name,
                    "opened_at": _iso(incident.opened_at),
                },
            )
        )
    for reading in facts.hosts:
        if reading.utilization < HOST_HIGH_UTILIZATION:
            continue
        items.append(
            _item(
                "infrastructure",
                evidence_id("host", f"{name_key(reading.host)}:{reading.measure}"),
                TELEMETRY,
                [
                    text_segment("Host "),
                    name_segment(reading.host),
                    text_segment(
                        f" reached {reading.utilization * 100:.0f}% {reading.measure} at "
                    ),
                    at_segment(_iso(reading.at)),
                    text_segment(" (whole project, not tied to this service)."),
                ],
                {
                    "host": reading.host,
                    "measure": reading.measure,
                    "utilization": reading.utilization,
                    "at": _iso(reading.at),
                },
            )
        )
    if "hosts" not in facts.failed_reads and not facts.host_names_present:
        items.append(absence_item(NO_HOST_NAMES))
    for read in sorted(facts.failed_reads):
        items.append(absence_item(COULD_NOT_READ, read=read))
    return items


# ------------------------------------------------------------------ order


def ordered(items: Sequence[dict[str, Any]], cause: Cause | None) -> list[dict[str, Any]]:
    """FR44: the cause's fact, its support, then by basis, then absences. Ties keep the
    order they were read in."""
    leading = [] if cause is None else [cause.evidence_id]
    support = [] if cause is None else list(cause.supported_by)

    def rank(item: dict[str, Any]) -> tuple[int, int]:
        if item["id"] in leading:
            return (0, 0)
        if item["id"] in support:
            return (1, support.index(item["id"]))
        if item["kind"] == "absence":
            return (6, 0)
        return (2 + BASES.index(item["basis"]) if item["basis"] in BASES else 5, 0)

    return sorted(items, key=rank)


def bounded(items: list[dict[str, Any]], limit: int = MAX_EVIDENCE_ITEMS) -> list[dict[str, Any]]:
    """FR51: past the bound, one `truncated` absence stands for the rest. Unique ids."""
    unique = list({item["id"]: item for item in items}.values())
    if len(unique) <= limit:
        return unique
    return [*unique[: limit - 1], absence_item(TRUNCATED)]


# ------------------------------------------------------------------ the cause


def cause_lines(
    cause: Cause | None, items: Iterable[dict[str, Any]], facts: Facts
) -> dict[str, Any]:
    """The header line and the card's short form (FR14, FR18). Strength comes first;
    a cause not `verified` says so (AC67)."""
    if cause is None:
        return {"header": None, "card": None}
    item = next((each for each in items if each["id"] == cause.evidence_id), None)
    if item is None:
        return {"header": None, "card": None}
    word = "Likely" if cause.strength == LIKELY else "Possibly"
    if cause.basis != VERIFIED:
        word += ", from telemetry only"
    prefix = text_segment(f"{word}: ")
    values = item["values"]
    if item["kind"] == "deploy":
        start = facts.finding.started_at
        rate = BaseRate(values["deploys_last_7_days"], values["days_of_records"], values["chance"])
        header = [
            prefix,
            text_segment("the deploy of "),
            name_segment(values["service"]),
            text_segment(" "),
            name_segment(values["version"]),
            text_segment(
                f", {gap_words(datetime.fromisoformat(values['at']), start)} before this "
                f"({rate_words(rate)}). The deploy is a signed record; the link to this "
                "finding is timing only."
            ),
        ]
        card = [
            prefix,
            text_segment("deploy of "),
            name_segment(values["service"]),
            text_segment(" "),
            name_segment(values["version"]),
            text_segment(f" ({short_rate_words(rate)})"),
        ]
    elif item["kind"] == "dependency":
        detail = _dependency_detail(cause, facts, values)
        header = [
            prefix,
            text_segment("calls to "),
            name_segment(values["callee"]),
            text_segment(detail),
        ]
        card = [prefix, text_segment("calls to "), name_segment(values["callee"])]
    elif item["kind"] == "traffic":
        header = [
            prefix,
            text_segment("traffic to "),
            name_segment(values["service"]),
            text_segment(" rose before it slowed."),
        ]
        card = [prefix, text_segment("traffic rose before the slowdown")]
    else:
        header = [prefix, *item["sentence"]]
        card = [prefix, *item["sentence"]]
    return {"header": header, "card": card}


def _dependency_detail(cause: Cause, facts: Facts, values: dict[str, Any]) -> str:
    callee = values["callee"]
    own = next((f for f in facts.dependency_findings if f.service == callee), None)
    if own is not None and not cause.same_minute and own.started_at < facts.finding.started_at:
        gap = gap_words(own.started_at, facts.finding.started_at)
        return f", which opened its own finding {gap} earlier."
    if cause.same_minute:
        return ", which opened its own finding in the same minute as this one."
    return " moved in the same minutes."


# ------------------------------------------------------------------ the record


def record(facts: Facts, cause: Cause | None) -> Record:
    """Every fact one gather read, ordered and bounded, with the header and card."""
    items = _project_items(facts) if facts.finding.scope == "project" else _service_items(facts)
    items.extend(_shared_items(facts))
    for read in sorted(facts.truncated_reads):
        items.append(absence_item(TRUNCATED, read=read))
    kept = bounded(ordered(items, cause))
    return Record(
        items=kept, summary=cause_lines(cause, kept, facts), reads_failed=len(facts.failed_reads)
    )


def all_words(segments: Iterable[dict[str, str]]) -> str:
    """The words nexovue wrote in a sentence, without the customer's names."""
    return "".join(segment.get("text", "") for segment in segments)


__all__ = ["BANNED_WORDS", "Record", "all_words", "record", "share_words"]
