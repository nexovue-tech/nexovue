"""Finding shapes (F6, ADR-022): the feed, one finding and its page, the false-finding
count, and what a person may do to a finding (FR19 to FR21).

The vocabularies are `nexovue_platform.detection`'s, spelled as literals so the
OpenAPI document names them; `test_findings.py` holds the two equal.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

FindingScope = Literal["service", "project"]
FindingSeverity = Literal["critical", "warning"]
# Acknowledged is derived, not stored: an open finding somebody has acknowledged (FR19).
FindingState = Literal["open", "acknowledged", "closed"]
ClosedReason = Literal[
    "recovered", "false_alarm", "new_normal", "adopted", "service_gone", "could_not_be_judged"
]
SignalName = Literal["slower", "failing", "traffic"]
SignalUnit = Literal["ms", "%", "rpm"]
# What a feed request asks for. `open` includes acknowledged findings.
FeedState = Literal["open", "closed", "all"]


class StoredSignal(BaseModel):
    """One entry of `findings.signals` as the project job writes it, leading signal first.

    Its unit is not stored: it follows from the signal.
    """

    signal: SignalName
    # None when there was no reading, never 0: a missing number and a real zero differ.
    normal: float | None
    current: float | None
    worst: float | None
    # The sentence naming what it was compared with ("Tuesdays around 15:00, last 3 weeks").
    comparison: str
    endpoint: str | None = None


class FindingSignal(StoredSignal):
    unit: SignalUnit


class Finding(BaseModel):
    """What any reader sees, a person or an API token."""

    id: uuid.UUID
    project_id: uuid.UUID
    scope: FindingScope
    # None for a project finding, which spans several services.
    service_name: str | None
    title: str
    severity: FindingSeverity
    state: FindingState
    closed_reason: ClosedReason | None
    muted: bool
    started_at: datetime
    closed_at: datetime | None
    acknowledged_at: datetime | None
    signals: list[FindingSignal]
    # Up to the last 60 readings of the leading signal, oldest first; empty when none exist.
    trend: list[float]


class SignalPoint(BaseModel):
    """One judged minute of one signal, in the signal's unit."""

    at: datetime
    # None when the minute had nothing to measure: no p95, no requests, no normal.
    value: float | None
    normal: float | None
    # Where the sensitivity's minimum change lies (spec, *Product values*): above the
    # normal for *slower* and *failing more*, below it for *losing traffic*. Between
    # it and the normal is the band a reading may move in without being unusual.
    edge: float | None


class SignalSeries(BaseModel):
    """One signal's readings against its normal band, oldest first (AC38)."""

    signal: SignalName
    unit: SignalUnit
    points: list[SignalPoint]


class LinkedIncident(BaseModel):
    """A threshold or *went silent* incident on the same service (BR2, FR16)."""

    id: uuid.UUID
    rule_name: str
    condition_kind: str
    opened_at: datetime
    resolved_at: datetime | None


class FindingDetail(Finding):
    """One finding as its page reads it (FR31)."""

    # Each signal's readings while they last (35 days at most); empty once expired,
    # and always for a project finding, which has no readings of its own (AC40).
    series: list[SignalSeries]
    incidents: list[LinkedIncident]
    # A project finding's services (FR15); empty for a service finding.
    services: list[str]


class ProposedShift(BaseModel):
    """What *This is the new normal* would do to one signal (FR21), in its unit."""

    signal: SignalName
    unit: SignalUnit
    ratio: float
    normal_before: float | None
    normal_after: float | None


class NewNormalPreview(BaseModel):
    """Whether an open finding's level is known well enough to become its normal, and
    what that normal would be: the dangerous action states its effect in real numbers
    before it can be confirmed (design, *The finding page*)."""

    available: bool
    # The fewest readings any of its signals has, and how many it needs.
    readings: int
    needed: int
    signals: list[ProposedShift]


class FindingForMember(FindingDetail):
    """A signed-in person's view of one finding. An API token never sees these fields:
    they name a person and quote what they wrote (design 2.1, item 8), or prepare an
    action only a person can take."""

    acknowledged_by: uuid.UUID | None
    acknowledged_by_name: str | None
    feedback_reason: str | None
    # None when the finding is closed, or is a project's, which has no normal to move.
    new_normal: NewNormalPreview | None


# Long enough for a sentence or two about why; bounded so a reason is not a document.
FEEDBACK_REASON_MAX_LENGTH = 1000


class FalseAlarmRequest(BaseModel):
    reason: Annotated[str, Field(max_length=FEEDBACK_REASON_MAX_LENGTH)] | None = None


class FindingChanged(BaseModel):
    """What an action did, for the page's success state (design, *The finding page*):
    the finding as it now is, and the channels its *resolved* message goes to."""

    finding: FindingForMember
    resolved_to: list[str]


class FindingListResponse(BaseModel):
    findings: list[Finding]
    # How many findings in the asked-for state the feed leaves out because they are muted.
    muted_count: int
    # F36, AC20: a capped list says when the cap bit.
    truncated: bool = False


class ServiceFindingStats(BaseModel):
    """FR26 for one service: its findings opened over the window, and its false alarms."""

    service_name: str
    opened: int
    false_alarms: int
    # The service's own measure against the budget of one false finding a week.
    false_alarms_per_week: float


class FindingStats(BaseModel):
    """FR26: findings opened over a window, and how many were false alarms, for the
    project and for each of its services."""

    days: int
    opened: int
    # Of the findings opened in the window, those marked a false alarm, whenever marked.
    false_alarms: int
    # Services with at least one judged minute: the denominator's services.
    judged_services: int
    # false_alarms / (judged_services * days / 7), the budget's own measure (spec,
    # *False-alarm budget*). None when no service has been judged.
    false_finding_rate_per_service_week: float | None
    # Each service with a finding opened in the window, most false alarms first. A
    # project finding spans several services, so it counts for the project alone.
    services: list[ServiceFindingStats]
