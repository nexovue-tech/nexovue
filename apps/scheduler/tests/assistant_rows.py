"""F4: real tenants and questions for chat's live suites, made as the API would store them.

Shared by the account spike, the budget suite, the kill tests and the expiry suite, so each
makes its rows one way. **Tests own their rows:** `made` deletes every organization a test
made, and every question here has a clock far in the future unless the test is about it.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from assistant_fakes import sealed_ticket
from insight_login import DEV_INSIGHT_PASSWORD, insight_dsn
from nexovue_platform.ai.connection import consent_fingerprint
from nexovue_platform.assistant_records import CHAT_DISCLOSURE_VERSION
from nexovue_platform.evidence.model_facts import SCHEMA_VERSION
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from nexovue_api import insight_account
from nexovue_api.signup.service import provision_tenant

DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
INSIGHT_ASYNC_DSN: Final = insight_dsn(DSN).replace("postgresql://", "postgresql+asyncpg://", 1)
BASE_URL: Final = "https://provider.example/v1"
LIMIT: Final = 10_000_000
RESERVE: Final = 60_000


def reachable() -> bool:
    parts = urlsplit(DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


@pytest.fixture
async def install(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    monkeypatch.setenv(insight_account.PASSWORD_VARIABLE, DEV_INSIGHT_PASSWORD)
    said: list[str] = []
    assert await insight_account.provision(ASYNC_DSN, say=said.append), said
    built = create_async_engine(ASYNC_DSN)
    yield built
    await built.dispose()


@pytest.fixture
async def as_insight(install: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    built = create_async_engine(INSIGHT_ASYNC_DSN, pool_size=2, max_overflow=0)
    yield built
    await built.dispose()


@dataclass(frozen=True)
class Asked:
    org_id: str
    user_id: str
    project_id: str
    message_id: str


@pytest.fixture
async def made(install: AsyncEngine) -> AsyncIterator[list[str]]:
    orgs: list[str] = []
    yield orgs
    async with install.begin() as db:
        for org_id in orgs:
            await db.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": org_id})


async def run(install: AsyncEngine, sql: str, **bound: Any) -> Any:
    async with install.begin() as db:
        result = await db.execute(text(sql), bound)
        return result.first() if result.returns_rows else None


async def ask(
    install: AsyncEngine,
    made: list[str],
    *,
    state: str = "queued",
    reserved: int = 0,
    hard_ends_in: timedelta = timedelta(hours=1),
    limit: int = LIMIT,
) -> Asked:
    """A tenant with AI and chat on, consented, and one question as the API would store it."""
    async with AsyncSession(install, expire_on_commit=False) as session, session.begin():
        tenant = await provision_tenant(
            session,
            organization_name="Chat account suite",
            email=f"chat-{uuid.uuid4().hex[:10]}@example.test",
            password="correct horse battery staple",
        )
    org_id, user_id = str(tenant.organization.id), str(tenant.user.id)
    made.append(org_id)
    project_id = str(
        (await run(install, "SELECT id FROM projects WHERE org_id = :o LIMIT 1", o=org_id))[0]
    )
    fingerprint = consent_fingerprint(
        wire_format="openai_chat", base_url=BASE_URL, region=None, key_id=None
    )
    await run(
        install,
        "INSERT INTO ai_connections (org_id, wire_format, base_url, model, monthly_token_limit, "
        "enabled, consented_by, consented_at, consented_base_url, consented_fingerprint, "
        "consented_schema) VALUES (:o, 'openai_chat', :url, 'm', :limit, true, :u, now(), :url, "
        ":fp, :schema)",
        o=org_id,
        u=user_id,
        url=BASE_URL,
        limit=limit,
        fp=fingerprint,
        schema=SCHEMA_VERSION,
    )
    await run(
        install,
        "INSERT INTO assistant_settings (org_id, enabled, data_level, consented_by, consented_at, "
        "consented_fingerprint, consented_disclosure_version, consented_data_level) VALUES "
        "(:o, true, 'facts_and_text', :u, now(), :fp, :v, 'facts_and_text')",
        o=org_id,
        u=user_id,
        fp=fingerprint,
        v=CHAT_DISCLOSURE_VERSION,
    )
    if reserved:
        await run(
            install,
            "INSERT INTO ai_usage_monthly (org_id, month, reserved_tokens) "
            "VALUES (:o, date_trunc('month', now())::date, :r)",
            o=org_id,
            r=reserved,
        )
    first = Asked(org_id, user_id, project_id, "")
    return await ask_again(
        install, first, state=state, reserved=reserved, hard_ends_in=hard_ends_in
    )


async def ask_again(
    install: AsyncEngine,
    asked: Asked,
    *,
    user_id: str | None = None,
    state: str = "queued",
    reserved: int = 0,
    hard_ends_in: timedelta = timedelta(hours=1),
    created_ago: timedelta = timedelta(0),
    conversation_id: str | None = None,
) -> Asked:
    """Another question in the same organization and project, by `user_id` (the tenant's
    owner unless named), in `conversation_id` or a new conversation. `reserved` is the
    message's own hold only."""
    asker = user_id or asked.user_id
    conversation = (
        conversation_id
        or (
            await run(
                install,
                "INSERT INTO assistant_conversations (org_id, user_id, project_id, title) "
                "VALUES (:o, :u, :p, 'q') RETURNING id",
                o=asked.org_id,
                u=asker,
                p=asked.project_id,
            )
        )[0]
    )
    message_id = str(uuid.uuid4())
    in_flight = state in ("queued", "running")
    await run(
        install,
        "INSERT INTO assistant_messages (id, org_id, project_id, conversation_id, user_id, "
        "question, context, history, data_level, state, reserved_tokens, reserved_month, "
        "ticket_hash, sealed_ticket, call_seconds, queue_expires_at, deadline_at, hard_ends_at, "
        "created_at) "
        "VALUES (:id, :o, :p, :c, :u, 'What is failing?', '{}', '[]', 'facts_and_text', :state, "
        "CAST(:reserved AS bigint), "
        "CASE WHEN CAST(:reserved AS bigint) > 0 THEN date_trunc('month', now())::date END, "
        ":hash, "
        ":sealed, 30, now() + :hard, now() + :hard, now() + :hard, now() - CAST(:ago AS interval))",
        id=message_id,
        o=asked.org_id,
        p=asked.project_id,
        c=conversation,
        u=asker,
        state=state,
        reserved=reserved,
        hash=f"hash-{message_id}" if in_flight else None,
        sealed=sealed_ticket(message_id) if in_flight else None,
        hard=hard_ends_in,
        ago=created_ago,
    )
    return Asked(asked.org_id, asker, asked.project_id, message_id)


async def member(install: AsyncEngine, asked: Asked) -> str:
    """Another person in the organization, so a second question can be in flight at once."""
    user_id = str(uuid.uuid4())
    await run(
        install,
        "INSERT INTO users (id, org_id, email, name, role, password_hash) "
        "VALUES (:id, :o, :e, 'Member', 'member', 'x')",
        id=user_id,
        o=asked.org_id,
        e=f"m-{user_id[:12]}@example.test",
    )
    return user_id


async def message(install: AsyncEngine, message_id: str) -> Any:
    return await run(install, "SELECT * FROM assistant_messages WHERE id = :id", id=message_id)
