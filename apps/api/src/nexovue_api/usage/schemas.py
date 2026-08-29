"""What the usage endpoint puts on the wire."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field


class UsageCounters(BaseModel):
    """What was ingested. Four counters, in the units they are metered in.

    `bytes_ingested` rather than `bytes`: the column is `usage_daily.bytes`, but
    a field of that name shadows a builtin everywhere it is unpacked.
    """

    spans: int = 0
    logs: int = 0
    metric_points: int = 0
    bytes_ingested: int = 0


class ProjectDayUsage(UsageCounters):
    """One project's counters for one day."""

    project_id: uuid.UUID
    day: date


class PlanSummary(BaseModel):
    """The tier this organization is on, and what it grants (ADR 0071).

    Retention is here because it is the plan attribute a usage screen needs
    beside the counters: how long what was ingested will still be readable.
    There is no price — see `service`.
    """

    slug: str
    name: str
    raw_retention_days: int
    derived_retention_days: int


class UsageResponse(BaseModel):
    """Ingestion counters for one organization over a range of days.

    **There is no cost field.** `plans` holds retention and nothing about money,
    so any figure here would be a number this service invented. See `service`.
    """

    plan: PlanSummary
    from_day: date
    to_day: date
    totals: UsageCounters
    # Day order, then project. A day with no ingestion is absent rather than
    # zero-filled: nothing writes a zero row, so a gap means "no data recorded",
    # which is not the same claim as "nothing was ingested".
    days: list[ProjectDayUsage] = Field(default_factory=list)
