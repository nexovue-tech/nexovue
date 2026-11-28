"""F4 plan T5.4, T6.2 and T7.2: knowledge search, against real Postgres (AC38, AC41, AC42, AC68,
AC83, AC84; FR29, FR37).

The search is read as the ticket's reader, its organization and project the ticket's own;
each test runs inside a transaction that is rolled back, so it owns its rows. Reading it over
real HTTP with a ticket, and the 404 for another project, is `test_assistant_tools_live.py`'s,
which covers every catalog tool. Clocks are pinned (`NOW`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from assistant_live import AVAILABLE, KEYS, NOW, chat_on, db, require_postgres, tenant
from nexovue_platform.ai.embed import vector_literal
from nexovue_platform.ai.testing import meaning_vector
from sqlalchemy import delete, select, text, update
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
    FindingNote,
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


# ------------------------------------------------------------- Step 7: notes and cases

NOTE = "Fixed checkout by raising the pool to 40 and restarting pgbouncer."


def _case_finding(
    made: ProvisionedTenant,
    *,
    closed_reason: str | None = "recovered",
    resolution: str | None = None,
    closed_at: datetime | None = NOW,
) -> Finding:
    """A finding, closed or open, holding a resolved case when `resolution` is given."""
    resolved = resolution is not None
    return Finding(
        org_id=made.organization.id,
        project_id=made.project.id,
        scope="service",
        service_name="checkout",
        severity="warning",
        state="closed" if closed_at is not None else "open",
        closed_reason=closed_reason if closed_at is not None else None,
        title="ignored",
        signals=[{"signal": "slower"}],
        started_at=(closed_at or NOW) - timedelta(hours=1),
        closed_at=closed_at,
        case_state="resolved" if resolved else None,
        case_resolution=resolution,
        case_opened_at=NOW - timedelta(minutes=50) if resolved else None,
        case_activity_at=NOW if resolved else None,
        case_resolved_at=NOW if resolved else None,
    )


async def _note(
    db: AsyncSession, made: ProvisionedTenant, finding: Finding, body: str = NOTE
) -> tuple[FindingNote, uuid.UUID]:
    """A note on the case, and its chunk as the knowledge sweep writes it."""
    note = FindingNote(
        org_id=made.organization.id,
        finding_id=finding.id,
        author_id=made.user.id,
        body=body,
        created_at=NOW - timedelta(days=3),
    )
    db.add(note)
    await db.flush()
    chunk = KnowledgeChunk(
        org_id=made.organization.id,
        project_id=finding.project_id,
        source_kind="note",
        note_id=note.id,
        content=body,
    )
    db.add(chunk)
    await db.flush()
    return note, chunk.id


def _fact(made: ProvisionedTenant, finding: Finding, day: int) -> KnowledgeChunk:
    return KnowledgeChunk(
        org_id=made.organization.id,
        project_id=made.project.id,
        source_kind="fact",
        finding_id=finding.id,
        content=f"service checkout | signal slower | day {day} | checkout closed: recovered",
        fact_service="checkout",
        fact_signal="slower",
        fact_day=(NOW - timedelta(days=day)).date(),
    )


async def test_a_note_is_found_with_its_date_and_its_case_and_never_its_author(
    db: AsyncSession,
) -> None:
    """Step 7's judgeable claim (UR9, FR28): "how did we fix this last time?" finds the
    note, labelled the team's (its row's kind), with the date it was written and its case;
    the author's name is not in the row the model reads."""
    made = await _ready(db, "Notes")
    finding = _case_finding(made, resolution="resolved")
    db.add(finding)
    await db.flush()
    note, chunk_id = await _note(db, made, finding)
    reader = await _reader(db, made)

    found = await knowledge.search(db, reader=reader, project_id=made.project.id, phrase="pool")
    [row] = found.chunks
    assert (row.id, row.source_kind, row.content, row.finding_id) == (
        chunk_id,
        "note",
        NOTE,
        finding.id,
    )
    assert (row.written_at, row.title, row.runbook_id) == (note.created_at, None, None)
    assert row.service_name == "checkout"
    assert made.user.email not in row.model_dump_json()


async def test_deleting_a_note_takes_it_out_of_search_in_the_same_statement(
    db: AsyncSession,
) -> None:
    """AC38, BR16: the chunk cascades from its note; the next search finds nothing."""
    made = await _ready(db, "NoteGone")
    finding = _case_finding(made, resolution="resolved")
    db.add(finding)
    await db.flush()
    note, chunk_id = await _note(db, made, finding)
    reader = await _reader(db, made)
    assert await _search(db, reader, "pool", made.project.id) == [NOTE]

    await db.execute(delete(FindingNote).where(FindingNote.id == note.id))
    assert await _search(db, reader, "pool", made.project.id) == []
    assert await db.scalar(select(KnowledgeChunk.id).where(KnowledgeChunk.id == chunk_id)) is None


