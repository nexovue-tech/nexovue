"""What an alert says, before any channel shapes it (FR16).

Every message states the rule, the severity, what happened in words, when it
started, how long it lasted when it is over, and one link — to the incident or the
finding, whose page links on to the evidence (D9). Not to the evidence itself: a check can be
deleted, and a message is read long after it was sent. It states nothing from the
evidence itself (BR9, FR18): the failure's reason arrives here already stripped by
`alerts.reasons`, and nothing else from a run is read.

Each channel renders this in its own format — email as plain text, Slack and
Discord with a colour, Teams as a card, PagerDuty as an event (FR17).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from nexovue_platform.alerts.model import SEVERITY_RANK
from nexovue_platform.detection import ADOPTION_AFTER_DAYS

from nexovue_scheduler.alerts.outbox import Notice, Subject, one_line
from nexovue_scheduler.detection.lifecycle import times

_STATE_WORDS: Final = {
    "down": "is down",
    "degraded": "is degraded",
    "misconfigured": "cannot be judged: its own configuration is wrong",
}


@dataclass(frozen=True, slots=True)
class AlertMessage:
    """One notification in words. `summary` is a single line — an email subject, a
    PagerDuty summary, a chat message's first line; `details` are the lines below it."""

    kind: str
    severity: str
    summary: str
    details: tuple[str, ...]
    link: str
    rule_name: str
    manage_link: str | None


@dataclass(frozen=True, slots=True)
class _Said:
    """How one kind of subject is spoken of."""

    # After the subject: "is down", "error rate is 12.3%, at or over 5%".
    happening: str
    # After the subject, for a repeat: "is still down".
    still: str
    # After the subject, when it is over: "recovered", "is sending again".
    recovered: str
    # Lines under the first, while it is open.
    facts: tuple[str, ...]


def compose(notice: Notice, *, app_base_url: str) -> AlertMessage:
    """The words for one notification, whatever its subject."""
    if notice.subject.kind == "finding":
        return _compose_finding(notice, app_base_url=app_base_url)
    if notice.subject.kind == "project":
        return _compose_project(notice, app_base_url=app_base_url)
    return _compose_incident(notice, app_base_url=app_base_url)


def _compose_incident(notice: Notice, *, app_base_url: str) -> AlertMessage:
    """The words for one notification about one incident."""
    base = app_base_url.rstrip("/")
    subject = notice.subject
    said = _said(subject)
    label = subject.label
    rule_name = one_line(subject.rule_name or "")
    new_error = subject.condition_kind == "new_error_group"

    if notice.kind == "resolved":
        summary = f"[resolved] {label} {said.recovered}"
        lines = [f"{label} {said.recovered}."]
    elif notice.kind == "closed":
        # Sent only to machines that were told of the opening, so they stop too.
        summary = f"[closed] {label} is no longer watched"
        lines = [
            f"{label} had not recovered, but nothing watches it any more: its rule or its "
            "check was removed, or the rule stopped covering it."
        ]
    elif notice.kind == "flapping":
        openings = subject.detail.get("openings")
        summary = f"[{notice.severity}] {label} is flapping"
        lines = [
            f"{label} has changed state {openings or 'several'} times in the last hour. "
            "Further changes are not sent until it has stayed recovered for 30 minutes."
        ]
    elif notice.kind == "repeated":
        summary = f"[{notice.severity}] {label} {said.still}"
        lines = [
            f"{label} {said.still}, open for "
            f"{describe_duration(subject.opened_at, notice.created_at)}."
        ]
    elif notice.kind == "escalated":
        worse = (
            f"is now {said.happening[3:]}" if said.happening.startswith("is ") else said.happening
        )
        summary = f"[{notice.severity}] {label} {worse}"
        lines = [f"{label} {worse}."]
    elif new_error:
        summary = f"[{notice.severity}] New error: {label}"
        lines = [f"A new error: {label}."]
    else:
        summary = f"[{notice.severity}] {label} {said.happening}"
        lines = [f"{label} {said.happening}."]

    if notice.kind not in ("resolved", "closed"):
        lines.extend(said.facts)
    lines.append(f"{'Seen' if new_error else 'Started'}: {_moment(subject.opened_at)}.")
    if subject.closed_at is not None and not new_error:
        lasted = describe_duration(subject.opened_at, subject.closed_at)
        lines.append(f"Open for: {lasted}." if notice.kind == "closed" else f"Lasted: {lasted}.")
    lines.append(f"Rule: {rule_name}.")

    return AlertMessage(
        kind=notice.kind,
        severity=notice.severity,
        summary=summary,
        details=tuple(lines),
        link=f"{base}/alerts/incidents/{subject.id}",
        rule_name=rule_name,
        manage_link=f"{base}/alerts/rules/{subject.rule_id}" if subject.rule_id else None,
    )


