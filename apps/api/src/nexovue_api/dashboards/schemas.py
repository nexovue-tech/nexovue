"""The wire contract of `/dashboards` (F20, design *Routes* and *Panel context*).

The request bodies are the dashboard document itself (`definition.DashboardDocument`),
so the validator is the schema and there is no second path around it (FR7).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from nexovue_api.dashboards.definition import DashboardDocument
from nexovue_api.detection.schemas import ProjectDetectionStatus


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


class DashboardListResponse(BaseModel):
    dashboards: list[DashboardSummary]


class DashboardResponse(BaseModel):
    """One dashboard, its definition as stored, and the version a save must name (FR19)."""

    id: uuid.UUID
    title: str
    description: str
    definition: dict[str, Any]
    version: int
    owner: str | None
    updated_by: str | None
    created_at: datetime
    updated_at: datetime


class ReplaceDashboard(DashboardDocument):
    """A save over an existing dashboard: the document and the version it was based on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int


class DashboardTemplate(BaseModel):
    """A starting point (FR39): opened as an unsaved draft, never saved by opening it."""

    id: str
    name: str
    document: DashboardDocument


class DashboardTemplatesResponse(BaseModel):
    templates: list[DashboardTemplate]


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


class OpenFindings(BaseModel):
    total: int
    by_service: dict[str, int]


class DashboardContext(BaseModel):
    """The Postgres half of a dashboard page (D24): it answers whatever ClickHouse does.

    Markers, coverage and alert incidents join it when F28 has merged (plan T5.4).
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
