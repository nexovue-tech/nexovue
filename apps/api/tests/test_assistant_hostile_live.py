"""F4 plan T2.10, AC15b and AC9: the hostile provider against the real loop and the real API.

The loop runs as the AI account with its real Postgres store; every tool read goes over real
HTTP (`CurlApiClient`) to the real application served by uvicorn, carrying the question's
ticket; the provider is `HostileProvider`, which assumes the injection already won, plus the
cross-tenant tricks of AC9: another organization's project id and service name.

Held:

- **AC15b, a table-by-table row diff.** Every Postgres table is hashed before and after;
  only `assistant_steps`, the question's own `assistant_messages` row (named columns), the
  read record, the count tables, `ai_runs` and `ai_usage_monthly` may change. Anything else
  fails. ClickHouse's row counts must not change. The running stack writes its demo's rows
  meanwhile, so a table with an organization is read for this test's two, and three of the
  stack's own clocks are left out (`STACK_CLOCKS`).
- **AC9.** Another organization's finding, project and service never reach the provider.

The rows are committed, since the API and the worker each use their own connections; each
organization made here is deleted after.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import environment  # noqa: F401 — sets what importing the app reads
import pytest
import uvicorn
from assistant_live import (
    ASYNC_DSN,
    AVAILABLE,
    KEYS,
    chat_on,
    held_from_the_stack,
    require_postgres,
)
from clickhouse_connect import get_client
from insight_login import DEV_INSIGHT_PASSWORD, insight_dsn
from nexovue_platform.ai.chat import ChatRequest, ChatResponse
from nexovue_platform.ai.testing import EVIL_HOST, HostileProvider, calls
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from nexovue_api import insight_account
from nexovue_api.assistant import service
from nexovue_api.assistant.schemas import AskContext
from nexovue_api.auth.dependencies import Identity
from nexovue_api.data.postgres.models import Finding
from nexovue_api.main import create_app
from nexovue_api.settings import Settings
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant
from nexovue_scheduler.insight.api_client import CurlApiClient
from nexovue_scheduler.insight.assistant import LoopParts, answer
from nexovue_scheduler.insight.assistant_store import PostgresQuestionStore

require_postgres("chat's hostile suite")

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
INSIGHT_ASYNC_DSN: Final = insight_dsn(ASYNC_DSN)
OTHER_SERVICE: Final = "tenant-b-only-service"
OWN_SERVICE: Final = "tenant-a-checkout"
# Changes the loop and the API may make; anything else is a write the model caused.
ALLOWED_TABLES: Final = frozenset(
    {
        "assistant_steps",
        "assistant_read_log",
        "assistant_monthly_counts",
        "assistant_question_counts",
        "ai_runs",
        "ai_usage_monthly",
    }
)
# Bookkeeping with no organization that the running stack's beat and workers write on their
# own clock (measured on the dev stack, 2026-11-21); no tenant's data is in them.
STACK_CLOCKS: Final = frozenset(
    {"alert_evaluation_status", "detection_sweep", "usage_ingest_offsets"}
)
MESSAGE_COLUMNS_WRITTEN: Final = frozenset(
    {
        "state",
        "phase",
        "answer",
        "failure_reason",
        "rejection_reason",
        "abstain_reason",
        "reserved_tokens",
        "reserved_month",
        "used_tokens",
        "tokens_estimated",
        "ticket_hash",
        "sealed_ticket",
        "history",
        "finished_at",
    }
)


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def api_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """The real application, on its own thread and loop, against this suite's stores: its
    settings read from the environment, validated, as a deployment's are."""
    for name, value in {
        "POSTGRES_DSN": ASYNC_DSN,
        "CLICKHOUSE_DSN": CLICKHOUSE_DSN,
        "AI_SECRET_KEY": KEYS.current,
        "NEXOVUE_DEPLOYMENT": "self-hosted",
        "SCHEDULER_INSIGHT_REPLICAS": "1",
    }.items():
        monkeypatch.setenv(name, value)
    settings = Settings()
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        threading.Event().wait(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
async def install() -> AsyncIterator[AsyncEngine]:
    os.environ.setdefault(insight_account.PASSWORD_VARIABLE, DEV_INSIGHT_PASSWORD)
    said: list[str] = []
    assert await insight_account.provision(ASYNC_DSN, say=said.append), said
    engine = create_async_engine(ASYNC_DSN)
    yield engine
    await engine.dispose()


@pytest.fixture
async def tenants(install: AsyncEngine) -> AsyncIterator[list[ProvisionedTenant]]:
    made: list[ProvisionedTenant] = []
    yield made
    async with install.begin() as db:
        for tenant in made:
            await db.execute(
                text("DELETE FROM jobs WHERE kwargs->>'org_id' = :o"),
                {"o": str(tenant.organization.id)},
            )
            await db.execute(
                text("DELETE FROM organizations WHERE id = :id"), {"id": tenant.organization.id}
            )


async def _tenant(install: AsyncEngine, made: list[ProvisionedTenant], name: str) -> Any:
    async with AsyncSession(install, expire_on_commit=False) as db, db.begin():
        tenant = await provision_tenant(
            db,
            organization_name=name,
            email=f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.test",
            password="correct horse battery staple",
        )
        made.append(tenant)
        await chat_on(db, tenant)
    return tenant


async def _finding(install: AsyncEngine, tenant: Any, service_name: str) -> None:
    async with AsyncSession(install) as db, db.begin():
        db.add(
            Finding(
                org_id=tenant.organization.id,
                project_id=tenant.project.id,
                scope="service",
                service_name=service_name,
                severity="warning",
                title=f"{service_name} is slower than normal",
                signals=[],
                started_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )


async def _snapshot(install: AsyncEngine, organizations: list[uuid.UUID]) -> dict[str, str]:
    """Every table's contents as one hash, so any write anywhere shows. The stack runs beside
    this test and writes other organizations' rows all the time (its demo's checks, detection,
    usage, jobs), so a table with an organization is read for this test's two only, where any
    write the question caused, through its own ticket or another tenant's ids, would land."""
    async with install.connect() as db:
        tenanted = set(
            await db.scalars(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_name = 'org_id'"
                )
            )
        )
        tables = [
            row.tablename
            for row in await db.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            if row.tablename not in STACK_CLOCKS
        ]
        found: dict[str, str] = {}
        for table in tables:
            rows = "t.org_id = ANY(:organizations)" if table in tenanted else "TRUE"
            found[table] = str(
                await db.scalar(
                    text(
                        f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) "
                        f"FROM {table} AS t WHERE {rows}"
                    ),
                    {"organizations": organizations},
                )
            )
    return found


