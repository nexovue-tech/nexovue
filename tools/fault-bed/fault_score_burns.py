"""Score a recorded run for a reliability target's burns (F19, AC49). Pure, like `fault_score.py`.

The target is on one service (`payment`, 99.9% over 28 days). Over the `paymentFailure`
injections a burn must fire in at least 4 of 5. A burn already open when an injection starts
counts as caught only if it escalates inside it, and the report says how many were already
open. Where a finding also opens, the report says whether the burn was linked to it and how many
emails the burn's own incident delivered or held back. The reference week is reported burn by
burn, beside the failure rate the demo itself had: a reported number, never a fixed zero.

**A demo result.** Demo traffic is steady and artificial, and a pass says little about a
customer's real burn alerts; every table says so.

Not read: the finding's own email. A read token cannot read a channel's delivery log, so the
count is of the burn incident's notifications, which the incident lists with their reasons.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from fault_model import REFERENCE, REQUIRED_SHARE, BurnSeen, Injection, RecordedRun

TARGET_FAULT: Final = "paymentFailure"
TARGET_SERVICE: Final = "payment"
# An escalation is delivered within a delivery sweep and a retry of the injection's end.
ESCALATION_GRACE: Final = timedelta(minutes=15)
# The owners channel is delivered by email, so its messages are emails too.
EMAIL_CHANNELS: Final = frozenset({"email", "owners"})
DELIVERED: Final = "delivered"
DROPPED: Final = "dropped"
DEMO_LABEL: Final = (
    "**Demo result.** Run on the OpenTelemetry Demo's steady, artificial traffic. It shows the "
    "path works, not how a customer's burn alerts behave."
)


@dataclass(frozen=True, slots=True)
class BurnInjection:
    round: int
    start: datetime
    how: str | None  # "opened", "escalated" or None
    burn: BurnSeen | None
    time_to_catch: timedelta | None
    finding_opened: bool
    linked: bool
    emails_delivered: int
    emails_withheld: int


@dataclass(frozen=True, slots=True)
class BurnCard:
    injections: tuple[BurnInjection, ...]
    already_open: int
    already_open_escalated: int
    reference_burns: tuple[BurnSeen, ...]
    baseline_rate: float | None
    baseline_requests: int
    skipped: int

    @property
    def caught(self) -> int:
        return sum(1 for i in self.injections if i.how is not None)

    @property
    def with_both(self) -> int:
        return sum(1 for i in self.injections if i.how is not None and i.finding_opened)

    @property
    def linked(self) -> int:
        return sum(1 for i in self.injections if i.how is not None and i.linked)

    @property
    def verdict(self) -> str:
        if not self.injections:
            return "not run"
        if len(self.injections) < 5:
            return f"not proved: {len(self.injections)} injections, 5 needed"
        needed = math.ceil(REQUIRED_SHARE * len(self.injections))
        if self.caught >= needed:
            return "pass"
        return f"fail: {needed} needed, {self.caught} caught"


def _of_target(burn: BurnSeen, service: str) -> bool:
    return burn.label.startswith(f"{service} ")


def _open_at(burn: BurnSeen, moment: datetime) -> bool:
    return burn.opened_at < moment and (burn.resolved_at is None or burn.resolved_at > moment)


def _escalated_in(burn: BurnSeen, injection: Injection) -> bool:
    until = injection.end + ESCALATION_GRACE
    return any(
        m.kind == "escalated"
        and m.delivered_at is not None
        and injection.start <= m.delivered_at <= until
        for m in burn.messages
    )


def _emails(burn: BurnSeen, injection: Injection) -> tuple[int, int]:
    until = injection.end + ESCALATION_GRACE
    sent = sum(
        1
        for m in burn.messages
        if m.channel_kind in EMAIL_CHANNELS
        and m.state == DELIVERED
        and m.delivered_at is not None
        and injection.start <= m.delivered_at <= until
    )
    held = sum(1 for m in burn.messages if m.channel_kind in EMAIL_CHANNELS and m.state == DROPPED)
    return sent, held


def _injection(
    run: RecordedRun, injection: Injection, burns: list[BurnSeen], service: str
) -> tuple[BurnInjection, bool, bool]:
    opened = [b for b in burns if injection.start <= b.opened_at < injection.end]
    standing = [b for b in burns if _open_at(b, injection.start)]
    escalated = [b for b in standing if _escalated_in(b, injection)]
    chosen, how = (opened[0], "opened") if opened else (None, None)
    if chosen is None and escalated:
        chosen, how = escalated[0], "escalated"
    findings = {
        f.id
        for f in run.findings
        if injection.start <= f.opened_at < injection.end and service in f.services
    }
    sent, held = _emails(chosen, injection) if chosen else (0, 0)
    scored = BurnInjection(
        injection.round,
        injection.start,
        how,
        chosen,
        None if chosen is None or how != "opened" else chosen.opened_at - injection.start,
        bool(findings),
        bool(chosen and findings & set(chosen.findings)),
        sent,
        held,
    )
    return scored, bool(standing), bool(escalated)


def score_burns(
    run: RecordedRun, *, fault: str = TARGET_FAULT, service: str = TARGET_SERVICE
) -> BurnCard:
    burns = [b for b in run.burns if _of_target(b, service)]
    planned = [i for i in run.plan.injections if i.fault == fault]
    scored: list[BurnInjection] = []
    open_count = escalated_count = 0
    for injection in planned:
        one, was_open, did_escalate = _injection(run, injection, burns, service)
        scored.append(one)
        open_count += was_open and one.how != "opened"
        escalated_count += did_escalate and one.how != "opened"
    reference_end = run.plan.reference_start + REFERENCE
    errors = requests = 0
    for observation in run.observations:
        if observation.fault != fault:
            continue
        for window in observation.reference:
            if window.service == service:
                errors += window.errors
                requests += window.requests
    return BurnCard(
        injections=tuple(scored),
        already_open=open_count,
        already_open_escalated=escalated_count,
        reference_burns=tuple(
            b for b in burns if run.plan.reference_start <= b.opened_at < reference_end
        ),
        baseline_rate=errors / requests if requests else None,
        baseline_requests=requests,
        skipped=sum(1 for s in run.skipped if s.fault == fault),
    )


def render_burns(card: BurnCard, *, service: str = TARGET_SERVICE) -> str:
    lines = [
        f"## AC49: reliability target burns on `{service}`",
        "",
        DEMO_LABEL,
        "",
        f"Verdict: **{card.verdict}**. A burn fired in {card.caught} of {len(card.injections)} "
        f"injections ({card.skipped} skipped). Already open when an injection began: "
        f"{card.already_open}, of which {card.already_open_escalated} escalated inside it.",
        f"A finding also opened in {card.with_both} injections with a burn caught; the burn was "
        f"linked to it in {card.linked}.",
        "",
        "| Round | Started | Caught | Time to catch | Finding also opened | Linked "
        "| Emails delivered | Emails held back |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i in card.injections:
        taken = (
            "—" if i.time_to_catch is None else f"{i.time_to_catch.total_seconds() / 60:.1f} min"
        )
        lines.append(
            f"| {i.round} | {i.start:%Y-%m-%d %H:%M} UTC | {i.how or 'no'} | {taken} "
            f"| {'yes' if i.finding_opened else 'no'} | {'yes' if i.linked else 'no'} "
            f"| {i.emails_delivered} | {i.emails_withheld} |"
        )
    rate = (
        "not recorded"
        if card.baseline_rate is None
        else f"{card.baseline_rate * 100:.1f}% of {card.baseline_requests:,} requests"
    )
    plural = "burn" if len(card.reference_burns) == 1 else "burns"
    lines += [
        "",
        f"No-fault reference week: {len(card.reference_burns)} {plural}, read against the demo's "
        f"own failure rate for `{service}` in the same windows: {rate}.",
        "",
        "The emails counted are the burn incident's own notifications. The finding's email is "
        'not readable with a token, so "one email" is read from the burn\'s side only.',
    ]
    if card.reference_burns:
        lines += ["", "| Opened | Closed | Severity |", "|---|---|---|"]
        lines += [
            f"| {b.opened_at:%Y-%m-%d %H:%M} UTC "
            f"| {'open' if b.resolved_at is None else f'{b.resolved_at:%Y-%m-%d %H:%M} UTC'} "
            f"| {b.severity} |"
            for b in card.reference_burns
        ]
    return "\n".join(lines) + "\n"
