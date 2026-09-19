"""The audit log screen's shapes (F26, FR36)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel

from nexovue_api.wire import IsoTimestamp

# What the screen filters by. Each is a set of action prefixes (`queries.KINDS`).
EventKind = Literal["people", "credentials", "sign_in", "configuration", "support"]


class AuditActor(BaseModel):
    """Who did it: a person in the organization, nexovue staff, or nobody named."""

    kind: Literal["person", "staff", "system"]
    id: uuid.UUID | None
    # A name to show. A removed person keeps theirs (BR6).
    name: str | None


class AuditEntry(BaseModel):
    id: uuid.UUID
    at: IsoTimestamp
    actor: AuditActor
    action: str
    # `<kind>:<id>`, as written; `target_name` names a person when it is one.
    target: str
    target_name: str | None
    detail: dict[str, Any] | None
    # Set on everything nexovue support did while viewing as someone (F31).
    impersonation_context: uuid.UUID | None


class AuditPage(BaseModel):
    entries: list[AuditEntry]
    next_cursor: str | None
