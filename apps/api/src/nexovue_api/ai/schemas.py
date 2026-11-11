"""What the AI settings routes take and answer (F3 FR23 to FR32). No field carries the key:
the sealed column has no schema here, so a response cannot hold it by accident (AC34)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Final, Literal

from nexovue_platform.ai.connection import MAX_BASE_URL_LENGTH, MAX_KEY_LENGTH, MAX_REGION_LENGTH
from nexovue_platform.ai.jobs import CheckReason
from nexovue_platform.ai_records import CALL_SECONDS_MAX, CALL_SECONDS_MIN
from pydantic import BaseModel, Field

# Why AI cannot be offered here at all (design, *AI settings*, Status states).
UnavailableReason = Literal["hosted", "account_missing", "no_key"]
# What stands between a connection and the switch (FR27), in the screen's order.
Missing = Literal["connection", "test", "limit", "consent", "container"]
WireFormatName = Literal["openai_chat", "anthropic_messages", "bedrock_converse"]
AddressKind = Literal["private", "public"]
# A run's reading of its address against the consents recorded when it ran (AC72).
HostConsent = Literal["consented", "not_consented", "not_applicable"]
Match = Literal["matches", "cannot_reproduce", "expired"]

# The screen's suggestion for a first limit, labelled a guess there (design, *Limit*).
SUGGESTED_MONTHLY_TOKENS: Final = 2_000_000
MAX_MONTHLY_TOKENS: Final = 10_000_000_000
MAX_MODEL_CHARS: Final = 200


class Consent(BaseModel):
    by_user_id: uuid.UUID | None
    by_name: str | None
    # The consenting owner has left, or is no longer an owner: consent stands (FR26).
    by_left: bool
    at: datetime
    # Whether it still covers the saved address, format, region, key and schema.
    current: bool


class ConnectionView(BaseModel):
    """The saved connection, for `MANAGE_AI`. `has_key` and never the key."""

    wire_format: WireFormatName
    base_url: str
    provider_host: str
    address_kind: AddressKind
    model: str
    region_label: str | None
    has_key: bool
    call_seconds: int
    monthly_token_limit: int | None
    price_input: Decimal | None
    price_output: Decimal | None
    checked_at: datetime | None
    checked_ok: bool | None
    # The last passing test covers the values saved now (FR27).
    test_current: bool
    # A saved model differs from the one last tested while consent holds (FR26).
    model_changed: bool
    consent: Consent | None


class AISettings(BaseModel):
    """`GET /ai/connection`. A person without `MANAGE_AI` reads `on` alone (AC34)."""

    on: bool
    full: bool
    available: bool | None = None
    unavailable_reason: UnavailableReason | None = None
    container_running: bool | None = None
    beat_lock_exposed: bool | None = None
    off_reason: Literal["person", "consent_ended"] | None = None
    missing: list[Missing] = Field(default_factory=list)
    connection: ConnectionView | None = None
    # Formats with a dated real call (AC77); the screen offers only these.
    formats_offered: list[WireFormatName] = Field(default_factory=list)
    # What the owner agrees to, sent back on consent so a change in between is refused.
    consent_fingerprint: str | None = None
    schema_version: int | None = None
    suggested_monthly_tokens: int = SUGGESTED_MONTHLY_TOKENS


class SaveConnection(BaseModel):
    """`PUT /ai/connection`. `key` absent keeps the saved one, unless the address or format
    changes, which erases it (FR23)."""

    wire_format: WireFormatName
    base_url: str = Field(min_length=1, max_length=MAX_BASE_URL_LENGTH)
    model: str = Field(min_length=1, max_length=MAX_MODEL_CHARS)
    region_label: str | None = Field(default=None, max_length=MAX_REGION_LENGTH)
    key: str | None = Field(default=None, max_length=MAX_KEY_LENGTH)
    call_seconds: int = Field(default=100, ge=CALL_SECONDS_MIN, le=CALL_SECONDS_MAX)
    monthly_token_limit: int | None = Field(default=None, gt=0, le=MAX_MONTHLY_TOKENS)
    price_input: Decimal | None = Field(default=None, ge=0, le=Decimal(100_000))
    price_output: Decimal | None = Field(default=None, ge=0, le=Decimal(100_000))


class CheckAccepted(BaseModel):
    request_id: uuid.UUID


class CheckResult(BaseModel):
    pending: bool
    ok: bool | None = None
    reason: CheckReason | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class AliasRow(BaseModel):
    alias: str
    stands_for: str


class HeaderRow(BaseModel):
    name: str
    value: str


class Preview(BaseModel):
    """`GET /ai/preview` (FR25, AC36): the exact request, with the key shown as a stand-in."""

    sample: bool
    finding_id: uuid.UUID | None
    project_id: uuid.UUID | None
    read_at: datetime | None
    summary: list[str]
    method: str
    url: str
    headers: list[HeaderRow]
    instructions: str
    body: str
    request_sha256: str
    prompt_version: str
    aliases: list[AliasRow]
    sendable_fields: list[str]
    fact_kinds: list[str]


class ConsentRequest(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64)
    schema_version: int


class SwitchRequest(BaseModel):
    on: bool


class SwitchState(BaseModel):
    on: bool


class ProjectOptOut(BaseModel):
    project_id: uuid.UUID
    name: str
    opted_out: bool


class Usage(BaseModel):
    """`GET /ai/usage` (FR29). Money is an estimate, from the prices typed (BR14)."""

    month: date
    resets_on: date
    used_tokens: int
    reserved_tokens: int
    monthly_token_limit: int | None
    input_tokens: int
    output_tokens: int
    estimated_cost: Decimal | None
    worst_case_cost: Decimal | None


class RunRow(BaseModel):
    """One run (FR29, FR39): never its content, only the names of the fields sent."""

    id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None
    seconds: float | None
    purpose: str
    outcome: str
    finding_id: uuid.UUID | None
    provider_host: str
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    tokens_estimated: bool
    fields_sent: list[str]
    by_person: bool
    host_consent: HostConsent


class SentView(BaseModel):
    """`GET /ai/runs/{id}/sent` (FR29, AC36): rebuilt, with its hash statement."""

    match: Match
    statement: str
    instructions: str | None
    body: str | None
    facts: str | None
