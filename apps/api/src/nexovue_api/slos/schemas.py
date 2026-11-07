"""What `/slos` sends and takes (F19 design, *API*).

A token's responses carry no person: no `created_by`, `deleted_by` or actor id, and
the name of whoever deleted a target only for a person (AC33, F6's m2).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from nexovue_platform import slo
from pydantic import BaseModel, Field, StringConstraints

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=slo.NAME_LONGEST)
]
Description = Annotated[str, StringConstraints(max_length=slo.DESCRIPTION_LONGEST)]
ServiceName = Annotated[str, StringConstraints(min_length=1, max_length=slo.SERVICE_NAME_LONGEST)]
Objective = Annotated[Decimal, Field(max_digits=7, decimal_places=4)]
WindowDays = Literal[7, 28]
Sampling = Literal["no", "yes", "not_sure"]
# Step 1 creates service availability only (plan T1.7); Step 2 widens this.
CreatableKind = Literal["service_availability"]


class SloCreate(BaseModel):
    kind: CreatableKind = "service_availability"
    name: Name
    description: Description = ""
    service_name: ServiceName
    objective_percent: Objective
    window_days: WindowDays = slo.DEFAULT_WINDOW_DAYS
    upstream_sampling: Sampling


class SloChange(BaseModel):
    """Only what is sent changes. The kind never changes (FR28)."""

    name: Name | None = None
    description: Description | None = None
    service_name: ServiceName | None = None
    objective_percent: Objective | None = None
    window_days: WindowDays | None = None
    upstream_sampling: Sampling | None = None
    enabled: bool | None = None


class SloRestore(BaseModel):
    """A new name, when the old one was taken while the target was deleted (AC116)."""

    name: Name | None = None


class SloFigures(BaseModel):
    """The window's figures, from stored days (FR9, FR11, FR12)."""

    good: int
    total: int
    failures: int
    allowed: int
    # Never below zero on screen (FR12); `missed_by` says how far past it went.
    left: int
    missed_by: int
    figure_percent: Decimal | None
    coverage: float | None
    lost_minutes: int
    excluded_minutes: int
    days_held: int


class SloView(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str
    kind: str
    service_name: str | None
    objective_percent: Decimal
    window_days: int
    upstream_sampling: str | None
    enabled: bool
    # Composed by nexovue (D43): what a message would call this target.
    label: str
    allowance_words: str
    state: slo.State
    variant: slo.Variant
    qualifiers: list[slo.Qualifier]
    attention: bool
    severity: Literal["critical", "warning"] | None
    hours_left: float | None
    quiet_minutes: int | None
    figures: SloFigures
    evaluated_minute: datetime | None
    last_event_minute: datetime | None
    longest_quiet_7d_minutes: int
    # The project's zone, which every time on the page shows (FR13).
    zone: str
    created_at: datetime
    updated_at: datetime


class DeletedSlo(BaseModel):
    """FR31's line on the list for 30 days after a deletion."""

    id: uuid.UUID
    name: str
    label: str
    deleted_at: datetime
    # The day its daily figures go, and it can no longer be restored.
    purged_on: date
    days_held: int
    # A person's name, for a person only; None for a token or a former member.
    deleted_by_name: str | None


class SloList(BaseModel):
    """In FR10a's order: the counted rows first, so the top N are the headline's N."""

    targets: list[SloView]
    deleted: list[DeletedSlo]
    zone: str
    # The project's sources, so an empty list can say "Nothing is reporting yet" (AC41).
    has_services: bool
