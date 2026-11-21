"""Chat's request and response bodies (F4 design, *Routes*). The MCP server's own shapes stay
in `tools.py`; these are the panel's and the Chat settings section's."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from nexovue_platform.assistant_records import MAX_QUESTION_CHARACTERS
from pydantic import BaseModel, Field, field_validator

from nexovue_api.assistant.standing import StandingState

DataLevelField = Literal["facts", "facts_and_text"]
WrongReasonField = Literal["wrong_numbers", "wrong_cause", "missed_something", "other"]

MAX_CONTEXT_TEXT = 200
MAX_TIME_ZONE = 64


# `no_data`: chat could answer, but the project has sent nothing to read (spec, *Error cases*).
AvailabilityState = StandingState | Literal["no_data"]


class Availability(BaseModel):
    """What a member learns before typing (design, *States*). Never the provider's address."""

    state: AvailabilityState
    provider_label: str | None = None
    # Whether the saved connection passed a dated real call of its format (F3 AC77).
    tested: bool = False
    questions_left_today: int | None = None
    resets_at: datetime | None = None
    # Owners an admin waits on to agree; empty unless the state is `no_consent`.
    waiting_for: list[str] = Field(default_factory=list)
    may_manage: bool = False


class Window(BaseModel):
    from_time: datetime = Field(alias="from")
    to_time: datetime = Field(alias="to")


class AskContext(BaseModel):
    """What the person was looking at, stated to the model by nexovue (design, *Context*)."""

    window: Window | None = None
    finding_id: uuid.UUID | None = None
    service_name: str | None = Field(default=None, max_length=MAX_CONTEXT_TEXT)
    time_zone: str | None = Field(default=None, max_length=MAX_TIME_ZONE)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARACTERS)
    context: AskContext = Field(default_factory=AskContext)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A question needs words.")
        return value


class NewConversation(AskRequest):
    project_id: uuid.UUID


class StepLink(BaseModel):
    """Built by nexovue from the catalog's table, never by the model."""

    path: str
    # The console route's `$params`, filled from the step's own arguments.
    params: dict[str, str] = Field(default_factory=dict)
    query: dict[str, str]
    caveat: str | None = None


class StepView(BaseModel):
    key: str
    position: int
    tool: str
    words: str
    display_arguments: str | None
    search_hidden: bool
    from_time: datetime | None
    to_time: datetime | None
    row_count: int | None
    was_cut: bool
    state: str
    failure: str | None
    # The first day still kept, on an aged-out step or one read narrowed to what is kept:
    # "The data before {date} is no longer kept" (spec, *Error cases*).
    kept_since: date | None = None
    link: StepLink | None


class MessageView(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    project_id: uuid.UUID
    question: str
    context: dict[str, Any]
    # The state a reader sees: a question past its clock reads `unavailable` before the
    # sweep writes it (design, *Settling*).
    state: str
    phase: str | None
    failure_reason: str | None
    rejection_reason: str | None
    abstain_reason: str | None
    answer: dict[str, Any] | None
    wrong_reason: str | None
    created_at: datetime
    finished_at: datetime | None
    steps: list[StepView]


class ConversationSummary(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    last_message_at: datetime
    in_flight_message_id: uuid.UUID | None


class ConversationView(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    messages: list[MessageView]
    has_older: bool


class FeedbackRequest(BaseModel):
    reason: WrongReasonField


class ChatSettings(BaseModel):
    enabled: bool
    daily_question_limit: int
    data_level: DataLevelField
    mask_emails: bool
    consented_at: datetime | None
    consented_by_name: str | None
    consent_current: bool
    # What an owner agrees to: F3's consent fingerprint and chat's disclosure version.
    consent_fingerprint: str | None
    disclosure_version: int
    monthly_counts: dict[str, int]
    people_this_month: list[PersonCount]


class PersonCount(BaseModel):
    name: str
    questions: int


class SaveChatSettings(BaseModel):
    enabled: bool
    daily_question_limit: int = Field(ge=1, le=1000)
    data_level: DataLevelField
    mask_emails: bool = True


class ChatConsent(BaseModel):
    fingerprint: str = Field(max_length=128)
    disclosure_version: int
    data_level: DataLevelField


class PreviewKind(BaseModel):
    """One kind of text chat can send, generated from the catalog (FR24)."""

    kind: str
    source: str
    # The lowest data level that sends it.
    level: DataLevelField


class PreviewSample(BaseModel):
    """One free-text kind as the provider would see it: masked, cut and fenced."""

    kind: str
    # True when it is nexovue's built-in example, because the project has none yet.
    labelled_sample: bool
    sent: str


class ChatPreview(BaseModel):
    """What an owner reads before agreeing to chat (FR24, AC2). Admins only."""

    provider_label: str
    address: str
    wire_format: str
    model: str
    project_id: uuid.UUID
    project_name: str
    kinds: list[PreviewKind]
    samples: list[PreviewSample]
    masking_rules_off: list[str]
    mask_emails: bool
    # Runbooks and notes sent at once for embedding when chat is turned on.
    sent_for_embedding: int
    consent_fingerprint: str
    disclosure_version: int


class ReadRow(BaseModel):
    at: datetime
    person: str | None
    project: str | None
    tool: str
    from_time: datetime | None
    to_time: datetime | None
    row_count: int | None
    provider_label: str


ChatSettings.model_rebuild()
