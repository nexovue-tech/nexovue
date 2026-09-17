"""Secrets are written and listed by name; their values are never read back (FR20)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final

from nexovue_platform.checks.templating import SECRET_NAME_PATTERN, VARIABLE_NAME_PATTERN
from pydantic import BaseModel, Field

# Long enough for a signed token or a PEM-less key, short enough to bound what a
# task carries. A secret shorter than this minimum would redact unrelated text
# everywhere it appears in evidence.
MIN_SECRET_LENGTH: Final = 4
MAX_SECRET_LENGTH: Final = 8192
MAX_VARIABLE_LENGTH: Final = 8192
MAX_SECRETS_PER_PROJECT: Final = 100
MAX_VARIABLES_PER_PROJECT: Final = 100

SecretName = Annotated[str, Field(pattern=SECRET_NAME_PATTERN.pattern)]
VariableName = Annotated[str, Field(pattern=VARIABLE_NAME_PATTERN.pattern)]


class CheckSecretSummary(BaseModel):
    name: str
    created_at: datetime
    updated_at: datetime
    # How many of the project's checks name this secret: what deleting it breaks.
    used_by_checks: int = 0


class CheckSecretListResponse(BaseModel):
    secrets: list[CheckSecretSummary]


class WriteSecretRequest(BaseModel):
    value: Annotated[str, Field(min_length=MIN_SECRET_LENGTH, max_length=MAX_SECRET_LENGTH)]


class CheckVariableSummary(BaseModel):
    name: str
    value: str
    updated_at: datetime
    used_by_checks: int = 0


class CheckVariableListResponse(BaseModel):
    variables: list[CheckVariableSummary]


class WriteVariableRequest(BaseModel):
    value: Annotated[str, Field(max_length=MAX_VARIABLE_LENGTH)]
