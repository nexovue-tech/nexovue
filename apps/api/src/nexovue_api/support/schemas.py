"""Support access's shapes."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel

from nexovue_api.wire import IsoTimestamp

# FR28: an owner chooses how long nexovue support may view as their people.
GrantHours = Literal[24, 72, 168]


class GrantSupportAccess(BaseModel):
    hours: GrantHours


class SupportView(BaseModel):
    """One *view as* session, from the audit log."""

    at: IsoTimestamp
    staff: str
    viewed: str | None
    reason: str | None
    impersonation_id: uuid.UUID | None


class SupportAccessState(BaseModel):
    # Null when support may not view; the moment it lapses otherwise.
    granted_until: IsoTimestamp | None
    views: list[SupportView]
