"""What signup accepts and what it hands back."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from nexovue_api.auth.schemas import EmailAddress, OrganizationSummary, Password
from nexovue_api.data.postgres.models import NAME_MAX_LENGTH
from nexovue_api.projects.schemas import ProjectSummary

OrganizationName = Annotated[str, Field(min_length=1, max_length=NAME_MAX_LENGTH)]
PersonName = Annotated[str, Field(min_length=1, max_length=NAME_MAX_LENGTH)]


class SignupRequest(BaseModel):
    organization_name: OrganizationName
    email: EmailAddress
    password: Password
    # Optional because `users.name` is nullable: an account can exist before
    # anyone has said what to call its owner.
    name: PersonName | None = None


class SignupResponse(BaseModel):
    """Everything a new customer needs to start sending telemetry.

    `api_key` is the only time the plaintext key exists outside the caller's
    request: what is stored is a hash, so this response cannot be reissued.
    """

    organization: OrganizationSummary
    project: ProjectSummary
    api_key: str
    otlp_endpoint: str
