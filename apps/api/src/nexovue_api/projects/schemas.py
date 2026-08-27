"""The project shape every screen's selector reads."""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class ProjectSummary(BaseModel):
    id: uuid.UUID
    slug: str
    name: str


class ProjectListResponse(BaseModel):
    projects: list[ProjectSummary]
