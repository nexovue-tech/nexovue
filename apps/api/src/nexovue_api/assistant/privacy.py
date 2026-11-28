"""A person's conversations when they leave (F4 design, *Other inventories*; plan T2.5, C1).

`members.scrub` keeps the user row, so nothing cascades from it: their conversations would
outlive them. This runs inside `scrub`'s transaction. It **settles first**: a question still
in flight ends `stopped`, and what it held is charged as spent, so a reservation is never
lost with its row. Then the person's conversations (questions and steps with them) and
their daily counts go, and the read record keeps its rows with no one named.
"""

from __future__ import annotations

import uuid
from typing import Final

from nexovue_platform.assistant_records import IN_FLIGHT
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    AssistantConversation,
    AssistantMessage,
    AssistantQuestionCount,
    AssistantReadLog,
)

_SETTLE: Final = text(
    "SELECT assistant_finish_message(:id, 'stopped', NULL, NULL, NULL, NULL, 'charge')"
)


async def forget_person(db: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
    unsettled = await db.scalars(
        select(AssistantMessage.id).where(
            AssistantMessage.org_id == org_id,
            AssistantMessage.user_id == user_id,
            AssistantMessage.state.in_(IN_FLIGHT) | (AssistantMessage.reserved_tokens > 0),
        )
    )
    for message_id in unsettled.all():
        await db.execute(_SETTLE, {"id": message_id})
    await db.execute(
        delete(AssistantConversation).where(
            AssistantConversation.org_id == org_id, AssistantConversation.user_id == user_id
        )
    )
    await db.execute(
        delete(AssistantQuestionCount).where(
            AssistantQuestionCount.org_id == org_id, AssistantQuestionCount.user_id == user_id
        )
    )
    await db.execute(
        update(AssistantReadLog)
        .where(AssistantReadLog.org_id == org_id, AssistantReadLog.user_id == user_id)
        .values(user_id=None)
    )
