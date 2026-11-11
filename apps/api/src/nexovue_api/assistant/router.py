"""`/assistant`: chat's panel and its settings (F4 design, *Routes*; plan T2.6). HTTP only.

Postgres only, so on the default rate-limit tier: the panel polls one message every 1.5 s,
and `/assistant` on the telemetry tier would put that poll beside a screen's ClickHouse
reads under one cap (design, *Polling*). Reading is the person's own; asking, stopping and
marking wrong are `ASK_ASSISTANT`; settings are `MANAGE_AI`; agreeing is the owner's.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from nexovue_api.ai.service import availability_of
from nexovue_api.assistant import service
from nexovue_api.assistant.schemas import (
    AskRequest,
    Availability,
    ChatConsent,
    ChatSettings,
    ConversationSummary,
    ConversationView,
    FeedbackRequest,
    MessageView,
    NewConversation,
    ReadRow,
    SaveChatSettings,
)
from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayAskAssistant, MayConsentAIDisclosure, MayManageAI
from nexovue_api.deps import AppSettings, DbSession

router = APIRouter(prefix="/assistant", tags=["assistant"])

ProjectId = Annotated[uuid.UUID, Query(description="The project the panel is on")]


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("/availability", summary="Whether chat can answer here, before anyone types")
async def read_availability(
    identity: CurrentIdentity, db: DbSession, settings: AppSettings, project_id: ProjectId
) -> Availability:
    return await service.availability(
        db,
        identity=identity,
        project_id=project_id,
        ai=availability_of(settings),
        now=_now(),
    )


@router.get("/conversations", summary="Your own conversations in a project, newest first")
async def list_conversations(
    identity: CurrentIdentity, db: DbSession, project_id: ProjectId
) -> list[ConversationSummary]:
    return await service.conversations(db, identity=identity, project_id=project_id)


@router.post(
    "/conversations",
    status_code=status.HTTP_202_ACCEPTED,
    summary="A new conversation, with its first question",
)
async def start_conversation(
    identity: MayAskAssistant, db: DbSession, settings: AppSettings, request: NewConversation
) -> MessageView:
    return await service.ask(
        db,
        identity=identity,
        project_id=request.project_id,
        conversation_id=None,
        question=request.question,
        context=request.context,
        ai=availability_of(settings),
        keys=settings.ai_keys,
        now=_now(),
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask a question in a conversation",
)
async def ask_question(
    identity: MayAskAssistant,
    db: DbSession,
    settings: AppSettings,
    conversation_id: uuid.UUID,
    project_id: ProjectId,
    request: AskRequest,
) -> MessageView:
    return await service.ask(
        db,
        identity=identity,
        project_id=project_id,
        conversation_id=conversation_id,
        question=request.question,
        context=request.context,
        ai=availability_of(settings),
        keys=settings.ai_keys,
        now=_now(),
    )


@router.get("/conversations/{conversation_id}", summary="One page of a conversation")
async def read_conversation(
    identity: CurrentIdentity,
    db: DbSession,
    conversation_id: uuid.UUID,
    before: Annotated[datetime | None, Query(description="Messages asked before this")] = None,
) -> ConversationView:
    return await service.conversation(
        db, identity=identity, conversation_id=conversation_id, before=before, now=_now()
    )


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}",
    summary="One question's state, steps and answer: polled while it runs",
)
async def read_message(
    identity: CurrentIdentity, db: DbSession, conversation_id: uuid.UUID, message_id: uuid.UUID
) -> MessageView:
    return await service.message(
        db,
        identity=identity,
        conversation_id=conversation_id,
        message_id=message_id,
        now=_now(),
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/stop", summary="Stop a question"
)
async def stop_question(
    identity: MayAskAssistant, db: DbSession, conversation_id: uuid.UUID, message_id: uuid.UUID
) -> MessageView:
    return await service.stop(
        db,
        identity=identity,
        conversation_id=conversation_id,
        message_id=message_id,
        now=_now(),
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark an answer wrong, with a reason",
)
async def mark_wrong(
    identity: MayAskAssistant,
    db: DbSession,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    request: FeedbackRequest,
) -> None:
    await service.mark_wrong(
        db,
        identity=identity,
        conversation_id=conversation_id,
        message_id=message_id,
        reason=request.reason,
        now=_now(),
    )


@router.delete(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Undo marking an answer wrong",
)
async def unmark_wrong(
    identity: MayAskAssistant, db: DbSession, conversation_id: uuid.UUID, message_id: uuid.UUID
) -> None:
    await service.mark_wrong(
        db,
        identity=identity,
        conversation_id=conversation_id,
        message_id=message_id,
        reason=None,
        now=_now(),
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation and its questions",
)
async def delete_conversation(
    identity: CurrentIdentity, db: DbSession, conversation_id: uuid.UUID
) -> None:
    await service.delete_conversation(db, identity=identity, conversation_id=conversation_id)


@router.get("/settings", summary="Chat's switch, limit, data level, consent and counts")
async def read_settings(
    identity: MayManageAI, db: DbSession, settings: AppSettings
) -> ChatSettings:
    return await service.chat_settings(
        db, org_id=identity.org_id, ai=availability_of(settings), now=_now()
    )


@router.put("/settings", summary="Change chat's switch, limit and data level")
async def save_settings(
    identity: MayManageAI, db: DbSession, settings: AppSettings, request: SaveChatSettings
) -> ChatSettings:
    return await service.save_settings(
        db, identity=identity, request=request, ai=availability_of(settings), now=_now()
    )


@router.post("/settings/consent", summary="An owner agrees to what chat sends, at a data level")
async def agree(
    identity: MayConsentAIDisclosure, db: DbSession, settings: AppSettings, request: ChatConsent
) -> ChatSettings:
    return await service.consent(
        db, identity=identity, request=request, ai=availability_of(settings), now=_now()
    )


@router.get("/reads", summary="What chat read and sent where: no filter values")
async def read_reads(identity: MayManageAI, db: DbSession) -> list[ReadRow]:
    return await service.reads(db, org_id=identity.org_id)
