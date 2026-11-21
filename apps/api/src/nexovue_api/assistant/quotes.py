"""F4 T3.2: a chat answer's quotes, read again as the person (design, *Quote display*).

An answer stores a quote as a reference: the step, the tool, the field and the row's own
keys, never its text (D17, D28). Opening the answer reads each one again through the same
services a screen uses, in the person's tenant and the question's project, so a record that
has aged out or been erased reads `gone`, and a failed read reads `read_failed`, never
"gone" (AC19). Postgres finds the message; ClickHouse holds the values, and Postgres a
knowledge chunk's (Step 5): a runbook's quote carries its runbook and its age, and one whose
runbook was deleted reads `gone` ("Earlier answers quoting it will show it is gone"). A note's
quote (Step 7) carries the date it was written and its case, never its author's name; it is
inert, never offered as a command, and a deleted note's quote reads `gone`, since its chunk went
with it.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from nexovue_platform.assistant_tools import KNOWLEDGE_TOOL
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.assistant.service import Asker
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import (
    AssistantMessage,
    AssistantStep,
    FindingNote,
    KnowledgeChunk,
    Runbook,
)
from nexovue_api.errorgroups import service as error_groups
from nexovue_api.errors.exceptions import DomainError, NotFoundError
from nexovue_api.logs.search import log_line_at
from nexovue_api.projects.service import require_project

# A quote shows at most this much, and says when it was cut (FR15, a guess).
MAX_QUOTE_CHARACTERS: Final = 500
NOT_FOUND: Final = "That question does not exist."

QuoteState = Literal["shown", "gone", "read_failed"]


class QuoteView(BaseModel):
    id: str
    tool: str
    field: str
    # Folded under "Text from a log, written by anyone who can reach your service. Do not
    # run it." when the worker found a link or a command in it (AC63).
    folded: bool
    state: QuoteState
    value: str | None = None
    was_cut: bool = False
    # A runbook's quote: the one labelled as the team's command, offered for copy, with the
    # runbook to open and its age (FR15, T5.6).
    runbook: bool = False
    runbook_id: uuid.UUID | None = None
    written_at: datetime | None = None
    # A teammate's note on a case: labelled "A teammate wrote this", never a command (AC63),
    # with the case it was written on.
    note: bool = False
    finding_id: uuid.UUID | None = None


class QuoteList(BaseModel):
    message_id: uuid.UUID
    quotes: list[QuoteView]


class _Gone(Exception):  # noqa: N818 — a record no longer kept, named for the reader
    pass


@dataclass(frozen=True, slots=True)
class _Read:
    value: object
    runbook: Runbook | None = None
    note: FindingNote | None = None


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


async def _log_value(
    repository: TenantScopedRepository, project_id: str, field: str, ref: Mapping[str, Any]
) -> object:
    observed = ref.get("observed_at")
    if not isinstance(observed, str):
        raise _Gone
    line = await log_line_at(
        repository,
        project_id=project_id,
        observed_at=datetime.fromisoformat(observed.replace("Z", "+00:00")),
        service_name=str(ref.get("service_name", "")),
        severity_number=int(ref.get("severity_number", 0)),
        row_key=int(str(ref.get("row_key", "0"))),
    )
    if line is None:
        raise _Gone
    return line.body if field == "body" else line.attributes


async def _group_value(
    repository: TenantScopedRepository,
    project_id: str,
    field: str,
    ref: Mapping[str, Any],
    step: AssistantStep | None,
) -> object:
    if step is None or step.from_time is None or step.to_time is None:
        raise _Gone
    try:
        detail = await error_groups.get_error_group(
            repository,
            project_id=project_id,
            group_id=str(ref.get("group_id", "")),
            from_time=step.from_time,
            to_time=step.to_time,
        )
    except NotFoundError as missing:
        raise _Gone from missing
    return getattr(detail.group, field)


async def _knowledge_value(
    session: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID, ref: Mapping[str, Any]
) -> tuple[KnowledgeChunk, Runbook | None, FindingNote | None]:
    try:
        chunk_id = uuid.UUID(str(ref.get("id", "")))
    except ValueError as malformed:
        raise _Gone from malformed
    found = (
        await session.execute(
            select(KnowledgeChunk, Runbook, FindingNote)
            .outerjoin(
                Runbook,
                (Runbook.org_id == KnowledgeChunk.org_id)
                & (Runbook.project_id == KnowledgeChunk.project_id)
                & (Runbook.id == KnowledgeChunk.runbook_id),
            )
            .outerjoin(
                FindingNote,
                (FindingNote.org_id == KnowledgeChunk.org_id)
                & (FindingNote.id == KnowledgeChunk.note_id),
            )
            .where(
                KnowledgeChunk.org_id == org_id,
                KnowledgeChunk.project_id == project_id,
                KnowledgeChunk.id == chunk_id,
            )
        )
    ).first()
    if found is None:
        raise _Gone
    chunk, runbook, note = found
    return chunk, runbook, note


async def _read(
    session: AsyncSession,
    repository: TenantScopedRepository,
    message: AssistantMessage,
    stored: Mapping[str, Any],
    step: AssistantStep | None,
) -> _Read:
    tool = stored.get("tool")
    field = str(stored.get("field", ""))
    ref = stored.get("reference")
    project_id = str(message.project_id)
    if not isinstance(ref, Mapping):
        raise _Gone
    if tool == "search_logs" and field in ("body", "attributes"):
        return _Read(await _log_value(repository, project_id, field, ref))
    if tool in ("list_error_groups", "get_error_group") and field in (
        "message_template",
        "latest_message",
    ):
        return _Read(await _group_value(repository, project_id, field, ref, step))
    if tool == KNOWLEDGE_TOOL and field in ("content", "title"):
        chunk, runbook, note = await _knowledge_value(
            session, message.org_id, message.project_id, ref
        )
        if field == "title":
            if runbook is None:
                raise _Gone
            return _Read(runbook.title, runbook)
        return _Read(chunk.content, runbook, note)
    raise _Gone


async def quotes(
    session: AsyncSession,
    repository: TenantScopedRepository,
    *,
    asker: Asker,
    message_id: uuid.UUID,
) -> QuoteList:
    """Each quote of the person's own answer, read again. Another person's message, or none,
    answers the same 404."""
    message: AssistantMessage | None = await session.scalar(
        select(AssistantMessage).where(
            AssistantMessage.org_id == asker.org_id,
            AssistantMessage.user_id == asker.user_id,
            AssistantMessage.id == message_id,
        )
    )
    if message is None:
        raise NotFoundError(NOT_FOUND)
    # A deleted project's answers read nothing: 404, as for a question that does not exist.
    await require_project(session, org_id=asker.org_id, project_id=message.project_id)
    stored = (message.answer or {}).get("quotes") or {}
    steps = {
        f"s{step.position}": step
        for step in await session.scalars(
            select(AssistantStep).where(
                AssistantStep.org_id == asker.org_id, AssistantStep.message_id == message.id
            )
        )
    }
    views: list[QuoteView] = []
    for quote_id, quote in stored.items() if isinstance(stored, Mapping) else ():
        if not isinstance(quote, Mapping):
            continue
        view = QuoteView(
            id=str(quote_id),
            tool=str(quote.get("tool", "")),
            field=str(quote.get("field", "")),
            folded=bool(quote.get("folded", True)),
            state="gone",
        )
        try:
            read = await _read(
                session, repository, message, quote, steps.get(str(quote.get("step")))
            )
            value = _text(read.value)
        except _Gone:
            views.append(view)
            continue
        except (DomainError, OSError, ValueError, TimeoutError):
            # A failed read is not "gone": the person may retry (AC19).
            views.append(view.model_copy(update={"state": "read_failed"}))
            continue
        runbook, note = read.runbook, read.note
        written_at = runbook.updated_at if runbook else (note.created_at if note else None)
        views.append(
            view.model_copy(
                update={
                    "state": "shown",
                    "value": value[:MAX_QUOTE_CHARACTERS],
                    "was_cut": len(value) > MAX_QUOTE_CHARACTERS,
                    # Re-read, not trusted from the stored answer: the chunk says what it is.
                    "runbook": runbook is not None,
                    "folded": False if runbook is not None else view.folded,
                    "runbook_id": None if runbook is None else runbook.id,
                    "written_at": written_at,
                    "note": note is not None,
                    "finding_id": None if note is None else note.finding_id,
                }
            )
        )
    return QuoteList(message_id=message.id, quotes=views)
