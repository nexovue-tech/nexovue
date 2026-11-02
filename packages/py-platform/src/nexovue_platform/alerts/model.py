"""The alerting vocabulary: the words both processes store, compare and send.

The API validates a rule with these models before saving it; the scheduler parses
the same JSON with them before evaluating it. One definition, so a rule the API
accepts is a rule the scheduler understands.

The tuples are the values the database's CHECK constraints allow. A migration
spells its own copy, because a migration is frozen at the day it ran; the models
module reads these, so the two are compared by `test_alert_model.py`.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

CHANNEL_KINDS: Final = ("owners", "email", "slack", "teams", "discord", "pagerduty", "webhook")
ChannelKind = Literal["owners", "email", "slack", "teams", "discord", "pagerduty", "webhook"]

CHANNEL_HEALTH: Final = ("healthy", "failing")

CONDITION_KINDS: Final = (
    "check_state",
    "new_error_group",
    "service_error_rate",
    "service_latency_p95",
    "service_silent",
    "anomaly_finding",
)

SEVERITIES: Final = ("critical", "warning")
Severity = Literal["critical", "warning"]

# Higher is worse. An incident's severity only ever rises (FR10, escalated).
SEVERITY_RANK: Final[dict[str, int]] = {"warning": 1, "critical": 2}

# What a notification says happened. Digests, summaries and grouped messages are
# shapes a delivery takes, not things that happened, so they are not kinds.
# `closed` is an incident ended without recovering — its rule or check removed, or
# the rule narrowed — told only to the machines that were sent its opening.
# F6 adds four. `joined`: a new signal or endpoint joined an open finding (D29).
# `adopted`: a finding's level became the new normal by itself, told to the owners
# outside any rule (D22). `stopped` and `running_again`: detection stopped judging a
# project, and resumed (D24); their subject is the project, not an incident or finding.
NOTIFICATION_KINDS: Final = (
    "opened",
    "escalated",
    "resolved",
    "repeated",
    "flapping",
    "closed",
    "joined",
    "adopted",
    "stopped",
    "running_again",
)
NotificationKind = Literal[
    "opened",
    "escalated",
    "resolved",
    "repeated",
    "flapping",
    "closed",
    "joined",
    "adopted",
    "stopped",
    "running_again",
]

# Kinds whose subject is a project, so the row names neither an incident nor a finding.
PROJECT_NOTIFICATION_KINDS: Final = ("stopped", "running_again")

# Kinds that end their subject: PagerDuty resolves the alert on either.
ENDING_KINDS: Final = frozenset({"resolved", "closed"})

# `delivering` is claimed under a lease; a worker that dies leaves it to be
# reclaimed when the lease ends (ADR-013). `dropped` is a decision not to send —
# a muted channel, or a PagerDuty resolve withheld while the alert still carries
# another open subject (D35) — recorded so the delivery log still shows it.
NOTIFICATION_STATES: Final = ("pending", "delivering", "delivered", "failed", "dropped")

ATTEMPT_OUTCOMES: Final = ("delivered", "retrying", "failed")

# What an attempt carried. A *Send test* goes through the same channel by the
# same path, so it belongs in the delivery log the customer reads afterwards —
# named, so nobody mistakes a test for an alert that fired
# (`docs/changes/console-review-fixes/`).
ATTEMPT_KINDS: Final = ("notification", "test")

# A threshold rule's position for one subject (F21 Step 5).
EVALUATION_PHASES: Final = ("ok", "pending", "firing", "resolving", "insufficient")

# The check states a rule can alert on. `up` is what resolves, never what fires.
AlertableCheckState = Literal["down", "degraded", "misconfigured"]

# The spec's defaults (FR5): down is critical, the other two are warnings.
DEFAULT_CHECK_SEVERITIES: Final[dict[str, Severity]] = {
    "down": "critical",
    "degraded": "warning",
    "misconfigured": "warning",
}


class _Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CheckStateCondition(_Condition):
    """A check becoming one of the selected states (FR5, FR7).

    `check_ids` absent means every check in the project, including ones created
    after the rule. Each selected state carries its own severity, so a rule can
    treat degraded as a warning and down as critical and still be one incident
    per check (BR1, FR10 escalated).
    """

    kind: Literal["check_state"] = "check_state"
    check_ids: list[uuid.UUID] | None = None
    states: Annotated[dict[AlertableCheckState, Severity], Field(min_length=1)]

    def covers(self, check_id: uuid.UUID) -> bool:
        return self.check_ids is None or check_id in self.check_ids

    def severity_of(self, state: str) -> Severity | None:
        """The severity this rule gives a state, or None when it does not alert on it."""
        for selected, severity in self.states.items():
            if selected == state:
                return severity
        return None


# The default rule every project is created with (FR8, D3): any check down, and
# its recovery, to the organization's owners.
DEFAULT_RULE_NAME: Final = "Any check down"
DEFAULT_RULE_CONDITION: Final = CheckStateCondition(states={"down": "critical"})

# The owners channel every organization has (FR2).
OWNERS_CHANNEL_NAME: Final = "Organization owners"


def check_subject(check_id: uuid.UUID | str) -> str:
    """An incident's subject for a check. Rule and subject together are the
    incident's identity (FR9)."""
    return f"check:{check_id}"