async def test_a_note_outranks_a_hundred_fact_lines_about_the_same_service(
    db: AsyncSession,
) -> None:
    """AC83, FR29: what a person wrote ranks above nexovue's composed lines, whatever the
    text ranking says; here every fact line says "checkout" twice and the note once."""
    made = await _ready(db, "Outranks")
    findings = [_case_finding(made, closed_at=NOW - timedelta(days=day)) for day in range(100)]
    db.add_all(findings)
    await db.flush()
    db.add_all(_fact(made, finding, day) for day, finding in enumerate(findings))
    noted = _case_finding(made, resolution="resolved")
    db.add(noted)
    await db.flush()
    _, note_chunk = await _note(db, made, noted, "Raised the pool. checkout is fine now.")
    reader = await _reader(db, made)

    found = await knowledge.search(db, reader=reader, project_id=made.project.id, phrase="checkout")
    assert found.chunks[0].id == note_chunk
    assert [row.source_kind for row in found.chunks[1:]] == ["fact"] * (len(found.chunks) - 1)

    # The merge keeps the order: a fact line scoring higher by rank still comes after.
    fact_a, fact_b, person = (uuid.uuid4() for _ in range(3))
    merged = knowledge.merged(
        [fact_a, fact_b, person], [fact_a, fact_b, person], facts=frozenset({fact_a, fact_b})
    )
    assert merged == [person, fact_a, fact_b]


async def test_a_resolved_case_s_line_stays_searchable_after_it_stops_being_recovered(
    db: AsyncSession,
) -> None:
    """FR28, design section 6: a fact line is kept while its finding is recovered or a
    person resolved its case; a case ended as a false alarm keeps none (AC84)."""
    made = await _ready(db, "CaseLines")
    resolved = _case_finding(made, closed_reason="service_gone", resolution="resolved")
    tracker = _case_finding(made, closed_at=None, resolution="tracker_closed")
    false_alarm = _case_finding(made, closed_reason="false_alarm", resolution="false_alarm")
    idle = _case_finding(made, closed_reason="service_gone", resolution="no_activity")
    db.add_all([resolved, tracker, false_alarm, idle])
    await db.flush()
    db.add_all(_fact(made, finding, day) for day, finding in enumerate([resolved, tracker]))
    db.add_all(
        _fact(made, finding, day) for day, finding in enumerate([false_alarm, idle], start=5)
    )
    await db.flush()
    reader = await _reader(db, made)

    found = await knowledge.search(db, reader=reader, project_id=made.project.id, phrase="slower")
    assert {row.finding_id for row in found.chunks} == {resolved.id, tracker.id}
    assert next(row for row in found.chunks if row.finding_id == resolved.id).written_at == NOW


async def test_by_meaning_too_a_note_comes_before_fact_lines_nearer_the_phrase(
    db: AsyncSession,
) -> None:
    """FR29 on the meaning side: with keywords finding nothing, the meaning list stands whole,
    and still puts people's words first, though 25 fact lines sit nearer the phrase."""
    made = await _ready(db, "MeaningNotes")
    findings = [_case_finding(made, closed_at=NOW - timedelta(days=day)) for day in range(25)]
    db.add_all(findings)
    await db.flush()
    facts = [_fact(made, finding, day) for day, finding in enumerate(findings)]
    db.add_all(facts)
    noted = _case_finding(made, resolution="resolved")
    db.add(noted)
    await db.flush()
    _, note_chunk = await _note(db, made, noted, "The disk filled; we cleared the volume.")
    for chunk_id, words in [(fact.id, "slow latency") for fact in facts] + [
        (note_chunk, "disk volume")
    ]:
        await db.execute(
            text(
                "UPDATE knowledge_chunks SET embedding = CAST(:v AS vector), "
                "embedded_model = :m, embedding_due = false WHERE id = :c"
            ),
            {"v": vector_literal(tuple(meaning_vector(words))), "m": MODEL, "c": chunk_id},
        )
    reader = await _reader(db, made)
    step = await _step(db, reader, "why is it slow")

    found = await knowledge.search(
        db, reader=reader, project_id=made.project.id, phrase="nothing matches", step_id=step
    )
    assert found.search == "hybrid"
    assert found.chunks[0].id == note_chunk
