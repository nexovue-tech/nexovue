"""F4 T5.4: knowledge search, read by chat's loop alone (design, section 6; D31).

`GET /assistant/knowledge/search` takes an **`AssistantTicketReader`**: a question's ticket,
and nothing else. A person reads runbooks on their own screen and an API token reads no
knowledge (FR37), so the route is not on the read surface; it is the catalog's one named
exception (AC14). The ticket check is the read surface's own (`tickets.valid_for`), run on
every read, and the organization and project come from the ticket's row, never the request
(AC42). At data level *facts only* it refuses, as the loop never offers it there (AC68).

**Keyword search always** (AC41): `content_tsv @@ websearch_to_tsquery('english', q)`, ranked,
at most 20. **Meaning search beside it (Step 6)** when the worker embedded the search phrase and
names its step: an exact scan (pgvector's default, perfect recall) by cosine distance over the
project's chunks embedded by **the step's own model**, so two models' vectors are never
compared (FR31), at most 20; the two lists merged by reciprocal rank, `1 / (60 + rank)`, top 5,
and where either list is empty the other stands whole (design, section 6). The result says
`hybrid` when meaning search found anything, else `keyword`. The step must be this question's
own (the ticket's message), and its vector is read in SQL, never sent over HTTP. With no
`vector` extension the route searches by keyword and nothing else changes (AC43). A fact line
whose finding is no longer `recovered`, and whose case was not resolved, is left out of both,
before the sweep deletes it (AC84). Postgres only, on the default rate-limit tier, charged to
the question's person.

**What people wrote ranks first (Step 7; FR29, AC83).** Each side orders runbooks' and notes'
chunks before nexovue's fact lines, and the merge keeps that order, so a person's note is never
pushed out of the answer by a hundred composed lines about the same service. A note's row
carries its own date, never its author's name, and its finding, so the panel can open the case.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends, Query
from nexovue_platform.assistant_tools import MAX_SEARCH_TEXT
from nexovue_platform.knowledge import (
    MERGED_RESULTS,
    RRF_OFFSET,
    SEARCH_SIDE_RESULTS,
    SearchKind,
)
from nexovue_platform.knowledge_facts import RECOVERED, RESOLVED_CASE_RESOLUTIONS, kept_sql
from pydantic import BaseModel
from sqlalchemy import and_, func, literal_column, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnClause

from nexovue_api import insight_account
from nexovue_api.auth.dependencies import Reader, get_ticket_reader
from nexovue_api.data.postgres.models import (
    AssistantMessage,
    AssistantStep,
    Finding,
    FindingNote,
    KnowledgeChunk,
    Runbook,
)
from nexovue_api.deps import DbSession
from nexovue_api.errors.exceptions import PermissionDeniedError
from nexovue_api.projects.service import require_readable_project

AssistantTicketReader = Annotated[Reader, Depends(get_ticket_reader)]

router = APIRouter(prefix="/assistant/knowledge", tags=["assistant"])

MAX_RESULTS: Final = SEARCH_SIDE_RESULTS
FACTS_ONLY: Final = "At this data level, chat reads no runbooks or notes."
NOT_THIS_QUESTION: Final = "That step is not this question's."
# The text-search configuration, a constant: never a request value.
_ENGLISH: Final[ColumnClause[Any]] = literal_column("'english'::regconfig")
# A note's finding: joined apart from a fact line's, since a note chunk keys its note alone.
NoteFinding = aliased(Finding, name="note_finding")


class KnowledgeRow(BaseModel):
    """One chunk, as the catalog sends it: the text is the team's, fenced wherever it goes."""

    id: uuid.UUID
    source_kind: Literal["runbook", "fact", "note"]
    # A runbook's title; null for a fact line or a note.
    title: str | None
    content: str
    service_name: str | None
    # A runbook's last change, when a note was written, or when a fact line's finding ended:
    # the age a label shows.
    written_at: datetime
    runbook_id: uuid.UUID | None
    # A fact line's finding, or the case a note was written on.
    finding_id: uuid.UUID | None


class KnowledgeResult(BaseModel):
    chunks: list[KnowledgeRow]
    # How it searched (AC41, FR27): `hybrid` when meaning search found anything.
    search: SearchKind = "keyword"


async def _refuse_facts_only(session: AsyncSession, reader: Reader) -> None:
    level = await session.scalar(
        select(AssistantMessage.data_level).where(
            AssistantMessage.org_id == reader.org_id, AssistantMessage.id == reader.message_id
        )
    )
    if level != "facts_and_text":
        raise PermissionDeniedError(FACTS_ONLY)