def parse_check_condition(raw: object) -> CheckStateCondition:
    return CheckStateCondition.model_validate(raw)


# ------------------------------------------------------- service conditions
#
# A service is named by its `service.name`. `services` absent means every service
# in the project, including ones that start reporting after the rule was written.

ServiceNames = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=255)]], Field(min_length=1, max_length=100)
]
# Minutes. A wait of zero fires on the first breached minute.
Wait = Annotated[int, Field(ge=0, le=120)]
Window = Annotated[int, Field(ge=1, le=60)]


class _ServiceCondition(_Condition):
    services: ServiceNames | None = None

    def covers(self, service: str) -> bool:
        return self.services is None or service in self.services


class _Threshold(_ServiceCondition):
    """A reading over a window, judged every minute (FR5, FR6)."""

    window_minutes: Window = 5
    # Below this many requests in the window the rule neither fires nor resolves:
    # five failures out of eight requests is not a 62% outage worth a page (FR6).
    min_requests: Annotated[int, Field(ge=1, le=1_000_000)] = 20
    fire_after_minutes: Wait = 5
    resolve_after_minutes: Wait = 5
    severity: Severity


class ErrorRateCondition(_Threshold):
    kind: Literal["service_error_rate"] = "service_error_rate"
    # Percent of requests that failed, at or above which the rule is breached.
    threshold_percent: Annotated[float, Field(gt=0, le=100)]
    severity: Severity = "critical"


class LatencyCondition(_Threshold):
    kind: Literal["service_latency_p95"] = "service_latency_p95"
    threshold_ms: Annotated[float, Field(gt=0, le=600_000)]
    severity: Severity = "warning"


class SilenceCondition(_ServiceCondition):
    """No telemetry of any kind from a service that sent some in the last day (FR5)."""

    kind: Literal["service_silent"] = "service_silent"
    silent_minutes: Annotated[int, Field(ge=5, le=24 * 60)] = 10
    severity: Severity = "critical"


class NewErrorCondition(_ServiceCondition):
    """An error group's first occurrence. An event: said once, never resolved."""

    kind: Literal["new_error_group"] = "new_error_group"
    severity: Severity = "warning"


class AnomalyFindingCondition(_ServiceCondition):
    """A finding the detector opened (F6, FR28). The finding's severity is the
    detector's, never the rule's (BR10 as amended); the rule only chooses which
    findings it sends, at or above `minimum_severity`."""

    kind: Literal["anomaly_finding"] = "anomaly_finding"
    minimum_severity: Severity

    def admits(self, severity: str) -> bool:
        return SEVERITY_RANK[severity] >= SEVERITY_RANK[self.minimum_severity]


# The second default rule every project is created with (F6 FR29): critical findings
# to the organization's owners. A rule has one condition kind, so the first default
# cannot carry it; warnings stay in the feed until a rule asks for them.
CRITICAL_PROBLEMS_RULE_NAME: Final = "Critical problems"
CRITICAL_PROBLEMS_CONDITION: Final = AnomalyFindingCondition(minimum_severity="critical")

# How long a service may have been quiet and still be one that "sent some" (FR5).
SILENCE_LOOKBACK_HOURS: Final = 24

Condition = (
    CheckStateCondition
    | ErrorRateCondition
    | LatencyCondition
    | SilenceCondition
    | NewErrorCondition
    | AnomalyFindingCondition
)


def parse_condition(raw: object) -> Condition:
    """Any rule's condition, by its `kind`. Raises `ValueError` (pydantic's
    `ValidationError` is one) naming what is wrong."""
    kind = raw.get("kind") if isinstance(raw, dict) else None
    match kind:
        case "check_state":
            return CheckStateCondition.model_validate(raw)
        case "service_error_rate":
            return ErrorRateCondition.model_validate(raw)
        case "service_latency_p95":
            return LatencyCondition.model_validate(raw)
        case "service_silent":
            return SilenceCondition.model_validate(raw)
        case "new_error_group":
            return NewErrorCondition.model_validate(raw)
        case "anomaly_finding":
            return AnomalyFindingCondition.model_validate(raw)
        case _:
            raise ValueError(f"unknown condition kind {kind!r}")


def service_subject(service: str) -> str:
    return f"service:{service}"


def error_subject(group_id: str) -> str:
    return f"error:{group_id}"
