"""The wire contract of `/releases` (F28 design, *Routes*).

No sender text crosses here but `SenderDetails`: `metadata`, `url` and `source` are
served by `sender-details` alone, to a signed-in person (FR54, AC76). Names are
telemetry text, shown as plain text (BR10).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nexovue_api.deploys.schemas import DeployState

# How the record knows of a release (BR3): told by a sender, counted in traffic, or both.
Basis = Literal["reported", "seen", "both"]
# Spec, *A release's activity*.
Activity = Literal["not_seen", "serving", "replaced", "idle"]
# Spec, *A service's coverage* (FR38).
CoverageValue = Literal["deploys_and_versions", "deploys_only", "versions_only", "none"]


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class StoredFlag(_Out):
    """ "Failure share higher" or "p95 higher", with the counts behind it (FR55)."""

    measure: Literal["failure_share", "p95"]
    this_requests: int
    this_errors: int
    this_p95_ms: float | None
    previous_requests: int
    previous_errors: int
    previous_p95_ms: float | None


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
    # FR55, from the reading a job stored 30 minutes in, never a ClickHouse read here
    # (AC81): each measure a version method read as higher, with its counts.
    flags: list[StoredFlag]
    # FR17, from the same reading: the worker combined versions in a compared minute.
    versions_combined: bool


class LatestRelease(_Out):
    """The newest release outside an empty window, for its sentence (AC27)."""

    id: uuid.UUID
    service_name: str
    version: str
    started_at: datetime


class NamingDiffers(_Out):
    """FR46: a service whose CI and traffic name versions differently, with the newest
    of each, in place of the "never seen" words on its rows."""

    service_name: str
    reported_version: str
    seen_version: str


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
    # FR46, over the last 24 hours.
    naming_differs: list[NamingDiffers]


class LaterFinding(_Out):
    """A finding on the service that started during a period (FR34): order, not cause."""

    id: uuid.UUID
    title: str
    severity: str
    state: str
    started_at: datetime


class PeriodView(_Out):
    id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None
    start_basis: Literal["seen", "reported"]
    previous_version: str | None
    already_running: bool
    is_return: bool
    # "Started after this release", oldest first, up to `LATER_FINDINGS_CAP`.
    later_findings: list[LaterFinding]
    later_findings_truncated: bool


class DeployFactView(_Out):
    """One reported deploy of the release, facts only (design, *Routes*). The sender's
    time beside our arrival time, so a gap between them shows (UR13)."""

    id: uuid.UUID
    deployed_at: datetime
    received_at: datetime
    state: DeployState
    commit_sha: str | None


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
    # Oldest claim first.
    deploys: list[DeployFactView]


class SideView(_Out):
    version: str
    requests: int
    errors: int
    p95_ms: float | None
    # Requests over the minutes that had any, not the whole stretch (item 36b).
    requests_per_minute: float | None


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
    # The previous side's stretch: the same minutes side by side, before otherwise.
    previous_from: datetime
    previous_to: datetime
    minutes_compared: int
    this_version: SideView
    previous_version: SideView
    failure_share: ReadingView
    p95: ReadingView
    # FR25: the whole service over this block's stretch a week earlier; before-and-after
    # methods only, and null where those numbers are gone.
    last_week: SideView | None = None


class FindingWindowView(_Out):
    """FR22: the finding the comparison was opened from, and whether its window
    reaches the finding's start."""

    id: uuid.UUID
    started_at: datetime
    reaches: bool


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
    # FR28: compared minutes whose version rows do not sum to the service's.
    incomplete: bool
    minutes_differing: int
    # FR17: the worker folded versions in a compared minute, so this is inexact.
    versions_combined: bool
    # FR45: more than one environment label in the service's stored spans.
    mixed_environments: bool
    # Set when opened from a finding of this project that started after the period.
    finding: FindingWindowView | None


class Marker(_Out):
    """One mark on a chart (design, *Release markers on charts*): a deploy a sender
    reported, or the start of a period counted in traffic. Exactly these fields, and no
    sender text (AC45): F20's dashboards read the same."""

    release_id: uuid.UUID | None
    period_id: uuid.UUID | None
    deploy_id: uuid.UUID | None
    service_name: str
    version: str
    at: datetime
    basis: Literal["reported", "seen"]
    state: DeployState | None


class MarkerList(_Out):
    markers: list[Marker]
    # More marks than `cap` fell in the window; these are the newest (FR35).
    truncated: bool
    cap: int


class ServiceCoverage(_Out):
    service_name: str
    coverage: CoverageValue
    # The last report at or before `at`, whatever its age (AC73).
    last_reported_at: datetime | None
    # Only when nothing was heard by `at` and something was afterwards.
    first_heard_at: datetime | None


class CoverageList(_Out):
    at: datetime
    services: list[ServiceCoverage]


ErrorKind = Literal["new_here", "gone_here", "in_both", "this_version"]


class VersionErrorGroup(_Out):
    """One error group of the two versions (FR29). `this_version` when there is no
    previous version to hold it against."""

    group_id: str
    exception_type: str
    message_template: str
    kind: ErrorKind
    this_occurrences: int
    previous_occurrences: int
    # Some occurrences came through the browser door (FR27).
    from_browser: bool


class VersionErrors(_Out):
    """Errors by version: new here, gone here, in both, or why there is no list."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    period_id: uuid.UUID
    version: str
    previous_version: str | None
    start: datetime = Field(serialization_alias="from")
    end: datetime = Field(serialization_alias="to")
    window_hours: int
    groups: list[VersionErrorGroup]
    # More groups than the cap of 100 fell in the window.
    truncated: bool
    # FR51: the read took too long, which is never an empty list.
    timed_out: bool
    # FR30: raw rows this read needed had aged out, on this date.
    aged_out_on: datetime | None
    # FR31: the project's sampling keeps errors off, so the list may be incomplete.
    may_be_incomplete: bool
    # A release with no server traffic reads the browser door too (FR27).
    browser_door_read: bool


RolloutSeriesKind = Literal["version", "no_version", "combined", "other_versions"]


class RolloutSeries(_Out):
    """One line of the share chart (FR26): a version, or one of three labels."""

    kind: RolloutSeriesKind
    version: str | None


class RolloutMinute(_Out):
    minute: datetime
    # Requests per series, in `series` order.
    requests: list[int]


class Rollout(_Out):
    """Each version's requests per minute (FR26): the top four, then the rest."""

    period_id: uuid.UUID
    series: list[RolloutSeries]
    minutes: list[RolloutMinute]
    as_of: datetime | None
    compared_to: datetime


class SenderDeploy(_Out):
    """What a sender wrote beside one deploy (FR54): shown to a person, as plain text.
    `metadata` is null for a viewer, with `metadata_withheld` saying so."""

    deploy_id: uuid.UUID
    deployed_at: datetime
    url: str | None
    source: str
    metadata: dict[str, str] | None
    metadata_withheld: bool


class SenderDetails(_Out):
    deploys: list[SenderDeploy]
