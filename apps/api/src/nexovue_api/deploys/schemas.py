"""The deploy shapes: what CI signs, and what the console reads back."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Final

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints

from nexovue_api.data.postgres.models import NAME_MAX_LENGTH, SHORT_LABEL_LENGTH

# What produced the notification. Free text, bounded by the column, because the
# set of CI systems is not ours to enumerate.
DEFAULT_SOURCE: Final = "webhook"

DEFAULT_PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 200

RequiredName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH)
]
SourceLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SHORT_LABEL_LENGTH)
]


class WebhookDeployRequest(BaseModel):
    """The signed body of a deploy notification.

    Our own envelope rather than GitHub's native ``deployment`` payload: that
    payload names a repository and an environment, and nothing in it answers
    "which service, at which version", which is the whole of what a deploy has
    to say to be correlatable later. Unknown keys are ignored so a CI job may
    post a superset of this without being refused.
    """

    version: RequiredName
    service_name: RequiredName
    # Absent means the notification arrived as the deploy finished, which is the
    # normal case; the service stamps the time it was received.
    deployed_at: AwareDatetime | None = None
    source: SourceLabel = DEFAULT_SOURCE
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeployEventSummary(BaseModel):
    """One recorded deploy, as the console lists it."""

    id: uuid.UUID
    project_id: uuid.UUID
    version: str
    service_name: str
    deployed_at: datetime
    source: str
    metadata: dict[str, Any]


class DeployEventListResponse(BaseModel):
    deploys: list[DeployEventSummary]


class WebhookSecret(BaseModel):
    """One live secret. Its own row, so it can be rotated and revoked without
    touching the ingest keys."""

    id: uuid.UUID
    secret: str
    created_at: datetime


class WebhookSecretsResponse(BaseModel):
    """Everything needed to configure the webhook at the other end."""

    project_id: uuid.UUID
    webhook_path: str
    signature_header: str
    secrets: list[WebhookSecret]
