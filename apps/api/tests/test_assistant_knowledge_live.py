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
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from nexovue_api.apitokens.tokens import ASSISTANT_TICKET_PREFIX
from nexovue_api.assistant import knowledge, service
from nexovue_api.assistant.schemas import AskContext
from nexovue_api.auth.dependencies import Identity, Reader, get_ticket_reader
from nexovue_api.data.postgres.models import (
    AssistantMessage,
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
