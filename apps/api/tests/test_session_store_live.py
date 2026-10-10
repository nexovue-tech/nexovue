"""What a session keeps, and who can end it (real Postgres).

A session is rewritten when it is seen, to record "last used". Anything that
rewrite forgets is silently dropped from a live session — which is how a
read-only support view became an ordinary one after sixty seconds
(`docs/changes/super-admin-in-the-console/`, found 2026-09-20). So every field a
session carries must survive being used, and each is named here rather than
counted, because the next field added is the next one that can be forgotten.

The store writes in transactions of its own, so these rows really commit. Each
test's tenant is removed afterwards, and its sessions go with it by cascade.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nexovue_api.auth.clients import ClientDescription
from nexovue_api.auth.sessions import (
    LAST_SEEN_RESOLUTION_SECONDS,
    Impersonation,
    SessionStore,
    token_fingerprint,
)
from nexovue_api.data.postgres.models import Organization, SessionRecord
from nexovue_api.signup import service as signup

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
TTL_SECONDS: Final = 600


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_session_store_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@dataclass(frozen=True, slots=True)
class Person:
    user_id: str
    org_id: str


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def store(factory: async_sessionmaker[AsyncSession]) -> SessionStore:
    return SessionStore(factory, ttl_seconds=TTL_SECONDS)


@pytest.fixture
async def people(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[list[Person]]:
    """Two people in two committed tenants, removed afterwards."""
    made: list[Person] = []
    async with factory.begin() as db:
        for _ in range(2):
            suffix = uuid.uuid4().hex[:12]
            tenant = await signup.provision_tenant(
                db,
                organization_name=f"Session Suite {suffix}",
                email=f"sessions-{suffix}@nexovue.test",
                password="correct-horse-battery",
                name=None,
            )
            made.append(Person(user_id=str(tenant.user.id), org_id=str(tenant.organization.id)))
    try:
        yield made
    finally:
        async with factory.begin() as db:
            await db.execute(
                delete(Organization).where(
                    Organization.id.in_([uuid.UUID(person.org_id) for person in made])
                )
            )


def _support_view() -> Impersonation:
    return Impersonation(
        impersonation_id=str(uuid.uuid4()),
        operator_label="Raj at nexovue",
        reason="checkout shows no traces",
        grant_id=str(uuid.uuid4()),
        until=datetime.now(UTC) + timedelta(minutes=30),
    )


async def _set(factory: async_sessionmaker[AsyncSession], token: str, **values: datetime) -> None:
    async with factory.begin() as db:
        await db.execute(
            update(SessionRecord)
            .where(SessionRecord.token_hash == token_fingerprint(token))
            .values(**values)
        )


async def _used_again(
    store: SessionStore, factory: async_sessionmaker[AsyncSession], token: str
) -> None:
    """Age the row past the last-seen resolution, then read it, so it is rewritten."""
    aged = datetime.now(UTC) - timedelta(seconds=LAST_SEEN_RESOLUTION_SECONDS + 1)
    await _set(factory, token, last_seen_at=aged)
    assert await store.get(token) is not None


async def test_a_support_view_is_still_a_support_view_after_it_is_used(
    store: SessionStore, factory: async_sessionmaker[AsyncSession], people: list[Person]
) -> None:
    """The defect: a read-only view became an ordinary session, as the customer,
    sixty seconds in — every write refused before that, and none after."""
    impersonation = _support_view()
    token, _ = await store.create(
        user_id=people[0].user_id, org_id=people[0].org_id, impersonation=impersonation
    )

    await _used_again(store, factory, token)

    session = await store.peek(token)
    assert session is not None
    assert session.impersonation == impersonation


async def test_what_a_session_is_recognised_by_survives_being_used(
    store: SessionStore, factory: async_sessionmaker[AsyncSession], people: list[Person]
) -> None:
    client = ClientDescription(label="Chrome on macOS", network="203.0.113.0/24")
    token, _ = await store.create(user_id=people[0].user_id, org_id=people[0].org_id, client=client)
    until = datetime.now(UTC) + timedelta(minutes=5)
    assert await store.set_admin_until(token, until) is not None

    await _used_again(store, factory, token)

    session = await store.peek(token)
    assert session is not None
    assert session.client == client
    assert session.admin_until == until
    assert (datetime.now(UTC) - session.last_used_at).total_seconds() < 5


async def test_being_used_does_not_lengthen_a_session(
    store: SessionStore, factory: async_sessionmaker[AsyncSession], people: list[Person]
) -> None:
    """A session's lifetime is fixed when it is made; neither being seen nor
    proving who you are moves it."""
    token, _ = await store.create(user_id=people[0].user_id, org_id=people[0].org_id)
    async with factory() as db:
        before = await db.scalar(
            select(SessionRecord.expires_at).where(
                SessionRecord.token_hash == token_fingerprint(token)
            )
        )

    await _used_again(store, factory, token)
    await store.set_admin_until(token, datetime.now(UTC) + timedelta(minutes=5))

    async with factory() as db:
        after = await db.scalar(
            select(SessionRecord.expires_at).where(
                SessionRecord.token_hash == token_fingerprint(token)
            )
        )
    assert after == before


async def test_a_support_view_ends_when_its_grant_does(
    store: SessionStore, factory: async_sessionmaker[AsyncSession], people: list[Person]
) -> None:
    """Shorter than an ordinary session, so the grant is what ends it."""
    impersonation = replace(_support_view(), until=datetime.now(UTC) + timedelta(minutes=5))
    token, _ = await store.create(
        user_id=people[0].user_id, org_id=people[0].org_id, impersonation=impersonation
    )

    async with factory() as db:
        expires_at = await db.scalar(
            select(SessionRecord.expires_at).where(
                SessionRecord.token_hash == token_fingerprint(token)
            )
        )
    assert expires_at == impersonation.until


async def test_an_expired_session_is_gone_to_every_reader(
    store: SessionStore, factory: async_sessionmaker[AsyncSession], people: list[Person]
) -> None:
    """Expiry is a column every read checks; the sweep only reclaims the disk."""
    person = people[0]
    token, _ = await store.create(user_id=person.user_id, org_id=person.org_id)
    await _set(factory, token, expires_at=datetime.now(UTC) - timedelta(seconds=1))

    assert await store.get(token) is None
    assert await store.peek(token) is None
    assert await store.set_admin_until(token, datetime.now(UTC)) is None
    assert token_fingerprint(token) not in [
        s.id for s in await store.list_for(org_id=person.org_id, user_id=person.user_id)
    ]


async def test_a_person_lists_their_own_sessions_newest_first(
    store: SessionStore, people: list[Person]
) -> None:
    mine, theirs = people
    older, _ = await store.create(user_id=mine.user_id, org_id=mine.org_id)
    newer, _ = await store.create(user_id=mine.user_id, org_id=mine.org_id)
    await store.create(user_id=theirs.user_id, org_id=theirs.org_id)

    listed = [entry.id for entry in await store.list_for(org_id=mine.org_id, user_id=mine.user_id)]

    assert listed == [token_fingerprint(newer), token_fingerprint(older)]


async def test_nobody_can_end_a_session_that_is_not_theirs(
    store: SessionStore, people: list[Person]
) -> None:
    """The user is part of the delete: another person's session id is answered
    exactly like one that never existed, and survives."""
    mine, theirs = people
    their_token, _ = await store.create(user_id=theirs.user_id, org_id=theirs.org_id)

    ended = await store.revoke_one_of(
        org_id=mine.org_id, user_id=mine.user_id, session_id=token_fingerprint(their_token)
    )

    assert ended is False
    assert await store.peek(their_token) is not None
    assert await store.revoke_one_of(
        org_id=theirs.org_id, user_id=theirs.user_id, session_id=token_fingerprint(their_token)
    )
    assert await store.peek(their_token) is None


async def test_a_revoked_session_stops_resolving(store: SessionStore, people: list[Person]) -> None:
    token, _ = await store.create(user_id=people[0].user_id, org_id=people[0].org_id)

    await store.revoke(token)
    await store.revoke(token)  # already gone: still fine

    assert await store.get(token) is None
