"""F4 plan T5.4: knowledge search, against real Postgres (AC41, AC42, AC68, AC84; FR37).

The search is read as the ticket's reader, its organization and project the ticket's own;
each test runs inside a transaction that is rolled back, so it owns its rows. Reading it over
real HTTP with a ticket, and the 404 for another project, is `test_assistant_tools_live.py`'s,
which covers every catalog tool. Clocks are pinned (`NOW`).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from assistant_live import AVAILABLE, KEYS, NOW, chat_on, db, require_postgres, tenant
from nexovue_platform.ai.embed import vector_literal
from nexovue_platform.ai.testing import meaning_vector
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from nexovue_api.apitokens.tokens import ASSISTANT_TICKET_PREFIX
from nexovue_api.assistant import knowledge, service
from nexovue_api.assistant.schemas import AskContext
from nexovue_api.auth.dependencies import Identity, Reader, get_ticket_reader
from nexovue_api.data.postgres.models import (
    AssistantMessage,
    AssistantStep,
    Finding,
    KnowledgeChunk,
    Project,
)
from nexovue_api.errors.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from nexovue_api.runbooks import service as runbooks
from nexovue_api.runbooks.schemas import NewRunbook
from nexovue_api.signup.service import ProvisionedTenant

require_postgres("chat's knowledge search")

# The rolled-back session, as a fixture.
__all__ = ["db"]

WORDS = "## Out of connections\ncheckout runs out of connections: restart pgbouncer."


async def _ready(db: AsyncSession, name: str) -> ProvisionedTenant:
    made = await tenant(db, name)
    await chat_on(db, made)
    return made


async def _runbook(db: AsyncSession, made: ProvisionedTenant, project_id: uuid.UUID) -> uuid.UUID:
    view = await runbooks.create_runbook(
        db,
        identity=Identity(user=made.user, organization=made.organization),
        request=NewRunbook(project_id=project_id, title="Checkout pool", content=WORDS),
        now=NOW,
    )
    return view.id


async def _reader(db: AsyncSession, made: ProvisionedTenant) -> Reader:
    """What a live question's ticket reads as: its organization, project and message."""
    asked = await service.ask(
        db,
        identity=Identity(user=made.user, organization=made.organization),
        project_id=made.project.id,
        conversation_id=None,
        question="What do we do when checkout runs out of connections?",
        context=AskContext(),
        ai=AVAILABLE,
        keys=KEYS,
        now=NOW,
    )
    return Reader(org_id=made.organization.id, project_scope=made.project.id, message_id=asked.id)


async def _search(db: AsyncSession, reader: Reader, phrase: str, project: uuid.UUID) -> list[str]:
    found = await knowledge.search(db, reader=reader, project_id=project, phrase=phrase)
    assert found.search == "keyword"
    return [row.content for row in found.chunks]


async def test_keyword_search_finds_a_runbook_and_says_it_searched_by_keyword(
    db: AsyncSession,
) -> None:
    """AC41: with no embeddings, search works by keywords, and says so."""
    made = await _ready(db, "Keyword")
    runbook_id = await _runbook(db, made, made.project.id)
    reader = await _reader(db, made)

    found = await knowledge.search(
        db, reader=reader, project_id=made.project.id, phrase="checkout connections"
    )
    assert found.search == "keyword"
    [row] = found.chunks
    assert (row.source_kind, row.runbook_id, row.title) == ("runbook", runbook_id, "Checkout pool")
    assert row.content == WORDS
    assert row.written_at == NOW
    assert await _search(db, reader, "disk full", made.project.id) == []
    # Alternatives with OR, as the tool's description says.
    assert await _search(db, reader, "disk OR pgbouncer", made.project.id) == [WORDS]


