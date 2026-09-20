"""Alert rule and incident shapes (F21, FR5, FR9, FR22, FR24)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

RuleName = Annotated[str, Field(min_length=1, max_length=120)]
# Never more often than every 30 minutes; at most 5 repeats per incident (FR10).
RenotifyMinutes = Annotated[int, Field(ge=30, le=24 * 60)]


class RuleChannel(BaseModel):
    id: uuid.UUID
    kind: str
    name: str


class RuleSummary(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    condition_kind: str
    condition: dict[str, Any]
    channels: list[RuleChannel]
    renotify_minutes: int | None
    enabled: bool
    muted_until: datetime | None
    muted_by: uuid.UUID | None
    is_default: bool
    # Every channel it sent to was deleted (FR25): shown, never silently kept.
    notifying_nobody: bool
    # It names checks and every one of them is gone.
    covers_nothing: bool
    created_at: datetime
    updated_at: datetime


class RuleListResponse(BaseModel):
    rules: list[RuleSummary]


class WriteRuleRequest(BaseModel):
    project_id: uuid.UUID
    name: RuleName
    # Validated against `nexovue_platform.alerts.model` for its kind.
    condition: dict[str, Any]
    channel_ids: list[uuid.UUID]
    renotify_minutes: RenotifyMinutes | None = None
    enabled: bool = True


class UpdateRuleRequest(BaseModel):
    """A partial edit. The project a rule belongs to does not change."""

    name: RuleName | None = None
    condition: dict[str, Any] | None = None
    channel_ids: list[uuid.UUID] | None = None
    renotify_minutes: RenotifyMinutes | None = None
    clear_renotify: bool = False
    enabled: bool | None = None


class MuteRuleRequest(BaseModel):
    until: AwareDatetime | None = None


class IncidentNotification(BaseModel):
    id: uuid.UUID
    channel_id: uuid.UUID
    channel_name: str
    channel_kind: str
    kind: str
    state: str
    reason: str | None
    delivered_at: datetime | None


class IncidentSummary(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    rule_id: uuid.UUID | None
    rule_name: str
    condition_kind: str
    subject_key: str
    subject_label: str
    severity: Literal["critical", "warning"]
    opened_at: datetime
    resolved_at: datetime | None
    is_event: bool
    flapping: bool
    muted: bool
    detail: dict[str, Any]
    # True when the incident was about a check that has since been deleted: its page
    # says so instead of linking to "No such check".
    check_deleted: bool
    # Which channels were told, and how that went.
    notifications: list[IncidentNotification]


class OpenIncidentCount(BaseModel):
    """What the sidebar shows beside Alerts: how many are open, and whether any is critical."""

    open: int
    critical: int


class IncidentListResponse(BaseModel):
    open: list[IncidentSummary]
    recent: list[IncidentSummary]
    # F36, AC20: a capped list says when the cap bit, so a caller reading through
    # the API knows the answer is partial rather than assuming it is complete.
    truncated: bool = False


class SweepStatus(BaseModel):
    name: Literal["thresholds", "silence", "new_errors"]
    finished_at: datetime | None
    # Why it did not judge, when it did not: no telemetry store, a meter behind.
    skipped: str | None
    # Not finished within three minutes, or skipped: what it would say is not current.
    behind: bool


class EvaluationStatus(BaseModel):
    sweeps: list[SweepStatus]


class CoverageResponse(BaseModel):
    """What watches one subject — a check or a service (FR24)."""

    rules: list[RuleSummary]
    open_incidents: list[IncidentSummary]
