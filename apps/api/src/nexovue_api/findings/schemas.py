"""Finding shapes (F6, ADR-022): the feed, one finding, and the false-finding count.

The vocabularies are `nexovue_platform.detection`'s, spelled as literals so the
OpenAPI document names them; `test_findings.py` holds the two equal.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

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


class FindingForMember(Finding):
    """A signed-in person's view of one finding. An API token never sees these two
    fields: they name a person and quote what they wrote (design 2.1, item 8)."""

    acknowledged_by: uuid.UUID | None
    feedback_reason: str | None


class FindingListResponse(BaseModel):
    findings: list[Finding]
    # How many findings in the asked-for state the feed leaves out because they are muted.
    muted_count: int
    # F36, AC20: a capped list says when the cap bit.
    truncated: bool = False


class FindingStats(BaseModel):
    """FR26: findings opened over a window, and how many were false alarms."""

    days: int
    opened: int
    # Of the findings opened in the window, those marked a false alarm, whenever marked.
    false_alarms: int
    # Services with at least one judged minute: the denominator's services.
    judged_services: int
    # false_alarms / (judged_services * days / 7), the budget's own measure (spec,
    # *False-alarm budget*). None when no service has been judged.
    false_finding_rate_per_service_week: float | None
