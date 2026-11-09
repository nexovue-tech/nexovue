"""What the connection routes take and answer (F22). No field ever carries a credential:
the sealed columns have no schema here, so a response cannot hold one by accident."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from nexovue_platform.deployment import HOSTED
from pydantic import BaseModel, Field

# What a person sees where connections do not exist (spec, *Packaging and limits*;
# design, `/settings/connections` states). The console shows these words as given.
HOSTED_SENTENCE: Final = "Tracker connections are not available on the hosted service yet."
OFF_SENTENCE: Final = (
    "Tracker connections are turned off on this install. "
    "An operator can turn them on: see the setup page."
)

UnavailableReason = Literal["hosted", "off"]
# Which step a GitHub setup is on (design, *GitHub setup screens*), one action each.
SetupStep = Literal[
    "on_github",
    "converting",
    "expired",
    "refused",
    "not_installed",
    "no_repository",
    "test_failed",
    "testing",
]
# An App key is a PEM of a few kilobytes; anything larger is not one.
MAX_PRIVATE_KEY_CHARS: Final = 16_384
MAX_SETUP_VALUE_CHARS: Final = 200


@dataclass(frozen=True, slots=True)
class Availability:
    """Whether connections exist on this install, from the settings the API and
    `scheduler-connections` share (AC118), and where the console is."""

    deployment: str
    enabled: bool
    app_base_url: str

    @property
    def unavailable_reason(self) -> UnavailableReason | None:
        # Hosted first: a shared App is founder decision 3, whatever the setting says.
        if self.deployment == HOSTED:
            return "hosted"
        return None if self.enabled else "off"

    @property
    def refusal(self) -> str | None:
        reason = self.unavailable_reason
        if reason is None:
            return None
        return HOSTED_SENTENCE if reason == "hosted" else OFF_SENTENCE


class ConnectionPlace(BaseModel):
    """A repository the connection reaches, by its strict key."""

    id: uuid.UUID
    key: str
    is_private: bool
    is_reachable: bool


class ConnectionView(BaseModel):
    """One live connection. A support view gets `id`, `kind` and `state` alone (AC79)."""

    id: uuid.UUID
    kind: str
    state: str
    app_slug: str | None = None
    # GitHub's page for choosing the App's repositories (general knowledge of its
    # address, not read from GitHub's pages; the founder's run checks it).
    install_url: str | None = None
    setup_step: SetupStep | None = None
    setup_expires_at: datetime | None = None
    last_ok_at: datetime | None = None
    failing_since: datetime | None = None
    last_failure: str | None = None
    last_failure_status: int | None = None
    repositories: list[ConnectionPlace] = Field(default_factory=list)
    # Issues whose status this connection keeps in step: Disconnect's real count (FR32).
    synced_links: int = 0


class ConnectionsOverview(BaseModel):
    available: bool
    unavailable_reason: UnavailableReason | None
    # Whether failure emails can go from this install (FR31).
    email_available: bool
    connections: list[ConnectionView]


class StartGitHubRequest(BaseModel):
    # "Can GitHub reach this server from the internet?" Default no (design).
    reachable: bool = False
    # The GitHub organization to create the App in; none means the person's own account.
    github_organization: str | None = Field(default=None, max_length=MAX_SETUP_VALUE_CHARS)


class GitHubSetupStarted(BaseModel):
    connection_id: uuid.UUID
    # The one-time value GitHub hands back on its redirect. Only its hash is stored.
    state: str
    manifest: dict[str, Any]
    post_url: str


class CompleteGitHubRequest(BaseModel):
    state: str = Field(max_length=MAX_SETUP_VALUE_CHARS)
    code: str = Field(max_length=MAX_SETUP_VALUE_CHARS)


class GitHubSetupCompleted(BaseModel):
    connection_id: uuid.UUID


class ReplaceCredentialRequest(BaseModel):
    private_key: str = Field(max_length=MAX_PRIVATE_KEY_CHARS * 2)


class ConnectionTestRequest(BaseModel):
    connection_id: uuid.UUID


class ConnectionTestAccepted(BaseModel):
    request_id: str


class ConnectionTestResult(BaseModel):
    pending: bool
    ok: bool | None = None
    failure: str | None = None
    state: str | None = None
