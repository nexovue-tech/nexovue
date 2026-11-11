"""F4 plan T2.5 (C1, AC31, AC59): a person's removal takes their conversations, after
settling a question in flight, and keeps the read record with no one named."""

from __future__ import annotations

from datetime import UTC, datetime

from assistant_live import AVAILABLE, KEYS, NOW, chat_on, db, require_postgres, tenant
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.assistant import service
from nexovue_api.assistant.schemas import AskContext
from nexovue_api.auth.dependencies import Identity
from nexovue_api.data.postgres.models import (
    AssistantConversation,
    AssistantMessage,
    AssistantQuestionCount,
    AssistantReadLog,
)
from nexovue_api.members.service import scrub

require_postgres("chat's privacy suite")
__all__ = ["db"]

RESERVED = 60_000


async def test_removal_settles_then_deletes_conversations_and_unnames_the_read_record(
    db: AsyncSession,
) -> None:
    made = await tenant(db)
    await chat_on(db, made)
    asked = await service.ask(
        db,
        identity=Identity(user=made.user, organization=made.organization),
        project_id=made.project.id,
        conversation_id=None,
        question="What is failing?",
        context=AskContext(),
        ai=AVAILABLE,
        keys=KEYS,
        now=NOW,
    )
    month = NOW.date().replace(day=1)
    await db.execute(
        update(AssistantMessage)
        .where(AssistantMessage.id == asked.id)
        .values(state="running", reserved_tokens=RESERVED, reserved_month=month)
    )
    await db.execute(
        text("INSERT INTO ai_usage_monthly (org_id, month, reserved_tokens) VALUES (:o, :m, :r)"),
        {"o": made.organization.id, "m": month, "r": RESERVED},
    )
    db.add(
        AssistantReadLog(
            org_id=made.organization.id,
            project_id=made.project.id,
            user_id=made.user.id,
            tool="list_services",
            provider_label="OpenAI-compatible",
        )
    )
    await db.flush()

    await scrub(db, member=made.user, now=datetime.now(UTC))

    org = made.organization.id
    for model in (AssistantConversation, AssistantMessage, AssistantQuestionCount):
        left = await db.scalar(select(func.count()).select_from(model).where(model.org_id == org))
        assert left == 0, model.__tablename__
    reads = (await db.scalars(select(AssistantReadLog).where(AssistantReadLog.org_id == org))).all()
    assert [read.user_id for read in reads] == [None]
    # What the question held was charged as spent before its row went (F3 BR13).
    usage = (
        await db.execute(
            text("SELECT used_tokens, reserved_tokens FROM ai_usage_monthly WHERE org_id = :o"),
            {"o": org},
        )
    ).one()
    assert tuple(usage) == (RESERVED, 0)
