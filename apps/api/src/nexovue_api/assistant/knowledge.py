"""F4 T5.4: knowledge search, read by chat's loop alone (design, section 6; D31).

`GET /assistant/knowledge/search` takes an **`AssistantTicketReader`**: a question's ticket,
and nothing else. A person reads runbooks on their own screen and an API token reads no
knowledge (FR37), so the route is not on the read surface; it is the catalog's one named
exception (AC14). The ticket check is the read surface's own (`tickets.valid_for`), run on
every read, and the organization and project come from the ticket's row, never the request
(AC42). At data level *facts only* it refuses, as the loop never offers it there (AC68).

**Keyword search only until Step 6** (AC41): `content_tsv @@ websearch_to_tsquery('english',
q)`, ranked, at most 20, and the result says `keyword`. A fact line whose finding is no longer
`recovered` is left out at once, before the sweep deletes it (AC84). Postgres only, on the
default rate-limit tier, charged to the question's person.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends, Query
from nexovue_platform.assistant_tools import MAX_SEARCH_TEXT
from pydantic import BaseModel
from sqlalchemy import func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnClause

from nexovue_api.auth.dependencies import Reader, get_ticket_reader
from nexovue_api.data.postgres.models import AssistantMessage, Finding, KnowledgeChunk, Runbook
from nexovue_api.deps import DbSession
from nexovue_api.errors.exceptions import PermissionDeniedError
from nexovue_api.projects.service import require_readable_project

AssistantTicketReader = Annotated[Reader, Depends(get_ticket_reader)]

router = APIRouter(prefix="/assistant/knowledge", tags=["assistant"])

MAX_RESULTS: Final = 20
FACTS_ONLY: Final = "At this data level, chat reads no runbooks or notes."
# The text-search configuration, a constant: never a request value.
_ENGLISH: Final[ColumnClause[Any]] = literal_column("'english'::regconfig")
_RECOVERED: Final = "recovered"


class KnowledgeRow(BaseModel):
    """One chunk, as the catalog sends it: the text is the team's, fenced wherever it goes."""

    id: uuid.UUID
    source_kind: Literal["runbook", "fact"]
    # A runbook's title; null for a fact line.
    title: str | None
    content: str
    service_name: str | None
    # A runbook's last change, or when a fact line's finding closed: the age a label shows.
    written_at: datetime
    runbook_id: uuid.UUID | None
    finding_id: uuid.UUID | None


class KnowledgeResult(BaseModel):
    chunks: list[KnowledgeRow]
    # How it searched (AC41); Step 6 adds meaning search beside it.
    search: Literal["keyword"] = "keyword"


async def _refuse_facts_only(session: AsyncSession, reader: Reader) -> None:
    level = await session.scalar(
        select(AssistantMessage.data_level).where(
            AssistantMessage.org_id == reader.org_id, AssistantMessage.id == reader.message_id
        )
    )
    if level != "facts_and_text":
        raise PermissionDeniedError(FACTS_ONLY)


async def search(
    session: AsyncSession, *, reader: Reader, project_id: uuid.UUID, phrase: str
) -> KnowledgeResult:
    project = await require_readable_project(session, reader=reader, project_id=project_id)
    await _refuse_facts_only(session, reader)
    query = func.websearch_to_tsquery(_ENGLISH, phrase)
    rank = func.ts_rank(KnowledgeChunk.content_tsv, query)
    rows = await session.execute(
        select(KnowledgeChunk, Runbook, Finding)
        .outerjoin(
            Runbook,
            (Runbook.org_id == KnowledgeChunk.org_id)
            & (Runbook.project_id == KnowledgeChunk.project_id)
            & (Runbook.id == KnowledgeChunk.runbook_id),
        )
        .outerjoin(
            Finding,
            (Finding.org_id == KnowledgeChunk.org_id)
            & (Finding.project_id == KnowledgeChunk.project_id)
            & (Finding.id == KnowledgeChunk.finding_id),
        )
        .where(
            KnowledgeChunk.org_id == reader.org_id,
            KnowledgeChunk.project_id == project.id,
            KnowledgeChunk.content_tsv.op("@@")(query),
            or_(KnowledgeChunk.source_kind != "fact", Finding.closed_reason == _RECOVERED),
        )
        .order_by(rank.desc(), KnowledgeChunk.id)
        .limit(MAX_RESULTS)
    )
    return KnowledgeResult(chunks=[_row(*found) for found in rows.all()])


def _row(chunk: KnowledgeChunk, runbook: Runbook | None, finding: Finding | None) -> KnowledgeRow:
    if runbook is not None:
        return KnowledgeRow(
            id=chunk.id,
            source_kind="runbook",
            title=runbook.title,
            content=chunk.content,
            service_name=runbook.service_name,
            written_at=runbook.updated_at,
            runbook_id=runbook.id,
            finding_id=None,
        )
    closed = finding.closed_at if finding is not None else None
    return KnowledgeRow(
        id=chunk.id,
        source_kind="fact",
        title=None,
        content=chunk.content,
        service_name=chunk.fact_service,
        written_at=closed or chunk.created_at,
        runbook_id=None,
        finding_id=chunk.finding_id,
    )


@router.get("/search", summary="Chat's keyword search over a project's runbooks and fact lines")
async def search_knowledge(
    reader: AssistantTicketReader,
    db: DbSession,
    project_id: Annotated[uuid.UUID, Query(description="The question's project")],
    q: Annotated[
        str, Query(min_length=1, max_length=MAX_SEARCH_TEXT, description="Keywords to find")
    ],
) -> KnowledgeResult:
    return await search(db, reader=reader, project_id=project_id, phrase=q)
