"""The check shapes the UI reads and writes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, Field

# The prober dispatches on this. `browser` is accepted here before the browser
# queue runs it, so a customer can define one and it starts working when that
# ships rather than needing a second migration of their data.
CheckType = Literal["http", "browser"]

# One a minute is the floor. Below that the cost to us and to the customer's
# server stops being proportionate to what a slower interval would miss.
# One browser worker serves every tenant at concurrency 1, so an unbounded flow
# is an unbounded share of a queue everybody else is waiting in.
MAX_STEPS_PER_FLOW: Final = 20

# Bounded per project because each check is a request we make on a timer,
# forever, at our own cost. Not yet tied to the plan tier — that belongs with
# billing, and a constant that is too generous is safer than no ceiling at all.
MAX_CHECKS_PER_PROJECT: Final = 100

MIN_INTERVAL_SECONDS: Final = 60
MAX_INTERVAL_SECONDS: Final = 86_400


class CheckSummary(BaseModel):
    """One check, as the list and the detail view both show it."""

    id: uuid.UUID
    project_id: uuid.UUID
    type: CheckType
    target: str
    interval_seconds: int
    enabled: bool
    config: dict[str, Any]
    created_at: datetime


class CheckListResponse(BaseModel):
    checks: list[CheckSummary]


class CreateCheckRequest(BaseModel):
    """A new check. The target is validated in the service, not here.

    Pydantic would reject a bad URL with its own message and a 422; routing it
    through `targets.validate_target` instead means one explanation of why a
    target was refused, in the problem+json shape every other error uses.
    """

    project_id: uuid.UUID
    type: CheckType = "http"
    target: str
    interval_seconds: Annotated[int, Field(ge=MIN_INTERVAL_SECONDS, le=MAX_INTERVAL_SECONDS)] = (
        MIN_INTERVAL_SECONDS
    )
    config: dict[str, Any] = Field(default_factory=dict)


class UpdateCheckRequest(BaseModel):
    """A partial edit. Omitted fields are left as they are.

    `enabled` is the field that matters most operationally: pausing a check is
    how a customer stops alerts during their own maintenance, and it must not
    require deleting and recreating one.
    """

    target: str | None = None
    interval_seconds: (
        Annotated[int, Field(ge=MIN_INTERVAL_SECONDS, le=MAX_INTERVAL_SECONDS)] | None
    ) = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None
