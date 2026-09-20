"""The check shapes the console reads and writes.

The configuration itself is `nexovue_platform.checks.config` — one definition
the scheduler parses too — so what is accepted here is exactly what can run.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Final, Literal

from nexovue_platform.checks.config import CheckConfig, CheckType
from nexovue_platform.checks.evidence import RunEvidence
from pydantic import BaseModel, Field

# Bounded per project because each check is a request we make on a timer,
# forever, at our own cost. Not yet tied to the plan tier — that belongs with
# billing, and a constant that is too generous is safer than no ceiling at all.
MAX_CHECKS_PER_PROJECT: Final = 100

# One a minute is the floor (spec D5). For a heartbeat this is the period a call
# is expected within.
MIN_INTERVAL_SECONDS: Final = 60
MAX_INTERVAL_SECONDS: Final = 86_400

MIN_RETRIES: Final = 1
MAX_RETRIES: Final = 3
DEFAULT_RETRIES: Final = 1

MAX_NAME_LENGTH: Final = 120
MAX_THRESHOLD_MS: Final = 600_000
MAX_STATE_CHANGES: Final = 200

Interval = Annotated[int, Field(ge=MIN_INTERVAL_SECONDS, le=MAX_INTERVAL_SECONDS)]
Retries = Annotated[int, Field(ge=MIN_RETRIES, le=MAX_RETRIES)]
Threshold = Annotated[int, Field(gt=0, le=MAX_THRESHOLD_MS)]
CheckName = Annotated[str, Field(min_length=1, max_length=MAX_NAME_LENGTH)]

# What the console shows. `paused` and `maintenance` are read off the check and
# its windows; the other four were confirmed by a run. None: never run.
DisplayedState = Literal["up", "degraded", "down", "misconfigured", "paused", "maintenance"]


class CheckSummary(BaseModel):
    """One check, as the list and the check page both show it."""

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    type: CheckType
    config: CheckConfig
    interval_seconds: int
    locations: list[str]
    degraded_ms: int | None
    failing_ms: int | None
    retries: int
    enabled: bool
    # The confirmed state, before paused and maintenance are applied.
    confirmed_state: str | None
    state_since: datetime | None
    last_run_at: datetime | None
    in_maintenance: bool
    displayed_state: DisplayedState | None
    last_ping_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CheckListResponse(BaseModel):
    checks: list[CheckSummary]
    # Where a check can run from on this deployment.
    available_locations: list[str]
    # F36, AC20: a capped list says when the cap bit, so a caller reading through
    # the API knows the answer is partial rather than assuming it is complete.
    truncated: bool = False


class CreatedCheck(CheckSummary):
    # A heartbeat's token, shown this once. Only its hash is kept.
    heartbeat_token: str | None = None


class CreateCheckRequest(BaseModel):
    project_id: uuid.UUID
    name: CheckName
    config: CheckConfig
    interval_seconds: Interval = MIN_INTERVAL_SECONDS
    # Empty runs the check from every location this deployment has.
    locations: list[str] = []
    degraded_ms: Threshold | None = None
    failing_ms: Threshold | None = None
    retries: Retries = DEFAULT_RETRIES
    enabled: bool = True


class UpdateCheckRequest(BaseModel):
    """A partial edit. Omitted fields are left as they are; a threshold sent as
    null is cleared. The kind of a check cannot change — delete and recreate."""

    name: CheckName | None = None
    config: CheckConfig | None = None
    interval_seconds: Interval | None = None
    locations: list[str] | None = None
    degraded_ms: Threshold | None = None
    failing_ms: Threshold | None = None
    retries: Retries | None = None
    enabled: bool | None = None


class HeartbeatToken(BaseModel):
    token: str
    # Relative to wherever the API is served; the console prefixes its origin.
    ping_path: str


class DeletionImpact(BaseModel):
    check_id: uuid.UUID
    files: int


class CheckStateChangeItem(BaseModel):
    from_state: str | None
    to_state: str
    changed_at: datetime
    run_id: str | None
    location: str | None
    reason: str | None


class CheckStateChangeListResponse(BaseModel):
    check_id: uuid.UUID
    changes: list[CheckStateChangeItem]


class RunNowRequest(BaseModel):
    """Run a definition once, saved or not. Nothing is recorded (FR30)."""

    project_id: uuid.UUID
    name: CheckName = "Run now"
    config: CheckConfig
    degraded_ms: Threshold | None = None
    failing_ms: Threshold | None = None
    location: str | None = None


class RunNowAccepted(BaseModel):
    request_id: str
    location: str


class RunNowResult(BaseModel):
    request_id: str
    status: Literal["pending", "complete"]
    evidence: RunEvidence | None = None
