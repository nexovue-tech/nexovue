"""Whether chat may answer in a project now (F4 FR24, FR25; design, *Routes*).

One function, read by the availability route, by `POST` before a question is stored, and by
`tickets.valid_for` on **every** read a ticket makes, so turning AI or chat off, opting a
project out, or moving the connection ends questions already in flight (AC60).

Chat's consent binds to F3's consent fingerprint, chat's own disclosure version and the
data level the owner read, so a change to any of them needs the owner again (FR25, AC3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from nexovue_platform.assistant_records import CHAT_DISCLOSURE_VERSION
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.ai import service as ai
from nexovue_api.data.postgres.models import AIConnection, AIProjectOptOut, AssistantSettings

StandingState = Literal["ready", "ai_off", "chat_off", "no_consent", "opted_out"]


@dataclass(frozen=True, slots=True)
class ChatStanding:
    state: StandingState
    connection: AIConnection | None
    settings: AssistantSettings | None

    @property
    def ready(self) -> bool:
        return self.state == "ready"


def consent_matches(settings: AssistantSettings, connection: AIConnection) -> bool:
    """The owner agreed to this connection, this disclosure and this data level."""
    return (
        settings.consented_at is not None
        and settings.consented_fingerprint == ai.consent_fingerprint_of(connection)
        and settings.consented_disclosure_version == CHAT_DISCLOSURE_VERSION
        and settings.consented_data_level == settings.data_level
    )


async def settings_of(session: AsyncSession, *, org_id: uuid.UUID) -> AssistantSettings | None:
    found: AssistantSettings | None = await session.scalar(
        select(AssistantSettings).where(AssistantSettings.org_id == org_id)
    )
    return found


async def standing_of(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    availability: ai.Availability,
) -> ChatStanding:
    connection = await ai.connection_in_use(session, org_id=org_id, availability=availability)
    if connection is None:
        return ChatStanding("ai_off", None, None)
    settings = await settings_of(session, org_id=org_id)
    if settings is None or not settings.enabled:
        return ChatStanding("chat_off", connection, settings)
    if not consent_matches(settings, connection):
        return ChatStanding("no_consent", connection, settings)
    opted_out = await session.scalar(
        select(AIProjectOptOut.project_id).where(
            AIProjectOptOut.org_id == org_id, AIProjectOptOut.project_id == project_id
        )
    )
    if opted_out is not None:
        return ChatStanding("opted_out", connection, settings)
    return ChatStanding("ready", connection, settings)
