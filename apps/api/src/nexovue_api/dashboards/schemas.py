"""The wire contract of `/dashboards` (F20, design *Routes* and *Panel context*).

The request bodies are the dashboard document itself (`definition.DashboardDocument`),
so the validator is the schema and there is no second path around it (FR7).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nexovue_api.dashboards.definition import DashboardDocument, Title
from nexovue_api.detection.schemas import ProjectDetectionStatus
from nexovue_api.releases.schemas import Marker


class DashboardSummary(BaseModel):
    """One row of the list, from stored columns only (AC96). `owner` is None for a
    former member, which the console says as such (FR38)."""

    id: uuid.UUID
    title: str
    description: str
    owner: str | None
    panel_count: int
    uses_picker: bool
    panels_not_reading: int
    last_viewed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Set only on Recently deleted, where it is what the restore window counts from.
    deleted_at: datetime | None = None


class DashboardListResponse(BaseModel):
    dashboards: list[DashboardSummary]
    # Only `?service=` limits the list, and then more may exist than were listed (FR57).
    more: bool = False


class DashboardResponse(BaseModel):
    """One dashboard, its definition as stored, and the version a save must name (FR19)."""

    id: uuid.UUID
    title: str
    description: str
    definition: dict[str, Any]
    version: int
    owner: str | None
    updated_by: str | None
    # For delete's own sentence, "last opened 2 hours ago" (FR25).
    last_viewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ReplaceDashboard(DashboardDocument):
    """A save over an existing dashboard: the document and the version it was based on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int


class VersionSummary(BaseModel):
    """One entry of a dashboard's history (FR51): which version, who saved it, and when.
    `current` is the one now in use, which no restore is needed for."""

    version: int
    title: str
    saved_by: str | None
    saved_at: datetime
    current: bool


class VersionsResponse(BaseModel):
    versions: list[VersionSummary]


class VersionResponse(BaseModel):
    """One kept version in full, so "Restore this version" can open it as a draft."""

    version: int
    title: str
    description: str
    definition: dict[str, Any]
    saved_by: str | None
    saved_at: datetime
    current: bool


class RestoreDashboard(BaseModel):
    """Restoring a deleted dashboard, under a new title when its own is now in use (AC11)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: Title | None = None


class PurgeDashboard(BaseModel):
    """What a purge removes: the dashboard whole, or one named version of it (FR68)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int | None = Field(default=None, ge=1)


class DashboardTemplate(BaseModel):
    """A starting point (FR39): opened as an unsaved draft, never saved by opening it."""

    id: str
    name: str
    document: DashboardDocument


class DashboardTemplatesResponse(BaseModel):
    templates: list[DashboardTemplate]


class ValidatedDocument(BaseModel):
    """A file that passed every check a save makes (FR42, D53), as the validator read it,
    with what it would cost: the console opens this document as an unsaved draft."""

    document: DashboardDocument
    panel_count: int
    worst_case_reads: int


class PlanRetention(BaseModel):
    raw_days: int
    derived_days: int


class ContextProject(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str
    # Null on a self-hosted install, which has no plan allowance (D13).
    retention: PlanRetention | None


class ContextFinding(BaseModel):
    """A finding open during the window, as the feed names it (FR47)."""

    id: uuid.UUID
    scope: str
    service_name: str | None
    severity: str
    title: str
    started_at: datetime
    closed_at: datetime | None


class ContextIncident(BaseModel):
    """An alert incident open during the window whose rule names a service (FR67). Listed
    in the strip, never shaded (D52)."""

    id: uuid.UUID
    service_name: str
    rule_name: str
    severity: str
    opened_at: datetime
    resolved_at: datetime | None


class OpenFindings(BaseModel):
    total: int
    by_service: dict[str, int]


class DashboardContext(BaseModel):
    """The Postgres half of a dashboard page (D24): it answers whatever ClickHouse does.

    Markers are F28's own (`releases.service.markers_in_window`, D14), each with its
    `basis`: a `reported` deploy is signed by the customer's CI, a `seen` version came
    from telemetry, which a stolen ingest key can plant, so the console never calls it a
    deploy (AC122). `coverage` is each service's F28 coverage at the window's end, so a
    chart with no marks can say "We receive no deploy information for this service"
    rather than read as nothing shipped (AC132).
    """

    project: ContextProject
    window_start: datetime
    window_end: datetime
    detection: ProjectDetectionStatus
    ever_reported: bool
    last_seen_at: datetime | None
    findings: list[ContextFinding]
    findings_truncated: bool
    open_findings: OpenFindings
    markers: list[Marker]
    # More marks than `markers_cap` fell in the window; these are the newest (F28 FR35).
    markers_truncated: bool
    markers_cap: int
    coverage: dict[str, str]
    incidents: list[ContextIncident]
    incidents_truncated: bool
