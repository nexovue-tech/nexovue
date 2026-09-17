"""Maintenance window shapes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Final

from nexovue_platform.checks.maintenance import Recurrence
from pydantic import AwareDatetime, BaseModel, Field

MAX_WINDOWS_PER_PROJECT: Final = 50

WindowName = Annotated[str, Field(min_length=1, max_length=120)]


class MaintenanceWindowSummary(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    starts_at: datetime
    ends_at: datetime
    recurrence: Recurrence
    repeat_until: datetime | None
    applies_to_all: bool
    check_ids: list[uuid.UUID]
    active_now: bool
    created_at: datetime


class MaintenanceWindowListResponse(BaseModel):
    windows: list[MaintenanceWindowSummary]


class WriteMaintenanceWindowRequest(BaseModel):
    """The whole window; an edit replaces it, including which checks it covers."""

    project_id: uuid.UUID
    name: WindowName
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    recurrence: Recurrence = "none"
    repeat_until: AwareDatetime | None = None
    applies_to_all: bool = False
    check_ids: list[uuid.UUID] = []
