"""What F5's setup routes take and answer: the Fix App, code locations, source consent,
the readiness counts and a person's GitHub link (design, *`/settings/ai`*,
*`/settings/connections`*, *GitHub account link*; plan T3.3 to T3.6, T3.17)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, Field

from nexovue_api.data.postgres.models import SERVICE_NAME_LENGTH

# A form takes many code locations at once (FR5b); more than this is a script's job.
MAX_LOCATIONS_PER_FORM: Final = 200
# An owner's extra paths on a consent (D9).
MAX_OWNER_PATHS: Final = 50
PATH_MAX_CHARACTERS: Final = 200


class FixAppRepository(BaseModel):
    id: uuid.UUID
    key: str
    is_private: bool
    is_reachable: bool
    # When an admin allowed drafts in this public repository; None while refused (FR42).
    public_allowed_at: datetime | None = None


class FixAppView(BaseModel):
    """The Nexovue suggestions App as an admin sees it. A support view reads its state only."""

    available: bool
    state: str | None
    setup_step: str | None
    install_url: str | None
    # GitHub's grant, once read; None until the first hourly refresh.
    permissions: dict[str, str] | None
    granted_at: datetime | None
    connection_id: uuid.UUID | None
    repositories: list[FixAppRepository]


class StartFixAppRequest(BaseModel):
    github_organization: str | None = Field(default=None, max_length=39)


# ------------------------------------------------------------- code locations


class CodeLocationRow(BaseModel):
    """One service's code location (FR5b). `project_id` null holds for the organization."""

    service: str = Field(min_length=1, max_length=SERVICE_NAME_LENGTH)
    project_id: uuid.UUID | None = None
    repository_id: uuid.UUID
    folder: str | None = Field(default=None, max_length=PATH_MAX_CHARACTERS)
    path_in_production: str | None = Field(default=None, max_length=PATH_MAX_CHARACTERS)


class CodeLocationsRequest(BaseModel):
    rows: list[CodeLocationRow] = Field(min_length=1, max_length=MAX_LOCATIONS_PER_FORM)


class CodeLocationView(BaseModel):
    id: uuid.UUID
    service: str
    project_id: uuid.UUID | None
    repository_id: uuid.UUID
    repository: str
    folder: str | None
    path_in_production: str | None
    # Whether a live consent covers this folder now (FR8): false stops new source.
    consented: bool
    set_at: datetime


class CodeLocations(BaseModel):
    locations: list[CodeLocationView]
    # Services seen with no location, so an admin sees what is left (design).
    services_without_location: list[str]


class CodeLocationRemoval(BaseModel):
    service: str = Field(min_length=1, max_length=SERVICE_NAME_LENGTH)
    project_id: uuid.UUID | None = None


class CodeLocationRemovals(BaseModel):
    rows: list[CodeLocationRemoval] = Field(min_length=1, max_length=MAX_LOCATIONS_PER_FORM)


# --------------------------------------------------------------- source consent


class SourceConsentRequest(BaseModel):
    """An owner's agreement (FR6). `require_github_access` has no default: the owner must
    answer, with nothing preselected (D20)."""

    require_github_access: bool
    extra_excluded_paths: list[str] = Field(default_factory=list, max_length=MAX_OWNER_PATHS)
    allowed_config_paths: list[str] = Field(default_factory=list, max_length=MAX_OWNER_PATHS)


ConsentStanding = Literal["live", "ended", "none"]


class SourceConsentView(BaseModel):
    id: uuid.UUID
    folder_scope: str | None
    provider_host: str
    model: str
    require_github_access: bool
    consented_at: datetime
    # `ended`: the provider, key or model changed since (FR8); agree again or withdraw.
    standing: ConsentStanding
    # An owner's extra paths: hidden from a support view (AC11).
    extra_excluded_paths: list[str] | None
    allowed_config_paths: list[str] | None


class RepositoryConsents(BaseModel):
    repository_id: uuid.UUID
    repository: str
    is_private: bool
    is_reachable: bool
    consents: list[SourceConsentView]


class SourceConsentOverview(BaseModel):
    """Every repository the Fix App reaches, with its consents, and what the confirm needs."""

    available: bool
    provider_host: str | None
    model: str | None
    region: str | None
    # How many members can ask for a suggested fix today (FR6's question).
    requesters: int
    repositories: list[RepositoryConsents]
    # FR7's sentences, exact, for the confirm.
    sentences: list[str]
    never_sent: list[str]


class SourceConsentWithdrawn(BaseModel):
    # How many suggestions' stored code the withdrawal deleted (design: the confirm says it).
    code_deleted: int


# ------------------------------------------------------------------ readiness


class ReadinessCounts(BaseModel):
    """FR10b: of the project's last 30 days of findings, how many reach each result."""

    findings: int
    ready: int
    ready_without_stack: int
    cause_is_elsewhere: int
    project_finding: int
    closed_as_not_a_problem: int
    services_without_location: list[str]
    sentence: str


# ------------------------------------------------------------ the GitHub link


class GitHubLinkStarted(BaseModel):
    attempt_id: uuid.UUID
    authorize_url: str


class GitHubLinkCompleteRequest(BaseModel):
    state: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=200)


class GitHubLinkAttemptView(BaseModel):
    id: uuid.UUID
    status: str
    reason: str | None


class GitHubLinkView(BaseModel):
    linked: bool
    login: str | None
    linked_at: datetime | None


class RepositoryDraftSettings(BaseModel):
    """One repository's draft choices (BR14, D45): off until an owner sets them."""

    repository_id: uuid.UUID
    repository: str
    is_private: bool | None
    drafts_allowed: bool
    ci_on_open: bool
    # GitHub refused a draft here in the last 24 hours (AC27a).
    draft_refused_recently: bool
    set_at: datetime | None


class RepositoryDraftSettingsView(BaseModel):
    """`GET /connections/github/repositories/fix-settings`. `writes_off` is the sentence
    shown while write access is off in code (ADR-097); None once the founder's go is in."""

    available: bool
    writes_off: str | None
    repositories: list[RepositoryDraftSettings]


class RepositoryDraftSettingsRequest(BaseModel):
    """`PUT .../{repository_id}/fix-settings`: an owner's two choices, each stated."""

    drafts_allowed: bool
    ci_on_open: bool
