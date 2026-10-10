"""Who reaches the read surface, and how far (F36, AC14, AC15, AC21).

Against real Postgres, rolled back per test. What it proves: an API token
resolves to its own organization and scope; a project outside that scope answers
exactly as a project that does not exist; a token is refused by any route that
asks for a person; an ingest key is refused in its own sentence; and a signed-in
person reaches the surface unchanged, reading every project.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Final, cast
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from nexovue_api.apitokens import service as api_tokens
from nexovue_api.apitokens.tokens import INGEST_KEYS_CANNOT_READ, TOKENS_ARE_READ_ONLY
from nexovue_api.apitokens.usage import TokenUsage
from nexovue_api.auth.dependencies import Reader, get_current_identity, get_current_reader
from nexovue_api.auth.sessions import Session
from nexovue_api.errors.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from nexovue_api.projects.service import (
    NO_SUCH_PROJECT,
    refuse_out_of_scope,
    require_readable_project,
)
from nexovue_api.settings import API_PREFIX
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
PASSWORD: Final = "correct horse battery staple"
INGEST_KEY: Final = "nvk_live_devlocaldevlocaldevloc"
SITE_KEY: Final = "nvp_live_devlocaldevlocaldevloc"


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_read_surface_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


def _now() -> datetime:
    return datetime.now(UTC)


def _request(presented: str | None = None) -> Request:
    headers = [(b"authorization", f"Bearer {presented}".encode())] if presented else []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"{API_PREFIX}/services",
            "headers": headers,
            "query_string": b"",
        }
    )


async def _tenant(db: AsyncSession, name: str = "Acme") -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name=name,
        email=f"{name.lower()}-{uuid.uuid4()}@example.test",
        password=PASSWORD,
    )


async def _token(
    db: AsyncSession, tenant: ProvisionedTenant, *, project_id: uuid.UUID | None
) -> str:
    made = await api_tokens.create(
        db,
        org_id=tenant.organization.id,
        actor=tenant.user,
        name="CI",
        project_id=project_id,
        expires_in_days=90,
        now=_now(),
    )
    return made.token.get_secret_value()


class _NoSessions:
    """The session store is never reached on a token request; proving that is the point."""

    async def get(self, token: str) -> Session | None:
        raise AssertionError("a token request looked for a session")

    peek = get


async def test_a_token_reads_as_its_own_organization_and_scope(db: AsyncSession) -> None:
    acme = await _tenant(db)
    presented = await _token(db, acme, project_id=acme.project.id)

    reader = await get_current_reader(_request(presented), db, cast(Any, _NoSessions()))

    assert reader.org_id == acme.organization.id
    assert reader.project_scope == acme.project.id
    assert reader.is_token


async def test_a_token_for_every_project_carries_no_scope(db: AsyncSession) -> None:
    acme = await _tenant(db)
    presented = await _token(db, acme, project_id=None)

    reader = await get_current_reader(_request(presented), db, cast(Any, _NoSessions()))

    assert reader.project_scope is None
    assert reader.may_read_project(acme.project.id)
    # Every project of *its own* organization; the tenant filter is elsewhere.
    assert reader.may_read_project(uuid.uuid4())


async def test_a_project_outside_the_scope_answers_as_one_that_does_not_exist(
    db: AsyncSession,
) -> None:
    """AC15, BR11. The two answers are the same object, not two similar sentences."""
    acme = await _tenant(db)
    staging = await _tenant(db, "Staging")
    presented = await _token(db, acme, project_id=acme.project.id)
    reader = await get_current_reader(_request(presented), db, cast(Any, _NoSessions()))

    with pytest.raises(NotFoundError) as out_of_scope:
        await require_readable_project(db, reader=reader, project_id=staging.project.id)
    with pytest.raises(NotFoundError) as never_existed:
        await require_readable_project(db, reader=reader, project_id=uuid.uuid4())

    assert out_of_scope.value.detail == never_existed.value.detail == NO_SUCH_PROJECT


async def test_the_scope_guard_refuses_the_same_way(db: AsyncSession) -> None:
    acme = await _tenant(db)
    scoped = Reader(
        org_id=acme.organization.id, project_scope=acme.project.id, token_id=uuid.uuid4()
    )

    with pytest.raises(NotFoundError) as refused:
        refuse_out_of_scope(scoped, uuid.uuid4())

    assert refused.value.detail == NO_SUCH_PROJECT
    # Its own project passes through untouched.
    refuse_out_of_scope(scoped, acme.project.id)


async def test_a_token_is_refused_by_a_route_that_asks_for_a_person(db: AsyncSession) -> None:
    """FR15: the refusal says where tokens do work, so nobody hunts for a bug."""
    acme = await _tenant(db)
    presented = await _token(db, acme, project_id=None)
    session = Session(
        user_id=str(acme.user.id), org_id=str(acme.organization.id), created_at=_now()
    )

    with pytest.raises(PermissionDeniedError) as refused:
        await get_current_identity(_request(presented), session, db)

    assert refused.value.detail == TOKENS_ARE_READ_ONLY


@pytest.mark.parametrize("key", [INGEST_KEY, SITE_KEY], ids=["ingest key", "site key"])
async def test_an_ingest_key_cannot_read(db: AsyncSession, key: str) -> None:
    """AC21's API half: the receiver refuses a read token, and this refuses a key."""
    with pytest.raises(UnauthenticatedError) as refused:
        await get_current_reader(_request(key), db, cast(Any, _NoSessions()))

    assert refused.value.detail == INGEST_KEYS_CANNOT_READ