def _said(subject: Subject) -> _Said:
    detail = subject.detail
    match subject.condition_kind:
        case "service_error_rate" | "service_latency_p95":
            reading = str(detail.get("reading") or "reading")
            unit = str(detail.get("unit") or "")
            threshold = _number(detail.get("threshold"), unit)
            value = _number(detail.get("value"), unit)
            requests = detail.get("requests")
            window = detail.get("window_minutes")
            facts = []
            if requests is not None and window is not None:
                facts.append(
                    f"Over {window} minutes, ending two minutes ago, across "
                    f"{int(requests):,} requests."
                )
            return _Said(
                happening=f"{reading} is {value}, at or over {threshold}",
                still=f"{reading} is still at or over {threshold}",
                recovered=f"{reading} is back under {threshold}",
                facts=tuple(facts),
            )
        case "service_silent":
            last_seen = detail.get("last_seen")
            quiet_facts: tuple[str, ...] = (
                (f"Nothing received since {_moment(datetime.fromisoformat(str(last_seen)))}.",)
                if last_seen
                else ()
            )
            return _Said(
                happening="went silent",
                still="is still silent",
                recovered="is sending again",
                facts=quiet_facts,
            )
        case "new_error_group":
            services = detail.get("services") or []
            error_facts: tuple[str, ...] = (
                (f"In: {', '.join(str(each) for each in services)}.",) if services else ()
            )
            return _Said(
                happening="is a new error",
                still="is still new",
                recovered="",
                facts=error_facts,
            )
        case _:
            state = str(detail.get("state") or "")
            happening = _STATE_WORDS.get(state, f"is {state}")
            facts = []
            if detail.get("reason"):
                facts.append(f"Why: {detail['reason']}.")
            if detail.get("location"):
                facts.append(f"Seen from: {detail['location']}.")
            return _Said(
                happening=happening,
                still=f"is still {happening.removeprefix('is ')}",
                recovered="recovered",
                facts=tuple(facts),
            )


def _number(value: object, unit: str) -> str:
    """*12.3%*, *480 ms*: one decimal where it means something, none where it does not."""
    if not isinstance(value, int | float):
        return "—"
    shown = f"{value:.1f}".rstrip("0").rstrip(".") if value < 100 else f"{value:,.0f}"
    return f"{shown}%" if unit == "%" else f"{shown} {unit}".strip()


