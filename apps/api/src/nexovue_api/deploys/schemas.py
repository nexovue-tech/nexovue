"""The deploy shapes: what CI signs, what it is answered, and what the console reads back.

**The signed body** (F28 FR6, FR9; D6). One shape for one service or a hundred:
``{"deploys": [ ... ], "dry_run": false}``. Each item signs its own `delivery_id` and
`deployed_at`, so a captured body sent again stores nothing, whatever its headers say.
An unknown field is refused rather than dropped, so a misspelt `commit_sha` is named in
CI's output instead of vanishing.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Final, Literal
from urllib.parse import urlsplit

from nexovue_platform import releases
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from nexovue_api.data.postgres.models import NAME_MAX_LENGTH, SHORT_LABEL_LENGTH

# What produced the notification. Free text, bounded by the column, because the
# set of CI systems is not ours to enumerate.
DEFAULT_SOURCE: Final = "webhook"

DEFAULT_PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 200

# FR6 and FR9 (each a starting guess where the spec says so).
MAX_ITEMS: Final = 100
MAX_URL_LENGTH: Final = 2048
MAX_METADATA_BYTES: Final = 1024
COMMIT_SHA_LENGTHS: Final = (40, 64)
DEFAULT_STATE: Final = "finished"
DeployState = Literal["started", "finished", "failed", "rolled_back"]

_NUL: Final = "\x00"
_HEX: Final = re.compile(r"[0-9a-fA-F]+")

NUL_REFUSED: Final = "must not contain a NUL character"
NAME_REFUSED: Final = (
    "must not contain control, bidirectional or tag characters, and must not be __other__"
)
COMMIT_REFUSED: Final = "must be 40 or 64 hexadecimal characters"
URL_REFUSED: Final = (
    f"must be an https link of at most {MAX_URL_LENGTH} characters, with a host and no "
    "username, password, query or fragment"
)
METADATA_REFUSED: Final = (
    f"must be flat string keys and string values, at most {MAX_METADATA_BYTES} bytes encoded"
)

# Exactly as sent, never stripped: a reported name must equal what telemetry carries, and
# stripping would quietly accept a C1 control the worker folds (U+0085 is whitespace).
Name = Annotated[str, StringConstraints(min_length=1, max_length=NAME_MAX_LENGTH)]
DeliveryId = Annotated[str, StringConstraints(min_length=1, max_length=NAME_MAX_LENGTH)]
SourceLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SHORT_LABEL_LENGTH)
]


class DeployItem(BaseModel):
    """One deploy, as CI signs it."""

    model_config = ConfigDict(extra="forbid")

    delivery_id: DeliveryId
    service_name: Name
    version: Name
    # The sender's claim. Bounded against our clock by the service (FR8).
    deployed_at: AwareDatetime
    state: DeployState = DEFAULT_STATE
    commit_sha: str | None = None
    url: str | None = None
    source: SourceLabel = DEFAULT_SOURCE
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("delivery_id", "source")
    @classmethod
    def _no_nul(cls, value: str) -> str:
        if _NUL in value:
            raise ValueError(NUL_REFUSED)
        return value

    @field_validator("service_name", "version")
    @classmethod
    def _one_name_check(cls, value: str) -> str:
        """The check the worker folds by (`release-names.json`), refused here (FR9)."""
        if _NUL in value:
            raise ValueError(NUL_REFUSED)
        if not releases.name_allowed(value):
            raise ValueError(NAME_REFUSED)
        return value

    @field_validator("commit_sha")
    @classmethod
    def _hex_commit(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) not in COMMIT_SHA_LENGTHS or not _HEX.fullmatch(value):
            raise ValueError(COMMIT_REFUSED)
        return value.lower()

    @field_validator("url")
    @classmethod
    def _plain_https(cls, value: str | None) -> str | None:
        """Only ever rendered for a person, never fetched; still, nothing that could
        carry a credential or a signature parameter (design, *Security*)."""
        if value is None:
            return None
        if _NUL in value or len(value) > MAX_URL_LENGTH or "?" in value or "#" in value:
            raise ValueError(URL_REFUSED)
        try:
            parts = urlsplit(value)
            # Reading the port parses it; an unparsable one raises here, not later.
            parts.port  # noqa: B018
        except ValueError as unparsable:
            raise ValueError(URL_REFUSED) from unparsable
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError(URL_REFUSED)
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _flat_and_small(cls, value: object) -> object:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(entry, str) for key, entry in value.items()
        ):
            raise ValueError(METADATA_REFUSED)
        if any(_NUL in key or _NUL in entry for key, entry in value.items()):
            raise ValueError(NUL_REFUSED)
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > MAX_METADATA_BYTES:
            raise ValueError(METADATA_REFUSED)
        return value


class DeployBatch(BaseModel):
    """The whole signed body."""

    model_config = ConfigDict(extra="forbid")

    deploys: list[DeployItem] = Field(min_length=1, max_length=MAX_ITEMS)
    # Checked in every way, stored nowhere (FR11, D21).
    dry_run: bool = False


class DeployAnswer(BaseModel):
    """One item's answer (FR12), so a misspelt service or version shows in CI's output."""

    delivery_id: str
    # Absent on a dry run.
    deploy_id: uuid.UUID | None = None
    release_id: uuid.UUID | None = None
    # This project already stored this delivery id: answered, nothing changed (FR7).
    repeated: bool = False
    # The service has sent telemetry to this project.
    known: bool
    # This version has been counted in the service's traffic.
    version_seen: bool
    # The service's most recently seen versions, newest first.
    recent_versions: list[str]


class DeployBatchAnswer(BaseModel):
    dry_run: bool
    # How many new deploys this request stored; 0 on a dry run.
    stored: int
    message: str
    deploys: list[DeployAnswer]


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
    # F36, AC20: a capped list says when the cap bit, so a caller reading through
    # the API knows the answer is partial rather than assuming it is complete.
    truncated: bool = False


class WebhookSecret(BaseModel):
    """One live secret, as listed: never its value (FR2, AC4)."""

    id: uuid.UUID
    # Its last characters, so a person can tell one secret from another.
    hint: str
    created_at: datetime
    last_used_at: datetime | None


class MintedWebhookSecret(WebhookSecret):
    """The one answer that carries a secret's value: the response that created it."""

    secret: str


class LastRefusal(BaseModel):
    """The last refusal a signed sender earned (FR10, D22). An unsigned one is never
    recorded, or an outsider could fill this."""

    at: datetime
    # 1-based; null when the whole body was refused.
    position: int | None
    field: str | None
    secret_id: uuid.UUID | None


class CurrentVersion(BaseModel):
    """What one service is running now, as counted, so a fixed `latest` stands out."""

    service_name: str
    version: str
    first_seen_at: datetime | None


class WebhookSecretsResponse(BaseModel):
    """Everything the Deploys section shows (FR1)."""

    project_id: uuid.UUID
    webhook_path: str
    signature_header: str
    secrets: list[WebhookSecret]
    last_deploy_at: datetime | None
    last_check_at: datetime | None
    last_refusal: LastRefusal | None
    current_versions: list[CurrentVersion]
    # FR56: older releases are being removed early.
    over_release_cap: bool


class MintedWebhookSecretResponse(WebhookSecretsResponse):
    minted: MintedWebhookSecret