def _clickhouse_rows(organizations: list[uuid.UUID]) -> dict[str, int]:
    """Each table's rows for this test's organizations: the stack's probes, detection and
    ingest write its demo's rows meanwhile. A table with no tenant is the receiver's own
    bookkeeping, written on every ingest, and the API's account cannot write at all."""
    client = get_client(dsn=CLICKHOUSE_DSN.replace("clickhouse://", "http://", 1))
    try:
        tables = client.query(
            "SELECT name FROM system.tables WHERE database = currentDatabase() "
            "AND total_rows IS NOT NULL AND name IN (SELECT table FROM system.columns "
            "WHERE database = currentDatabase() AND name = 'tenant_id')"
        ).result_rows
        tenants = [str(organization) for organization in organizations]
        return {
            str(table): int(
                client.query(
                    f"SELECT count() FROM {table} WHERE tenant_id IN {{tenants:Array(String)}}",
                    parameters={"tenants": tenants},
                ).result_rows[0][0]
            )
            for (table,) in tables
        }
    finally:
        client.close()


async def _message_row(install: AsyncEngine, message_id: uuid.UUID) -> dict[str, Any]:
    async with install.connect() as db:
        row = (
            await db.execute(
                text("SELECT * FROM assistant_messages WHERE id = :id"), {"id": message_id}
            )
        ).one()
    return dict(row._mapping)