async def test_another_project_s_chunk_is_never_returned_whatever_the_arguments(
    db: AsyncSession,
) -> None:
    """AC42: the project is the ticket's; another project, of this organization or another,
    is the 404 a project that never was gets."""
    made = await _ready(db, "Scoped")
    stranger = await _ready(db, "Stranger")
    sibling = Project(org_id=made.organization.id, slug=f"s-{uuid.uuid4().hex[:8]}", name="Staging")
    db.add(sibling)
    await db.flush()
    await _runbook(db, made, sibling.id)
    await _runbook(db, stranger, stranger.project.id)
    reader = await _reader(db, made)

    assert await _search(db, reader, "checkout connections", made.project.id) == []
    for elsewhere in (sibling.id, stranger.project.id, uuid.uuid4()):
        with pytest.raises(NotFoundError):
            await _search(db, reader, "checkout connections", elsewhere)


async def test_a_recovered_finding_marked_a_false_alarm_drops_out_of_search_at_once(
    db: AsyncSession,
) -> None:
    """AC84: search joins the finding, so the line is excluded before the sweep deletes it."""
    made = await _ready(db, "Facts")
    finding = Finding(
        org_id=made.organization.id,
        project_id=made.project.id,
        scope="service",
        service_name="checkout",
        severity="warning",
        state="closed",
        closed_reason="recovered",
        title="ignored",
        signals=[{"signal": "slower"}],
        started_at=NOW - timedelta(hours=1),
        closed_at=NOW,
    )
    db.add(finding)
    await db.flush()
    line = "service checkout | signal slower | 2026-11-11 13:30 to 2026-11-11 14:30 UTC"
    db.add(
        KnowledgeChunk(
            org_id=made.organization.id,
            project_id=made.project.id,
            source_kind="fact",
            finding_id=finding.id,
            content=line,
            fact_service="checkout",
            fact_signal="slower",
            fact_day=NOW.date(),
        )
    )
    await db.flush()
    reader = await _reader(db, made)
    found = await knowledge.search(db, reader=reader, project_id=made.project.id, phrase="slower")
    assert [(row.source_kind, row.finding_id, row.written_at) for row in found.chunks] == [
        ("fact", finding.id, NOW)
    ]

    await db.execute(
        update(Finding).where(Finding.id == finding.id).values(closed_reason="false_alarm")
    )
    assert await _search(db, reader, "slower", made.project.id) == []


async def test_at_facts_only_the_search_is_refused(db: AsyncSession) -> None:
    """AC68, FR39: the loop never offers it there; the route refuses it as well."""
    made = await _ready(db, "FactsOnly")
    await _runbook(db, made, made.project.id)
    reader = await _reader(db, made)
    await db.execute(
        update(AssistantMessage)
        .where(AssistantMessage.id == reader.message_id)
        .values(data_level="facts")
    )
    with pytest.raises(PermissionDeniedError):
        await _search(db, reader, "checkout", made.project.id)


def _request(authorization: str | None) -> Request:
    headers = [] if authorization is None else [(b"authorization", authorization.encode())]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


async def test_only_a_ticket_reaches_knowledge_search(db: AsyncSession) -> None:
    """FR37, D31: a person or an API token is refused outright; a ticket goes on to the
    ticket check, which an app with none registered refuses too."""
    for presented in (None, "Bearer nvt_not-a-ticket", "Bearer nvk_an-ingest-key"):
        with pytest.raises(PermissionDeniedError):
            await get_ticket_reader(_request(presented), db)
    with pytest.raises(UnauthenticatedError):
        await get_ticket_reader(_request(f"Bearer {ASSISTANT_TICKET_PREFIX}x"), db)


# ------------------------------------------------------------- Step 6: meaning search

MODEL = "fake-embedding"
LATENCY = "## Latency\nCheckout latency climbs when the payment provider lags."
DISK = "## Disk\nThe queue host's volume fills up overnight."


