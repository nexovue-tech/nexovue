"""Runbooks' request and response bodies (F4 design, *Routes*; FR32).

Lengths are checked by the service, not here, so every refusal is nexovue's own sentence
with its own code (AC90), never a validator's.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from nexovue_platform.knowledge import MAX_RUNBOOKS_PER_PROJECT, MAX_UPLOAD_FILES
from pydantic import BaseModel, Field

# An upload names the projects it copies into; an organization has few.
MAX_UPLOAD_PROJECTS = 50


class RunbookSummary(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    service_name: str | None
    # A current member's name or email; null once they left, which reads "Former member".
    author: str | None
    created_at: datetime
    updated_at: datetime


class RunbookView(RunbookSummary):
    # Null for a support view, which sees the list and no bodies (FR32, AC92).
    content: str | None


class RunbookList(BaseModel):
    runbooks: list[RunbookSummary]
    # How many a project may hold, so the screen can say the cap is reached.
    limit: int = MAX_RUNBOOKS_PER_PROJECT


class RunbookFields(BaseModel):
    title: str
    service_name: str | None = None
    content: str
    # The second click on text that looks like a credential (FR32, AC69).
    confirm_credentials: bool = False


class NewRunbook(RunbookFields):
    project_id: uuid.UUID


class UploadedFile(BaseModel):
    name: str = Field(max_length=255)
    content: str


class RunbookUpload(BaseModel):
    """Several Markdown files, each a runbook of its own in each project named (design,
    `/settings/runbooks`): separate copies, refused or saved file by file."""

    project_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_UPLOAD_PROJECTS)
    files: list[UploadedFile] = Field(min_length=1, max_length=MAX_UPLOAD_FILES)
    service_name: str | None = None
    confirm_credentials: bool = False


class RefusedFile(BaseModel):
    name: str
    project_id: uuid.UUID
    code: str
    detail: str


class UploadResult(BaseModel):
    created: list[RunbookSummary]
    refused: list[RefusedFile]