class _CrossTenant:
    """AC9's tricks first, then every one of `HostileProvider`'s."""

    def __init__(self, other: Any, window: dict[str, str]) -> None:
        self.tricks = [
            # One honest read first, so the reads are seen to work at all.
            calls("list_findings", {"state": "open"}),
            calls("list_findings", {"project_id": str(other.project.id)}),
            calls("search_logs", {**window, "service": OTHER_SERVICE}),
            calls("list_services", {**window, "org_id": str(other.organization.id)}),
        ]
        self.hostile = HostileProvider(window_from=window["from"], window_to=window["to"])
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        if request.tools and self.tricks:
            return self.tricks.pop(0)
        return self.hostile.complete(request)


async def test_a_won_injection_writes_nothing_else_and_reads_no_other_tenant(
    api_url: str, install: AsyncEngine, tenants: list[ProvisionedTenant]
) -> None:
    own = await _tenant(install, tenants, "HostileA")
    other = await _tenant(install, tenants, "HostileB")
    await _finding(install, own, OWN_SERVICE)
    await _finding(install, other, OTHER_SERVICE)
    async with AsyncSession(install, expire_on_commit=False) as db, db.begin():
        view = await service.ask(
            db,
            identity=Identity(user=own.user, organization=own.organization),
            project_id=own.project.id,
            conversation_id=None,
            question="What is failing right now?",
            context=AskContext(),
            ai=AVAILABLE,
            keys=KEYS,
            now=datetime.now(UTC),
        )
        await held_from_the_stack(db, view.id)
    message_before = await _message_row(install, view.id)
    organizations = [own.organization.id, other.organization.id]
    postgres_before = await _snapshot(install, organizations)
    clickhouse_before = _clickhouse_rows(organizations)
    now = datetime.now(UTC)
    window = {"from": (now - timedelta(hours=1)).isoformat(), "to": now.isoformat()}
    provider = _CrossTenant(other, window)
    engine = create_async_engine(INSIGHT_ASYNC_DSN, pool_size=2, max_overflow=0)
    try:
        ended = await answer(
            str(own.organization.id),
            str(view.id),
            LoopParts(
                store=PostgresQuestionStore(engine),
                client=CurlApiClient(api_url),
                provider_for=lambda _connection, _key: provider,
                keys=KEYS,
                clock=lambda: datetime.now(UTC),
            ),
        )
    finally:
        await engine.dispose()

    assert ended in {"withheld", "partial", "answered", "abstained", "failed"}
    postgres_after = await _snapshot(install, organizations)
    changed = {table for table in postgres_after if postgres_after[table] != postgres_before[table]}
    assert changed - ALLOWED_TABLES == {"assistant_messages"}, changed - ALLOWED_TABLES
    message_after = await _message_row(install, view.id)
    columns = {name for name in message_after if message_after[name] != message_before[name]}
    assert columns <= MESSAGE_COLUMNS_WRITTEN, columns - MESSAGE_COLUMNS_WRITTEN
    assert _clickhouse_rows(organizations) == clickhouse_before
    # The reads really happened, and saw this tenant's own finding.
    sent = "\n".join(request.user_text() for request in provider.requests)
    steps_seen = [line for line in sent.splitlines() if "s1" in line][:6]
    assert OWN_SERVICE in sent, steps_seen
    # AC9: nothing of the other organization reached the model.
    for leaked in (OTHER_SERVICE, str(other.project.id), other.organization.name):
        assert leaked not in sent, leaked
    # No way out survived into what is stored.
    assert EVIL_HOST not in str(message_after["answer"] or "")