async def _embedded(
    db: AsyncSession, made: ProvisionedTenant, content: str, *, model: str = MODEL, dims: int = 5
) -> uuid.UUID:
    """A runbook chunk with a vector of `model`, as the embedding sweep would write it."""
    view = await runbooks.create_runbook(
        db,
        identity=Identity(user=made.user, organization=made.organization),
        request=NewRunbook(
            project_id=made.project.id, title=f"r {uuid.uuid4().hex[:6]}", content=content
        ),
        now=NOW,
    )
    chunk_id = await db.scalar(
        text("SELECT id FROM knowledge_chunks WHERE runbook_id = :r ORDER BY position LIMIT 1"),
        {"r": view.id},
    )
    await db.execute(
        text(
            "UPDATE knowledge_chunks SET embedding = CAST(:v AS vector), embedded_model = :m, "
            "embedding_due = false WHERE runbook_id = :r"
        ),
        {
            "v": vector_literal(tuple(meaning_vector(content, dimensions=dims))),
            "m": model,
            "r": view.id,
        },
    )
    assert isinstance(chunk_id, uuid.UUID)
    return chunk_id


async def _step(
    db: AsyncSession,
    reader: Reader,
    phrase: str | None,
    *,
    model: str = MODEL,
    dims: int = 5,
) -> uuid.UUID:
    """A knowledge step of the reader's question, holding the phrase's vector as the worker
    stores it; `phrase` None leaves it with none."""
    taken = await db.scalar(
        text("SELECT count(*) FROM assistant_steps WHERE message_id = :m"),
        {"m": reader.message_id},
    )
    step = AssistantStep(
        org_id=reader.org_id,
        message_id=reader.message_id,
        position=int(taken or 0) + 1,
        tool="search_knowledge",
        arguments={},
    )
    db.add(step)
    await db.flush()
    if phrase is not None:
        await db.execute(
            text(
                "UPDATE assistant_steps SET query_embedding = CAST(:v AS vector), "
                "query_embedding_model = :m WHERE id = :s"
            ),
            {
                "v": vector_literal(tuple(meaning_vector(phrase, dimensions=dims))),
                "m": model,
                "s": step.id,
            },
        )
    return step.id


async def test_meaning_search_finds_the_runbook_keywords_miss_and_says_hybrid(
    db: AsyncSession,
) -> None:
    """Step 6's judgeable claim: "why is it slow?" finds a runbook that says "latency" with
    embeddings on, and not with them off (AC41, FR29)."""
    made = await _ready(db, "Meaning")
    latency = await _embedded(db, made, LATENCY)
    await _embedded(db, made, DISK)
    reader = await _reader(db, made)

    assert await _search(db, reader, "why is it slow", made.project.id) == []
    step = await _step(db, reader, "why is it slow")
    found = await knowledge.search(
        db, reader=reader, project_id=made.project.id, phrase="why is it slow", step_id=step
    )
    assert found.search == "hybrid"
    assert found.chunks[0].id == latency and found.chunks[0].content == LATENCY


async def test_two_models_vectors_are_never_compared(db: AsyncSession) -> None:
    """AC41, FR31: the step's own model filters the chunks, even of another size."""
    made = await _ready(db, "TwoModels")
    first = await _embedded(db, made, LATENCY, model="model-a", dims=5)
    second = await _embedded(db, made, DISK, model="model-b", dims=3)
    reader = await _reader(db, made)

    for model, dims, expected in (("model-a", 5, first), ("model-b", 3, second)):
        step = await _step(db, reader, "slow disk", model=model, dims=dims)
        found = await knowledge.search(
            db, reader=reader, project_id=made.project.id, phrase="nothing matches", step_id=step
        )
        assert [row.id for row in found.chunks] == [expected]
    # A step of a third model finds no vector to compare, and keyword search stands.
    step = await _step(db, reader, "slow", model="model-c")
    found = await knowledge.search(
        db, reader=reader, project_id=made.project.id, phrase="volume", step_id=step
    )
    assert (found.search, [row.id for row in found.chunks]) == ("keyword", [second])


