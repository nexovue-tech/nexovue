"""The import wire contract: a preview that writes nothing, and a confirm that writes it all.

The preview's plan is the confirm's input once the console has filled its gaps,
so both speak `CheckConfig` — the same model a save validates against (FR11).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final, Literal

from nexovue_platform.checks.config import MAX_URL_LENGTH, CheckConfig
from pydantic import BaseModel, Field

from nexovue_api.checks.schemas import MIN_INTERVAL_SECONDS, CheckName, Interval
from nexovue_api.checksecrets.schemas import (
    MAX_SECRET_LENGTH,
    MAX_SECRETS_PER_PROJECT,
    MAX_VARIABLE_LENGTH,
    MAX_VARIABLES_PER_PROJECT,
    MIN_SECRET_LENGTH,
    SecretName,
    VariableName,
)

# FR15. Five mebibytes holds a 500-operation description with room to spare.
MAX_IMPORT_CHARACTERS: Final = 5 * 1024 * 1024
# NFR5: the preview is sized and timed for this many operations or requests.
MAX_IMPORTED_CHECKS: Final = 500

ImportSource = Literal["curl", "openapi", "postman"]
# A Postman folder becomes one multistep check, or one check per request (FR14).
PostmanFolderMode = Literal["multistep", "separate"]

ImportText = Annotated[str, Field(min_length=1, max_length=MAX_IMPORT_CHARACTERS)]


class ImportPreviewRequest(BaseModel):
    project_id: uuid.UUID
    source: ImportSource
    content: ImportText
    # A Postman environment export, whose values fill the collection's variables.
    environment: ImportText | None = None
    # The OpenAPI server the checks should call; the first one when absent.
    server_url: Annotated[str, Field(max_length=MAX_URL_LENGTH)] | None = None
    postman_folders: PostmanFolderMode = "separate"


class DetectedSecret(BaseModel):
    """A credential the import found, or a scheme that needs one. Empty when the
    description declared the need without the value."""

    name: str
    value: str
    found_in: str


class DetectedVariable(BaseModel):
    name: str
    value: str


class SkippedItem(BaseModel):
    """Something the import read and did not carry over, with why (EC5, D16)."""

    item: str
    reason: str


class ImportItem(BaseModel):
    key: str
    name: str
    method: str
    url: str
    config: CheckConfig
    needs_values: list[str] = []
    needs_files: list[str] = []
    selected: bool = True


class ImportPlan(BaseModel):
    source: ImportSource
    title: str
    servers: list[str] = []
    items: list[ImportItem] = []
    secrets: list[DetectedSecret] = []
    variables: list[DetectedVariable] = []
    skipped: list[SkippedItem] = []


class ImportedCheck(BaseModel):
    name: CheckName
    config: CheckConfig
    interval_seconds: Interval = MIN_INTERVAL_SECONDS
    # Empty runs the check from every location this deployment has.
    locations: list[str] = []


class ImportedSecret(BaseModel):
    name: SecretName
    value: Annotated[str, Field(min_length=MIN_SECRET_LENGTH, max_length=MAX_SECRET_LENGTH)]


class ImportedVariable(BaseModel):
    name: VariableName
    value: Annotated[str, Field(max_length=MAX_VARIABLE_LENGTH)] = ""


class ImportConfirmRequest(BaseModel):
    project_id: uuid.UUID
    checks: Annotated[list[ImportedCheck], Field(min_length=1, max_length=MAX_IMPORTED_CHECKS)]
    secrets: Annotated[list[ImportedSecret], Field(max_length=MAX_SECRETS_PER_PROJECT)] = []
    variables: Annotated[list[ImportedVariable], Field(max_length=MAX_VARIABLES_PER_PROJECT)] = []


class ImportConfirmed(BaseModel):
    created: list[uuid.UUID]
