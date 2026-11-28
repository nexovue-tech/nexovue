"""A request holds a pooled Postgres connection only while it uses Postgres (real Postgres).

F20's measurement (2026-11-22) gave HTTP 500 on 7 of 60 dashboard reads at 20 viewers on one
API process: `QueuePool limit of size 10 overflow 5 reached`, with every pooled connection
`idle in transaction` after the identity lookup. Two things were wrong, and each is held here
(`docs/changes/request-holds-one-connection/`):

1. The caller's lookup left its transaction open, so a request kept its connection from the
   identity lookup to the end of the request — through every non-Postgres wait. A route that
   also asks for the reader (the dashboard read asks for both) then needed a *second*
   connection for the session lookup while holding the first: with the pool full of such
   requests, each waited on the others until the pool's timeout.
2. A full pool answered 500 after that timeout, as if we had crashed, instead of saying we
   are busy and when to come back.

The pool here is one connection, so the deadlock needs one request rather than twenty. The
engine names itself to Postgres so `pg_stat_activity` shows this suite's connections alone.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from live import require_stack
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nexovue_api.auth.dependencies import CurrentIdentity, CurrentReader
from nexovue_api.auth.sessions import SESSION_COOKIE_NAME, SessionStore
from nexovue_api.data.postgres.models import Organization
from nexovue_api.deps import get_resources
from nexovue_api.errors.handlers import BUSY_RETRY_AFTER_SECONDS, install_error_handlers
from nexovue_api.errors.problems import SERVICE_BUSY
from nexovue_api.signup import service as signup

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
TTL_SECONDS: Final = 600
# Short, so a request stuck waiting for a connection fails the test in a second, not ten.
POOL_TIMEOUT_SECONDS: Final = 1.0
CONCURRENT_REQUESTS: Final = 6
CONCURRENT_POOL_SIZE: Final = 2


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_request_connection_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@dataclass(frozen=True, slots=True)
class Stack:
    engine: AsyncEngine
    application_name: str
    resources: Any
    cookie: str


def _engine(application_name: str, pool_size: int) -> AsyncEngine:
    return create_async_engine(
        ASYNC_DSN,
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=POOL_TIMEOUT_SECONDS,
        connect_args={"server_settings": {"application_name": application_name}},
    )


async def _stack(pool_size: int) -> AsyncIterator[Stack]:
    """One committed tenant with a live session, behind a pool of `pool_size` connections."""
    application_name = f"pool-suite-{uuid.uuid4().hex[:12]}"
    engine = _engine(application_name, pool_size)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sessions = SessionStore(factory, ttl_seconds=TTL_SECONDS)
    suffix = uuid.uuid4().hex[:12]
    async with factory.begin() as db:
        tenant = await signup.provision_tenant(
            db,
            organization_name=f"Pool Suite {suffix}",
            email=f"pool-{suffix}@nexovue.test",
            password="correct-horse-battery",
            name=None,
        )
    token, _ = await sessions.create(
        user_id=str(tenant.user.id), org_id=str(tenant.organization.id)
    )
    # The resources a route reaches through `get_resources`: the pools and the session store.
    resources = type("Resources", (), {"session_factory": factory, "sessions": sessions})()
    try:
        yield Stack(engine, application_name, resources, f"{SESSION_COOKIE_NAME}={token}")
    finally:
        async with factory.begin() as db:
            await db.execute(delete(Organization).where(Organization.id == tenant.organization.id))
        await engine.dispose()


@pytest.fixture
async def one_connection() -> AsyncIterator[Stack]:
    async for stack in _stack(pool_size=1):
        yield stack


@pytest.fixture
async def two_connections() -> AsyncIterator[Stack]:
    async for stack in _stack(pool_size=CONCURRENT_POOL_SIZE):
        yield stack


async def _states(application_name: str) -> list[tuple[str, str]]:
    """This suite's backends as Postgres sees them, read on a connection outside its pool."""
    observer = create_async_engine(ASYNC_DSN, pool_size=1, max_overflow=0)
    try:
        async with observer.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT state, query FROM pg_stat_activity "
                    "WHERE application_name = :name AND state IS NOT NULL"
                ),
                {"name": application_name},
            )
            return [(str(state), str(query)) for state, query in rows]
    finally:
        await observer.dispose()


