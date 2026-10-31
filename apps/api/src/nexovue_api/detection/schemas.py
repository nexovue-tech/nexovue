"""Detection status shapes (F6, FR25): a project's headline and one service's state."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from nexovue_api.findings.schemas import FindingSeverity, SignalName

ProjectDetectionState = Literal["learning", "judging", "data_delayed", "not_running", "no_services"]
ServiceDetectionState = Literal[
    "judged", "learning", "muted", "data_delayed", "not_running", "no_detection_yet"
]
SignalState = Literal["judged", "learning", "too_little_traffic", "not_judged"]
# Why a signal is not judged: its hour's normal is not computed yet, or the
# scheduler records nothing for it yet (the signals Step 3 adds).
NotJudgedReason = Literal["no_baseline", "not_recorded"]


class ProjectDetectionStatus(BaseModel):
    """What the feed's headline says (FR25, AC26, AC42, AC43).

    `services_learning` and `services_judged` sum to `services_total`; a judged
    service may be too quiet to judge this minute. `services_muted` overlaps them:
    a muted service is still judged.
    """

    state: ProjectDetectionState
    services_total: int
    # Waiting for a normal, or not judged a minute yet, also counts as learning.
    services_learning: int
    services_judged: int
    services_delayed: int
    services_muted: int
    # The first local date, in the project's zone, on which a learning signal is judged.
    earliest_judging_date: date | None
    # The project job's last completed run, when that is over 3 minutes old.
    not_running_since: datetime | None
    # The newest judged minute plus the settling lag, while the data is delayed.
    delayed_since: datetime | None
    # Open findings, muted ones included: a muted problem is still not "normal".
    open_findings: int


class SignalStatus(BaseModel):
    signal: SignalName
    state: SignalState
    learning_until: date | None
    reason: NotJudgedReason | None


class FindingMarker(BaseModel):
    """A finding on the service's timeline. Bands are deferred (D32)."""

    id: uuid.UUID
    started_at: datetime
    closed_at: datetime | None
    severity: FindingSeverity


class ServiceDetection(BaseModel):
    state: ServiceDetectionState
    signals: list[SignalStatus]
    muted_until: datetime | None
    snoozed_until: datetime | None
    findings: list[FindingMarker]
    # F36, AC20: a capped list says when the cap bit.
    truncated: bool = False