async def test_a_revoked_token_reaches_nothing(db: AsyncSession) -> None:
    acme = await _tenant(db)
    presented = await _token(db, acme, project_id=None)
    live = await api_tokens.token_for(db, presented, now=_now())
    await api_tokens.revoke(
        db, org_id=acme.organization.id, actor=acme.user, token_id=live.id, now=_now()
    )

    with pytest.raises(UnauthenticatedError):
        await get_current_reader(_request(presented), db, cast(Any, _NoSessions()))


async def test_a_signed_in_person_reads_the_surface_with_no_scope(db: AsyncSession) -> None:
    acme = await _tenant(db)
    other = await _tenant(db, "Globex")
    stored = Session(user_id=str(acme.user.id), org_id=str(acme.organization.id), created_at=_now())

    class _OneSession:
        async def get(self, token: str) -> Session | None:
            return stored

        peek = get

    reader = await get_current_reader(_request("a-session-token"), db, cast(Any, _OneSession()))

    assert reader.org_id == acme.organization.id
    assert reader.project_scope is None
    assert not reader.is_token
    assert reader.identity is not None
    assert reader.identity.user.id == acme.user.id
    # A person reads every project of their own organization, and no other tenant's.
    project = await require_readable_project(db, reader=reader, project_id=acme.project.id)
    assert project.id == acme.project.id
    with pytest.raises(NotFoundError):
        await require_readable_project(db, reader=reader, project_id=other.project.id)


async def test_a_token_request_marks_the_token_used_and_counts_the_day(
    db: AsyncSession,
) -> None:
    """AC18: requests are counted in memory and added to the day once a minute —
    added, never overwritten, so two processes' counts both land."""
    acme = await _tenant(db)
    presented = await _token(db, acme, project_id=None)
    token = await api_tokens.token_for(db, presented, now=_now())
    await db.flush()
    connection = await db.connection()
    usage = TokenUsage(
        async_sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
    )

    for _ in range(3):
        usage.count(token.id, _now())
    await usage.flush()
    for _ in range(2):
        usage.count(token.id, _now())
    await usage.flush()
    org_id = acme.organization.id
    db.expire_all()

    [listed] = (await api_tokens.list_for_organization(db, org_id=org_id, now=_now())).api_tokens
    assert listed.last_used_at is not None
    assert listed.requests_today == 5