def _app(stack: Stack, slow_step: Callable[[], Awaitable[None]]) -> FastAPI:
    """Two routes on the real caller dependencies, each with a non-Postgres step to observe."""
    app = FastAPI()
    install_error_handlers(app)
    app.dependency_overrides[get_resources] = lambda: stack.resources

    @app.get("/person")
    async def person(identity: CurrentIdentity) -> dict[str, str]:
        await slow_step()
        return {"org_id": str(identity.org_id)}

    # The dashboard read's shape: a person, and the reader its telemetry repository asks for.
    @app.get("/person-and-reader")
    async def person_and_reader(identity: CurrentIdentity, reader: CurrentReader) -> dict[str, str]:
        await slow_step()
        return {"org_id": str(reader.org_id)}

    return app


def _client(app: FastAPI, stack: Stack) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
        headers={"cookie": stack.cookie},
    )


async def test_a_slow_step_after_the_caller_lookup_holds_no_connection(
    one_connection: Stack,
) -> None:
    """The defect's signature: the identity lookup's connection `idle in transaction` while
    the request did something else. After the lookup, the connection is back in the pool."""
    seen: dict[str, Any] = {}

    async def slow_step() -> None:
        seen["checked_out"] = one_connection.engine.pool.checkedout()  # type: ignore[attr-defined]
        seen["states"] = await _states(one_connection.application_name)

    async with _client(_app(one_connection, slow_step), one_connection) as client:
        reply = await client.get("/person")

    assert reply.status_code == 200, reply.text
    assert [state for state, _ in seen["states"]] == ["idle"], seen["states"]
    assert seen["checked_out"] == 0


async def test_a_person_and_a_reader_need_one_connection_not_two(one_connection: Stack) -> None:
    """The deadlock: with the identity lookup still holding the only connection, the reader's
    session lookup waited for a second one until the pool gave up."""

    async def slow_step() -> None:
        return None

    async with _client(_app(one_connection, slow_step), one_connection) as client:
        reply = await client.get("/person-and-reader")

    assert reply.status_code == 200, reply.text


async def test_more_readers_than_connections_all_answer(two_connections: Stack) -> None:
    """The measurement in small: more requests at once than the pool holds, each pausing
    after its caller lookup as a dashboard read pauses for ClickHouse."""
    gate = asyncio.Event()
    arrived = 0

    async def slow_step() -> None:
        nonlocal arrived
        arrived += 1
        if arrived == CONCURRENT_REQUESTS:
            gate.set()
        await asyncio.wait_for(gate.wait(), timeout=POOL_TIMEOUT_SECONDS * 5)

    async with _client(_app(two_connections, slow_step), two_connections) as client:
        replies = await asyncio.gather(
            *(client.get("/person-and-reader") for _ in range(CONCURRENT_REQUESTS))
        )

    assert [reply.status_code for reply in replies] == [200] * CONCURRENT_REQUESTS


async def test_a_full_pool_answers_busy_with_a_time_to_come_back(one_connection: Stack) -> None:
    """Every connection taken: a 503 saying so, with Retry-After, not a 500 saying nothing."""

    async def slow_step() -> None:
        return None

    async with one_connection.engine.connect() as held:
        await held.execute(text("SELECT 1"))
        async with _client(_app(one_connection, slow_step), one_connection) as client:
            reply = await client.get("/person")

    assert reply.status_code == 503, reply.text
    assert reply.headers["retry-after"] == str(BUSY_RETRY_AFTER_SECONDS)
    body = reply.json()
    assert body["type"] == SERVICE_BUSY.uri
    assert "QueuePool" not in reply.text


async def test_the_session_still_writes_what_the_route_changes_on_the_person(
    one_connection: Stack,
) -> None:
    """Ending the lookup's transaction must not detach the person: a route that changes
    `identity.user` (a name, a second factor) relies on the request's commit to keep it."""
    app = FastAPI()
    install_error_handlers(app)
    app.dependency_overrides[get_resources] = lambda: one_connection.resources
    new_name = f"Renamed {uuid.uuid4().hex[:6]}"

    @app.post("/rename")
    async def rename(identity: CurrentIdentity) -> dict[str, str]:
        identity.user.name = new_name
        return {"user_id": str(identity.user.id)}

    async with _client(app, one_connection) as client:
        reply = await client.post("/rename")
    assert reply.status_code == 200, reply.text

    factory: async_sessionmaker[AsyncSession] = one_connection.resources.session_factory
    async with factory() as db:
        stored = await db.scalar(
            text("SELECT name FROM users WHERE id = :id"), {"id": reply.json()["user_id"]}
        )
    assert stored == new_name
