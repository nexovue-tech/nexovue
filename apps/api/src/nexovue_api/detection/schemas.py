"""Detection shapes (F6): a project's headline and one service's state (FR25), and the
user's controls over them: snooze and mute (FR22), sensitivity and time zone (FR18,
FR24)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

from nexovue_api.data.postgres.models import TIMEZONE_NAME_LENGTH
from nexovue_api.findings.schemas import FindingSeverity, SignalName

ProjectDetectionState = Literal[
    "learning", "judging", "data_delayed", "not_running", "no_services", "too_many_services"
]
ServiceDetectionState = Literal[
    "judged",
    "learning",
    "muted",
    "data_delayed",
    "not_running",
    "no_detection_yet",
    "too_many_services",
    "no_incoming_requests",
]
SignalState = Literal["judged", "learning", "too_little_traffic", "not_judged"]
# Why a signal is not judged: its hour's normal is not computed yet, or the
# scheduler records nothing for it yet (the signals Step 3 adds).
NotJudgedReason = Literal["no_baseline", "not_recorded"]


class ProjectDetectionStatus(BaseModel):
    """What the feed's headline says (FR25, AC26, AC42, AC43).

    `services_learning`, `services_judged` and `services_too_many` sum to
    `services_total`; a judged service may be too quiet to judge this minute.
    `services_muted` overlaps them: a muted service is still judged.
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
    # Services past the cap, which are not judged, and the cap: a project judges at
    # most `judged_service_limit` services, its busiest.
    services_too_many: int
    judged_service_limit: int


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


class QuietServiceRequest(BaseModel):
    """Snooze or mute a service until a time, or end it now with `until` null."""

    project_id: uuid.UUID
    # Aware, so "until 18:00" never means a different instant on another machine.
    until: AwareDatetime | None


class ServiceControls(BaseModel):
    """What is quietening a service now, if anything."""

    muted_until: datetime | None
    snoozed_until: datetime | None


Sensitivity = Literal["low", "normal", "high"]


class DetectionSettings(BaseModel):
    """A project's detection settings: its sensitivity and the IANA zone its normal is
    read in (FR18, FR24)."""

    sensitivity: Sensitivity
    timezone: str


class DetectionSettingsWrite(BaseModel):
    """Both, replaced together, as the settings form saves them."""

    sensitivity: Sensitivity
    # Bounded here and judged by `profile.timezones`, as a person's own zone is.
    timezone: str = Field(max_length=TIMEZONE_NAME_LENGTH)