async def test_another_question_s_step_is_refused(db: AsyncSession) -> None:
    """Design, section 6: the route checks the step belongs to the ticket's message."""
    made = await _ready(db, "OtherStep")
    await _embedded(db, made, LATENCY)
    reader = await _reader(db, made)
    earlier = await _step(db, reader, "slow")
    # The same person and organization, another question's ticket.
    later = Reader(
        org_id=reader.org_id, project_scope=reader.project_scope, message_id=uuid.uuid4()
    )
    with pytest.raises(PermissionDeniedError):
        await knowledge.search(
            db, reader=later, project_id=made.project.id, phrase="slow", step_id=earlier
        )
    with pytest.raises(PermissionDeniedError):
        await knowledge.search(
            db, reader=reader, project_id=made.project.id, phrase="slow", step_id=uuid.uuid4()
        )


async def test_a_step_with_no_vector_searches_by_keyword(db: AsyncSession) -> None:
    made = await _ready(db, "NoVector")
    await _embedded(db, made, LATENCY)
    reader = await _reader(db, made)
    step = await _step(db, reader, None)
    found = await knowledge.search(
        db, reader=reader, project_id=made.project.id, phrase="latency", step_id=step
    )
    assert (found.search, len(found.chunks)) == ("keyword", 1)


async def test_hybrid_results_merge_by_reciprocal_rank_and_either_side_stands(
    db: AsyncSession,
) -> None:
    """AC90: hybrid search merges, and stands when either side is empty."""
    made = await _ready(db, "Merged")
    latency = await _embedded(db, made, LATENCY)
    disk = await _embedded(db, made, DISK)
    reader = await _reader(db, made)
    step = await _step(db, reader, "slow")

    # Keyword finds the disk runbook, meaning ranks latency first: both stand, merged.
    found = await knowledge.search(
        db, reader=reader, project_id=made.project.id, phrase="volume", step_id=step
    )
    assert found.search == "hybrid"
    assert [row.id for row in found.chunks] == knowledge.merged([disk], [latency, disk])
    assert {row.id for row in found.chunks} == {latency, disk}

    a, b, c, d, e, f = (uuid.uuid4() for _ in range(6))
    assert knowledge.merged([a, b], []) == [a, b]
    assert knowledge.merged([], [c, d]) == [c, d]
    # In both lists beats first in one; the merge keeps the top five.
    # c: 1/63 + 1/61; a: 1/61 + 1/65.
    assert knowledge.merged([a, b, c], [c, d, e, f, a])[:2] == [c, a]
    assert len(knowledge.merged([a, b, c], [d, e, f])) == 5


async def test_a_false_alarm_s_fact_line_is_left_out_of_meaning_search_too(
    db: AsyncSession,
) -> None:
    """AC84 on the meaning side: search joins the finding before the sweep deletes the line."""
    made = await _ready(db, "MeaningFacts")
    finding = Finding(
        org_id=made.organization.id,
        project_id=made.project.id,
        scope="service",
        service_name="checkout",
        severity="warning",
        state="closed",
        closed_reason="false_alarm",
        title="ignored",
        signals=[{"signal": "slower"}],
        started_at=NOW - timedelta(hours=1),
        closed_at=NOW,
    )
    db.add(finding)
    await db.flush()
    line = "service checkout | signal slower | latency up"
    chunk = KnowledgeChunk(
        org_id=made.organization.id,
        project_id=made.project.id,
        source_kind="fact",
        finding_id=finding.id,
        content=line,
        fact_service="checkout",
        fact_signal="slower",
        fact_day=NOW.date(),
    )
    db.add(chunk)
    await db.flush()
    await db.execute(
        text(
            "UPDATE knowledge_chunks SET embedding = CAST(:v AS vector), embedded_model = :m "
            "WHERE id = :c"
        ),
        {"v": vector_literal(tuple(meaning_vector(line))), "m": MODEL, "c": chunk.id},
    )
    reader = await _reader(db, made)
    step = await _step(db, reader, "slow")
    found = await knowledge.search(
        db, reader=reader, project_id=made.project.id, phrase="nothing here", step_id=step
    )
    assert found.chunks == []