# The exact scan, read as typed SQL since the vector columns are `setup`'s, outside the ORM.
# The step's own model filters the chunks, and equal dimensions are asked again, so a vector
# of another model, or another size, is never compared (FR31). Organization and project are
# the ticket's; the step must be the ticket's own message's.
_MEANING: Final = text(
    """
    SELECT c.id
    FROM knowledge_chunks AS c
    JOIN assistant_steps AS s
      ON s.org_id = c.org_id AND s.id = :step_id AND s.message_id = :message_id
    LEFT JOIN findings AS f
      ON f.org_id = c.org_id AND f.project_id = c.project_id AND f.id = c.finding_id
    WHERE c.org_id = :org_id AND c.project_id = :project_id
      AND s.query_embedding IS NOT NULL AND c.embedding IS NOT NULL
      AND c.embedded_model = s.query_embedding_model
      AND vector_dims(c.embedding) = vector_dims(s.query_embedding)
      AND (c.source_kind <> 'fact' OR """
    + kept_sql("f")
    + """)
    ORDER BY c.source_kind = 'fact', c.embedding <=> s.query_embedding, c.id
    LIMIT :limit
    """
)


async def search(
    session: AsyncSession,
    *,
    reader: Reader,
    project_id: uuid.UUID,
    phrase: str,
    step_id: uuid.UUID | None = None,
) -> KnowledgeResult:
    project = await require_readable_project(session, reader=reader, project_id=project_id)
    await _refuse_facts_only(session, reader)
    keyword = await _keyword(session, reader=reader, project_id=project.id, phrase=phrase)
    meaning: list[uuid.UUID] = []
    if step_id is not None:
        await _refuse_another_question_s(session, reader=reader, step_id=step_id)
        meaning = await _meaning(session, reader=reader, project_id=project.id, step_id=step_id)
    if not meaning:
        return KnowledgeResult(chunks=[_row(*found) for found in keyword])
    rows = {found[0].id: found for found in keyword}
    missing = [chunk_id for chunk_id in meaning if chunk_id not in rows]
    if missing:
        rows |= {
            found[0].id: found
            for found in await _rows(session, reader=reader, project_id=project.id, ids=missing)
        }
    facts = frozenset(chunk_id for chunk_id, found in rows.items() if _is_fact(found[0]))
    order = merged([found[0].id for found in keyword], meaning, facts=facts)
    return KnowledgeResult(
        chunks=[_row(*rows[chunk_id]) for chunk_id in order if chunk_id in rows],
        search="hybrid",
    )


def merged(
    keyword: list[uuid.UUID],
    meaning: list[uuid.UUID],
    *,
    facts: frozenset[uuid.UUID] = frozenset(),
) -> list[uuid.UUID]:
    """Reciprocal rank fusion, `1 / (60 + rank)` from rank 1, the top `MERGED_RESULTS`; where
    either list is empty, the other stands whole. What people wrote comes before `facts`, the
    fact lines, whatever their scores (FR29); ties keep keyword's order, then meaning's."""
    if not keyword or not meaning:
        return keyword or meaning
    scores: dict[uuid.UUID, float] = {}
    for ranked in (keyword, meaning):
        for rank, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_OFFSET + rank)
    first_seen = {
        chunk_id: place for place, chunk_id in enumerate(dict.fromkeys(keyword + meaning))
    }
    best = sorted(
        scores,
        key=lambda chunk_id: (chunk_id in facts, -scores[chunk_id], first_seen[chunk_id]),
    )
    return best[:MERGED_RESULTS]


def _is_fact(chunk: KnowledgeChunk) -> bool:
    return chunk.source_kind == "fact"