def compose_batch(notices: tuple[Notice, ...], *, app_base_url: str) -> AlertMessage:
    """Several notifications as one message: the shape a shared outage (FR13), a
    quiet-hours digest (FR15) and a burst summary (FR12) all take. One line each,
    worst first, and the link to the Alerts screen that lists them all."""
    if len(notices) == 1:
        return compose(notices[0], app_base_url=app_base_url)
    messages = [compose(notice, app_base_url=app_base_url) for notice in notices]
    worst = max(
        (message for message in messages if message.kind != "resolved"),
        key=lambda message: SEVERITY_RANK.get(message.severity, 0),
        default=messages[0],
    )
    still_open = sum(1 for message in messages if message.kind != "resolved")
    opened = [notice for notice in notices if notice.kind == "opened"]
    if (
        len(opened) == len(notices)
        and all(notice.subject.condition_kind == "check_state" for notice in opened)
        and len({notice.subject.detail.get("state") for notice in opened}) == 1
    ):
        # The shape of a shared outage: the same thing happening to several at once.
        state = str(opened[0].subject.detail.get("state") or "failing")
        summary = f"[{worst.severity}] {len(notices)} checks are {state}"
    elif still_open == 0:
        summary = f"[resolved] {len(notices)} incidents recovered"
    else:
        summary = f"[{worst.severity}] {len(notices)} alerts"
    rules = {message.rule_name for message in messages}
    ordered = sorted(
        messages,
        key=lambda message: (message.kind == "resolved", -SEVERITY_RANK.get(message.severity, 0)),
    )
    return AlertMessage(
        kind="opened" if still_open else "resolved",
        severity=worst.severity,
        summary=summary,
        details=tuple(message.summary for message in ordered),
        link=f"{app_base_url.rstrip('/')}/alerts",
        rule_name=rules.pop() if len(rules) == 1 else f"{len(rules)} rules",
        manage_link=None,
    )


# How a closed finding is said, after its subject (FR13, FR20, FR21, FR32, D20).
_FINDING_CLOSED_WORDS: Final = {
    "recovered": "is back to normal",
    "false_alarm": "was marked a false alarm",
    "new_normal": "has a new normal, set by a person",
    "adopted": "has a new normal, adopted by itself",
    "service_gone": "has sent nothing for 24 hours and is treated as gone",
    "could_not_be_judged": "could no longer be judged, so its finding closed",
}
# Each signal's line: its name, and the unit `_number` writes its values in.
_SIGNAL_WORDS: Final = {
    "slower": ("Slower", "ms"),
    "failing": ("Failing", "%"),
    "traffic": ("Traffic", "requests a minute"),
}
_PROJECT_NOTICE_WORDS: Final = {
    "stopped": (
        "Detection stopped for {project}",
        "nexovue's anomaly detection has not judged {project} for 15 minutes, as of {at}. "
        "No finding can open until it runs again.",
    ),
    "running_again": (
        "Detection is running again for {project}",
        "nexovue's anomaly detection is judging {project} again, as of {at}. It starts "
        "from the latest complete minute; what happened while it was stopped is not judged.",
    ),
}


def _compose_finding(notice: Notice, *, app_base_url: str) -> AlertMessage:
    """A finding's words (FR17): its title, which already names the project's zone, and
    one line for each signal. Service and endpoint names are telemetry text: each is
    one line, the subject's from `Subject` and the rest through `one_line` here, and
    the channel escapes them after."""
    base = app_base_url.rstrip("/")
    subject = notice.subject
    detail = subject.detail
    label = subject.label
    title = one_line(str(detail.get("title") or label))
    rule_name = one_line(subject.rule_name or "")
    reason = str(detail.get("closed_reason") or "recovered")
    if notice.kind == "resolved":
        said = _FINDING_CLOSED_WORDS.get(reason, _FINDING_CLOSED_WORDS["recovered"])
        summary = f"[resolved] {label} {said}"
        lines = [f"{label} {said}.", f"It was: {title}."]
    elif notice.kind == "adopted":
        summary = f"[{notice.severity}] {label} has a new normal"
        lines = [_adopted_sentence(label, detail.get("adopted_shifts") or [])]
        lines.append(
            "Nobody marked this finding, so the change became the normal by itself. "
            "If it is not normal, open the finding and mark it."
        )
    elif notice.kind == "flapping":
        summary = f"[{notice.severity}] {label} is flapping"
        lines = [
            f"Detection has opened a finding on {label} five times in the last hour. "
            "Further findings are not sent until it has stayed normal for 30 minutes."
        ]
    elif notice.kind == "escalated":
        summary = f"[critical] Now critical: {title}"
        lines = [f"{title}.", "This finding is now critical."]
    elif notice.kind == "joined":
        summary = f"[{notice.severity}] More is unusual: {title}"
        lines = [f"{title}.", "Another signal or endpoint joined this finding."]
    else:
        summary = f"[{notice.severity}] {title}"
        lines = [f"{title}."]
    if notice.kind not in ("resolved", "adopted", "flapping"):
        lines.extend(_signal_lines(detail.get("signals") or []))
    lines.append(f"Started: {_moment(subject.opened_at)}.")
    if subject.closed_at is not None:
        lines.append(f"Lasted: {describe_duration(subject.opened_at, subject.closed_at)}.")
    if rule_name:
        lines.append(f"Rule: {rule_name}.")
    return AlertMessage(
        kind=notice.kind,
        severity=notice.severity,
        summary=summary,
        details=tuple(lines),
        link=f"{base}/findings/{subject.id}",
        rule_name=rule_name,
        manage_link=f"{base}/alerts/rules/{subject.rule_id}" if subject.rule_id else None,
    )


