"""The wire contract of `/releases` (F28 design, *Routes*).

No sender text crosses here: `metadata`, `url` and `source` are served by
`sender-details` alone, from Step 4 (FR54, AC76). Names are telemetry text, shown as
plain text (BR10).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# How the record knows of a release (BR3): told by a sender, counted in traffic, or both.
Basis = Literal["reported", "seen", "both"]
# Spec, *A release's activity*.
Activity = Literal["not_seen", "serving", "replaced", "idle"]


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class ReleaseRow(_Out):
    """One release in the list, by its newest period in the window."""

    id: uuid.UUID
    period_id: uuid.UUID
    service_name: str
    version: str
    started_at: datetime
    ended_at: datetime | None
    start_basis: Literal["seen", "reported"]
    basis: Basis
    activity: Activity
    already_running: bool
    is_return: bool
    last_seen_at: datetime | None
    last_state: str | None


class LatestRelease(_Out):
    """The newest release outside an empty window, for its sentence (AC27)."""

    id: uuid.UUID
    service_name: str
    version: str
    started_at: datetime


class ReleaseList(_Out):
    releases: list[ReleaseRow]
    # The window's whole count, not the page's: "12 releases in this window on 4 services".
    total: int
    services: int
    next_cursor: str | None
    # Whether the project holds any release at all, so an empty window and an empty
    # project read differently (AC27).
    has_any: bool
    latest: LatestRelease | None
    window_from: datetime
    window_to: datetime


class PeriodView(_Out):
    id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None
    start_basis: Literal["seen", "reported"]
    previous_version: str | None
    already_running: bool
    is_return: bool


class ReleaseView(_Out):
    id: uuid.UUID
    project_id: uuid.UUID
    service_name: str
    version: str
    basis: Basis
    activity: Activity
    replaced_by: str | None
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    first_reported_at: datetime | None
    last_reported_at: datetime | None
    report_count: int
    last_state: str | None
    periods: list[PeriodView]


class SideView(_Out):
    version: str
    requests: int
    errors: int
    p95_ms: float | None


class ReadingView(_Out):
    reading: Literal["higher", "lower", "about_same", "not_clearly_different"] | None
    # Why a cell has no reading.
    note: Literal["too_little_traffic", "warm_up"] | None = None


class BlockView(_Out):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    kind: Literal["rollout", "after_rollout", "around_deploy"]
    method: Literal["side_by_side", "version_before_after", "time_before_after"]
    start: datetime = Field(serialization_alias="from")
    end: datetime = Field(serialization_alias="to")
    minutes_compared: int
    this_version: SideView
    previous_version: SideView
    failure_share: ReadingView
    p95: ReadingView


class TrafficView(_Out):
    """Too little traffic: the counts of the largest block tried (AC31)."""

    this_version: SideView
    previous_version: SideView


class ComparisonView(_Out):
    period_id: uuid.UUID
    version: str
    previous_version: str | None
    # One of FR21's reasons, or null when blocks follow.
    reason: str | None
    lead: Literal["rollout", "after_rollout", "around_deploy"] | None
    # Set when no minute is settled yet, and the read ends a little before now.
    as_of: datetime | None
    # Where the comparison stops, exclusive: never past the settled minute (AC35).
    compared_to: datetime
    window_hours: int
    blocks: list[BlockView]
    traffic: TrafficView | None
    # A version never seen: what the service sends instead, busiest first.
    other_versions: list[str]
    # The project's limit on version numbers, for "kept 30 days on this project".
    numbers_kept_days: int
