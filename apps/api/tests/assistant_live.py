"""Shared set-up for chat's live API suites (F4 plan T2.1, T2.2, T2.6): a tenant with AI and
chat on, consented, inside a transaction that is rolled back, so **each test owns its rows**
and the stack's own sweeps and workers never see them (a job enqueued here is never
committed, and a wake is sent only on commit).
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from nexovue_platform.ai.connection import consent_fingerprint
from nexovue_platform.assistant_records import ANSWER_QUESTION_TASK, CHAT_DISCLOSURE_VERSION
from nexovue_platform.checks.sealing import ConfiguredKeys, generate_key
from nexovue_platform.deployment import SELF_HOSTED
from nexovue_platform.evidence.model_facts import SCHEMA_VERSION
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.ai.service import Availability
from nexovue_api.data.postgres.models import AIConnection, AssistantSettings, UsageDaily
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
NOW: Final = datetime(2026, 11, 11, 14, 30, tzinfo=UTC)
BASE_URL: Final = "https://provider.example/v1"
PASSWORD: Final = "correct horse battery staple"
# A fresh sealing key for each run; never a real one.
AI_KEY: Final = generate_key()
KEYS: Final = ConfiguredKeys(AI_KEY, "")
AVAILABLE: Final = Availability(
    deployment=SELF_HOSTED, keys=KEYS, insight_replicas=1, app_base_url="http://localhost"
)


def reachable() -> bool:
    parts = urlsplit(POSTGRES_DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


def require_postgres(suite: str) -> None:
    require_stack(
        reachable=reachable(),
        suite=suite,
        remedy="Postgres is not reachable, and `setup` must have run (`just dev`)",
    )


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


async def tenant(db: AsyncSession, name: str = "Chat") -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name=name,
        email=f"{name.lower()}-{uuid.uuid4()}@example.test",
        password=PASSWORD,
    )


async def reported(db: AsyncSession, made: ProvisionedTenant) -> None:
    """The project has sent telemetry once, as the ingest meter records it."""
    db.add(UsageDaily(org_id=made.organization.id, project_id=made.project.id, day=NOW.date()))
    await db.flush()


async def chat_on(
    db: AsyncSession,
    made: ProvisionedTenant,
    *,
    daily_limit: int = 50,
    data_level: str = "facts_and_text",
) -> AIConnection:
    """AI on with an owner's consent, and chat on with its own, as `/settings/ai` leaves it."""
    org_id, owner = made.organization.id, made.user.id
    fingerprint = consent_fingerprint(
        wire_format="openai_chat", base_url=BASE_URL, region=None, key_id=None
    )
    connection = AIConnection(
        org_id=org_id,
        wire_format="openai_chat",
        base_url=BASE_URL,
        model="m",
        monthly_token_limit=10_000_000,
        enabled=True,
        consented_by=owner,
        consented_at=NOW,
        consented_base_url=BASE_URL,
        consented_fingerprint=fingerprint,
        consented_schema=SCHEMA_VERSION,
    )
    db.add(connection)
    db.add(
        AssistantSettings(
            org_id=org_id,
            enabled=True,
            daily_question_limit=daily_limit,
            data_level=data_level,
            mask_emails=True,
            consented_by=owner,
            consented_at=NOW,
            consented_fingerprint=fingerprint,
            consented_disclosure_version=CHAT_DISCLOSURE_VERSION,
            consented_data_level=data_level,
        )
    )
    await db.flush()
    return connection


async def held_from_the_stack(db: AsyncSession, message_id: uuid.UUID) -> None:
    """Remove a question's job before its transaction commits, for a suite that commits the
    question and plays the worker itself: the running stack's insight worker would otherwise
    take the job, end the question in its own way, and with it the ticket."""
    removed = await db.scalars(
        text(
            "DELETE FROM jobs WHERE task = :task AND kwargs->>'message_id' = :message_id "
            "RETURNING id"
        ),
        {"task": ANSWER_QUESTION_TASK, "message_id": str(message_id)},
    )
    assert len(removed.all()) == 1, "the question's job was not found"
