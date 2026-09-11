"""What the project screen sends and reads back.

The two configuration documents travel as opaque objects rather than as typed
models. Their grammar is `config.py`'s, mirrored from the Go worker, and a
pydantic model of the same shape would be a second definition free to disagree
with it — which is the drift this whole feature exists to prevent.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Final

from pydantic import BaseModel, Field

from nexovue_api.data.postgres.models import NAME_MAX_LENGTH, SLUG_MAX_LENGTH
from nexovue_api.wire import IsoTimestamp

# Lowercase words joined by single hyphens. The same shape `signup.slugs`
# produces, stated here because a slug the customer typed is not one we built.
SLUG_PATTERN: Final = r"^[a-z0-9]+(-[a-z0-9]+)*$"

ProjectName = Annotated[str, Field(min_length=1, max_length=NAME_MAX_LENGTH)]
ProjectSlug = Annotated[str, Field(min_length=1, max_length=SLUG_MAX_LENGTH, pattern=SLUG_PATTERN)]

# Said on every masking write, because the screen must not imply that turning a
# rule on cleans up history. Masking runs before anything downstream
# sees a value, so a value already stored was stored as it arrived.
MASKING_NOTICE: Final = (
    "Masking applies to telemetry received from now on. Telemetry already stored keeps "
    "the values it was stored with; there is no way to mask it afterwards."
)

# Said on every deletion, for the same reason: it is the honest answer to "is my
# data gone", and it is no.
DELETION_TELEMETRY_NOTICE: Final = (
    "Telemetry already stored for this project is not deleted. It stops being readable in "
    "the product and expires with the project's retention window."
)


class ProjectSummary(BaseModel):
    """The selector's shape: enough to name a project and nothing else."""

    id: uuid.UUID
    slug: str
    name: str


class ProjectListResponse(BaseModel):
    projects: list[ProjectSummary]


class ProjectDetail(BaseModel):
    """One project as its settings screen reads it.

    `retention_days` is here and is not writable: what a project actually gets is
    resolved against the plan's ceiling elsewhere, and a writable control is a
    decision §9 has not taken.
    """

    id: uuid.UUID
    slug: str
    name: str
    retention_days: int
    sampling_config: dict[str, Any]
    masking_config: dict[str, Any]
    created_at: IsoTimestamp


class ProjectCreateRequest(BaseModel):
    """A name, and optionally the identifier to go with it.

    The slug is derived from the name when it is absent, so the ordinary case is
    one field. Supplying one is for the customer who wants `prod` rather than
    `production`.
    """

    name: ProjectName
    slug: ProjectSlug | None = None


class ProjectUpdateRequest(BaseModel):
    """A rename. Both fields optional, and a body naming neither is refused —
    an empty PATCH is a request nobody meant to send."""

    name: ProjectName | None = None
    slug: ProjectSlug | None = None


class ProjectDeleted(BaseModel):
    """What the deletion closed, and what it deliberately did not.

    The counts are here because "deleted" would otherwise be the only thing said
    about an action that revokes live credentials, and because they are what a
    customer checks when they wonder why their exporter stopped.
    """

    project_id: uuid.UUID
    deleted_at: datetime
    api_keys_revoked: int
    site_keys_revoked: int
    webhook_secrets_revoked: int
    telemetry_notice: str = DELETION_TELEMETRY_NOTICE


class SamplingConfigWrite(BaseModel):
    """The whole document, replaced. The worker reads it whole, so a partial
    write here would mean two different ideas of what "the config" is."""

    config: dict[str, Any]


class SamplingConfigResponse(BaseModel):
    project_id: uuid.UUID
    config: dict[str, Any]


class MaskingConfigWrite(BaseModel):
    """The whole document, plus an acknowledgement for any rule being switched off.

    `confirm_disabling` names the shipped rules this write turns off. It is
    required only for rules that were on before — re-sending a document that
    already had `credential_keys` disabled asks for no new confirmation, because
    it widens nothing.
    """

    config: dict[str, Any]
    confirm_disabling: list[str] = Field(default_factory=list)


class MaskingConfigResponse(BaseModel):
    project_id: uuid.UUID
    config: dict[str, Any]
    notice: str = MASKING_NOTICE