def _joined(statement: Any) -> Any:
    return (
        statement.outerjoin(
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
        .outerjoin(
            FindingNote,
            (FindingNote.org_id == KnowledgeChunk.org_id)
            & (FindingNote.id == KnowledgeChunk.note_id),
        )
        .outerjoin(
            NoteFinding,
            (NoteFinding.org_id == FindingNote.org_id)
            & (NoteFinding.project_id == KnowledgeChunk.project_id)
            & (NoteFinding.id == FindingNote.finding_id),
        )
    )


_SELECTED: Final = (KnowledgeChunk, Runbook, Finding, FindingNote, NoteFinding)


def _still_kept() -> Any:
    """A fact line stays while its finding is recovered or its case resolved (AC84); a note
    is read only within its finding's own project."""
    resolved_case = and_(
        Finding.case_state == "resolved",
        Finding.case_resolution.in_(RESOLVED_CASE_RESOLUTIONS),
    )
    return or_(
        KnowledgeChunk.source_kind == "runbook",
        and_(KnowledgeChunk.source_kind == "note", NoteFinding.id.is_not(None)),
        and_(
            KnowledgeChunk.source_kind == "fact",
            or_(Finding.closed_reason == RECOVERED, resolved_case),
        ),
    )


async def _keyword(
    session: AsyncSession, *, reader: Reader, project_id: uuid.UUID, phrase: str
) -> list[Any]:
    query = func.websearch_to_tsquery(_ENGLISH, phrase)
    rank = func.ts_rank(KnowledgeChunk.content_tsv, query)
    rows = await session.execute(
        _joined(select(*_SELECTED))
        .where(
            KnowledgeChunk.org_id == reader.org_id,
            KnowledgeChunk.project_id == project_id,
            KnowledgeChunk.content_tsv.op("@@")(query),
            _still_kept(),
        )
        # People's words first (FR29): `false` sorts before `true`.
        .order_by((KnowledgeChunk.source_kind == "fact").asc(), rank.desc(), KnowledgeChunk.id)
        .limit(MAX_RESULTS)
    )
    return list(rows.all())


async def _rows(
    session: AsyncSession, *, reader: Reader, project_id: uuid.UUID, ids: list[uuid.UUID]
) -> list[Any]:
    """The meaning side's chunks, by id; `_MEANING` already left out a false alarm's line."""
    rows = await session.execute(
        _joined(select(*_SELECTED)).where(
            KnowledgeChunk.org_id == reader.org_id,
            KnowledgeChunk.project_id == project_id,
            KnowledgeChunk.id.in_(ids),
        )
    )
    return list(rows.all())


async def _refuse_another_question_s(
    session: AsyncSession, *, reader: Reader, step_id: uuid.UUID
) -> None:
    """A step of another question, even of this person, is refused: its vector is not this
    question's to compare (design, section 6)."""
    found = await session.scalar(
        select(AssistantStep.id).where(
            AssistantStep.org_id == reader.org_id,
            AssistantStep.id == step_id,
            AssistantStep.message_id == reader.message_id,
        )
    )
    if found is None:
        raise PermissionDeniedError(NOT_THIS_QUESTION)


async def _meaning(
    session: AsyncSession, *, reader: Reader, project_id: uuid.UUID, step_id: uuid.UUID
) -> list[uuid.UUID]:
    """The exact scan's chunk ids, nearest first; none without the extension (AC43)."""
    if not await insight_account.vector_columns_present(session):
        return []
    found = await session.execute(
        _MEANING,
        {
            "org_id": reader.org_id,
            "project_id": project_id,
            "step_id": step_id,
            "message_id": reader.message_id,
            "limit": MAX_RESULTS,
        },
    )
    return [uuid.UUID(str(chunk_id)) for chunk_id in found.scalars()]


def _ended(finding: Finding | None) -> datetime | None:
    """When a fact line's finding ended: its case's resolve when that keeps the line, as the
    sweep wrote it, else its close."""
    if finding is None:
        return None
    if finding.case_state == "resolved" and finding.case_resolution in RESOLVED_CASE_RESOLUTIONS:
        return finding.case_resolved_at
    return finding.closed_at


def _row(
    chunk: KnowledgeChunk,
    runbook: Runbook | None,
    finding: Finding | None,
    note: FindingNote | None = None,
    note_finding: Finding | None = None,
) -> KnowledgeRow:
    if note is not None:
        return KnowledgeRow(
            id=chunk.id,
            source_kind="note",
            title=None,
            content=chunk.content,
            service_name=None if note_finding is None else note_finding.service_name,
            written_at=note.created_at,
            runbook_id=None,
            finding_id=note.finding_id,
        )
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
    return KnowledgeRow(
        id=chunk.id,
        source_kind="fact",
        title=None,
        content=chunk.content,
        service_name=chunk.fact_service,
        written_at=_ended(finding) or chunk.created_at,
        runbook_id=None,
        finding_id=chunk.finding_id,
    )


@router.get(
    "/search", summary="Chat's keyword and meaning search over a project's runbooks and fact lines"
)
async def search_knowledge(
    reader: AssistantTicketReader,
    db: DbSession,
    project_id: Annotated[uuid.UUID, Query(description="The question's project")],
    q: Annotated[
        str, Query(min_length=1, max_length=MAX_SEARCH_TEXT, description="Keywords to find")
    ],
    step_id: Annotated[
        uuid.UUID | None,
        Query(description="The question's step holding the phrase's own vector, if embedded"),
    ] = None,
) -> KnowledgeResult:
    return await search(db, reader=reader, project_id=project_id, phrase=q, step_id=step_id)