def _signal_lines(signals: list[dict[str, Any]]) -> list[str]:
    """One line a signal: "Slower: 480 ms at worst, against 300 ms normally (…)"."""
    lines = []
    for entry in signals:
        name, unit = _SIGNAL_WORDS.get(str(entry.get("signal")), ("Unusual", ""))
        endpoint = entry.get("endpoint")
        if endpoint and entry.get("signal") == "failing":
            name = f"{name}, {one_line(str(endpoint))}"
        line = (
            f"{name}: {_number(entry.get('worst'), unit)} at worst, "
            f"against {_number(entry.get('normal'), unit)} normally"
        )
        comparison = entry.get("comparison")
        lines.append(f"{line} ({comparison})." if comparison else f"{line}.")
    return lines


def _adopted_sentence(label: str, shifts: list[dict[str, Any]]) -> str:
    """FR21's words: Checkout's new normal is 1.6 times slower, adopted after 7 days."""
    changes = []
    for shift in shifts:
        ratio = shift.get("ratio")
        if not isinstance(ratio, int | float) or ratio <= 0:
            continue
        if shift.get("signal") == "slower":
            changes.append(f"{times(ratio)} slower" if ratio >= 1 else f"{times(1 / ratio)} faster")
        elif shift.get("signal") == "traffic":
            changes.append(f"{round(100 * ratio)}% of its earlier traffic")
    after = f"Adopted after {ADOPTION_AFTER_DAYS} days."
    if not changes:
        return f"{label}'s new level became its normal. {after}"
    return f"{label}'s new normal is {' and '.join(changes)}. {after}"


def _compose_project(notice: Notice, *, app_base_url: str) -> AlertMessage:
    """Detection stopping or running again for a whole project (D24)."""
    base = app_base_url.rstrip("/")
    project = notice.subject.label
    headline, sentence = _PROJECT_NOTICE_WORDS[notice.kind]
    words = {"project": project, "at": _moment(notice.created_at)}
    summary = headline.format(**words)
    return AlertMessage(
        # Running again is a recovery: it is coloured and filed as one.
        kind="resolved" if notice.kind == "running_again" else notice.kind,
        severity=notice.severity,
        summary=f"[{'resolved' if notice.kind == 'running_again' else notice.severity}] {summary}",
        details=(sentence.format(**words),),
        link=f"{base}/findings",
        rule_name="",
        manage_link=None,
    )


def describe_duration(start: datetime, end: datetime) -> str:
    """Coarse on purpose: *2 h 5 min*, not *2:05:13*. Under a minute is a minute."""
    minutes = max(1, round((end - start).total_seconds() / 60))
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours} h {minutes} min"
    return f"{hours} h" if hours else f"{minutes} min"


def _moment(at: datetime) -> str:
    """UTC, stated as UTC: a message is read in every time zone its channel spans."""
    return at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
