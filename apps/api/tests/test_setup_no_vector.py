"""F4 plan T6.1, AC43: on an account that cannot create `vector`, `setup` finishes with a clear
warning, and chat searches by keyword only; Steps 2 to 5 need nothing of the extension.

The test makes a database of its own from `template0`, which holds no `vector`, owned by an
account that is not a superuser (pgvector's control file does not mark it trusted, so only a
superuser may create it), runs the real `setup` against it as that account, and reads what
the install would have: every migration applied, no vector columns, a warning said, the AI
account's grants still applied whole, the settings section saying "keyword only", and
knowledge search answering by keyword even when the worker names a step. It drops the
database and the account at the end, so it owns everything it touches.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import pytest
from assistant_live import (
    ASYNC_DSN,
    AVAILABLE,
    KEYS,
    NOW,
    chat_on,
    require_postgres,
    tenant,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from nexovue_api import insight_account, setup
from nexovue_api.assistant import knowledge, service
from nexovue_api.assistant.embedding import embedding_state
from nexovue_api.assistant.schemas import AskContext
from nexovue_api.auth.dependencies import Identity, Reader
from nexovue_api.data.postgres.models import AssistantSettings, AssistantStep
from nexovue_api.runbooks import service as runbooks
from nexovue_api.runbooks.schemas import NewRunbook

require_postgres("setup without the vector extension")

PASSWORD: Final = "no-vector-test-only"


@dataclass(frozen=True)
class Install:
    name: str
    account: str
    # The account's own DSN on the new database, as `setup` reads `POSTGRES_DSN`.
    dsn: str
    # The superuser's DSN on the new database, to read back what setup left.
    superuser_dsn: str


def _on(dsn: str, database: str, account: str | None = None, password: str | None = None) -> str:
    parts = urlsplit(dsn)
    netloc = parts.netloc
    if account is not None:
        host = netloc.rsplit("@", 1)[-1]
        netloc = f"{account}:{password}@{host}"
    return urlunsplit((parts.scheme, netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
async def install() -> AsyncIterator[Install]:
    suffix = uuid.uuid4().hex[:10]
    name, account = f"f4_no_vector_{suffix}", f"f4_no_vector_{suffix}"
    admin = create_async_engine(ASYNC_DSN, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as db:
            await db.execute(text(f"CREATE ROLE {account} LOGIN PASSWORD '{PASSWORD}'"))
            await db.execute(text(f"CREATE DATABASE {name} OWNER {account} TEMPLATE template0"))
        yield Install(
            name=name,
            account=account,
            dsn=_on(ASYNC_DSN, name, account, PASSWORD),
            superuser_dsn=_on(ASYNC_DSN, name),
        )
    finally:
        async with admin.connect() as db:
            await db.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
            await db.execute(text(f"DROP ROLE IF EXISTS {account}"))
        await admin.dispose()


@pytest.fixture
async def ran(
    install: Install, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> str:
    """The real `setup`, as the install's own account; what it said."""
    monkeypatch.setenv("POSTGRES_DSN", install.dsn)
    monkeypatch.delenv(insight_account.PASSWORD_VARIABLE, raising=False)
    for name in setup.OBJECT_STORE_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    # `setup` runs its own event loops, as `nexovue setup` does: off this test's loop.
    assert await asyncio.to_thread(setup.main, []) == 0
    return capsys.readouterr().out


async def _engine(dsn: str) -> AsyncEngine:
    return create_async_engine(dsn)


async def test_setup_finishes_with_a_clear_warning_and_every_migration(
    install: Install, ran: str
) -> None:
    """AC43: setup finishes, says why, and leaves every migration applied, so detection and
    alerts start; the vector columns are simply not there."""
    assert "postgres: at the newest migration" in ran
    assert "vector: Postgres refused to create the vector extension" in ran
    assert "keyword only" in ran
    engine = await _engine(install.superuser_dsn)
    try:
        async with engine.connect() as db:
            head = await db.scalar(text("SELECT version_num FROM alembic_version"))
            assert head == setup.postgres_schema()
            assert (
                await db.scalar(text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'"))
                == 0
            )
            assert not await insight_account.vector_columns_present(db)
    finally:
        await engine.dispose()


async def test_the_ai_account_s_grants_apply_whole_without_the_vector_columns(
    install: Install, ran: str
) -> None:
    """A grant naming a missing column would fail the whole list; the vector grants wait for
    the columns, so the account keeps every other grant (AC43, BR17)."""
    engine = create_async_engine(install.superuser_dsn)
    try:
        async with engine.begin() as db:
            await insight_account._apply_grants(db)
            granted = await db.scalar(
                text(
                    "SELECT has_column_privilege('nexovue_insight', 'assistant_settings', "
                    "'embedding_checked_ok', 'UPDATE')"
                )
            )
            on_chunks = await db.scalar(
                text("SELECT has_table_privilege('nexovue_insight', 'knowledge_chunks', 'SELECT')")
            )
            column_grants = await db.scalar(
                text(
                    "SELECT count(*) FROM information_schema.column_privileges "
                    "WHERE grantee = 'nexovue_insight' AND table_name = 'knowledge_chunks'"
                )
            )
        assert granted is True
        assert (on_chunks, column_grants) == (False, 0)
    finally:
        await engine.dispose()


async def test_the_settings_say_keyword_only_and_search_answers_by_keyword(
    install: Install, ran: str
) -> None:
    """AC43: the settings section says search is keyword only, and knowledge search answers
    by keyword even when the worker names a step (AC41)."""
    engine = create_async_engine(install.dsn)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            db = AsyncSession(
                bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            made = await tenant(db, "NoVector")
            await chat_on(db, made)
            identity = Identity(user=made.user, organization=made.organization)
            settings = AssistantSettings(
                org_id=made.organization.id, embedding_model="fake-embedding"
            )
            state = await embedding_state(db, row=settings, connection=None)
            assert (state.vector_available, state.keyword_only) == (False, "no_extension")
            await runbooks.create_runbook(
                db,
                identity=identity,
                request=NewRunbook(
                    project_id=made.project.id,
                    title="Pool",
                    content="## Pool\ncheckout runs out of connections: restart pgbouncer.",
                ),
                now=NOW,
            )
            asked = await service.ask(
                db,
                identity=identity,
                project_id=made.project.id,
                conversation_id=None,
                question="What do we do when checkout runs out of connections?",
                context=AskContext(),
                ai=AVAILABLE,
                keys=KEYS,
                now=NOW,
            )
            step = AssistantStep(
                org_id=made.organization.id,
                message_id=asked.id,
                position=1,
                tool="search_knowledge",
                arguments={},
            )
            db.add(step)
            await db.flush()
            reader = Reader(
                org_id=made.organization.id, project_scope=made.project.id, message_id=asked.id
            )
            found = await knowledge.search(
                db, reader=reader, project_id=made.project.id, phrase="pgbouncer", step_id=step.id
            )
            assert (found.search, len(found.chunks)) == ("keyword", 1)
            await db.close()
            await transaction.rollback()
    finally:
        await engine.dispose()
