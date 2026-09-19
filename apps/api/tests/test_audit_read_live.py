"""The audit log screen's read (F26, FR36, AC36) against real Postgres.

Each test runs in a rolled-back transaction. What it proves: pages are newest
first and, walked with their cursors, return every entry once; each filter
narrows to what it names; a removed person is still named; another
organization's history never appears. Who may read it (AC38) is the route gate,
proved by `test_authorization_coverage.py` and `test_policy.py`.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.audit import read
from nexovue_api.auth import set_password
from nexovue_api.data.postgres.models import AuditLogEntry
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.members import service as members
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
PASSWORD: Final = "correct horse battery staple"


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_audit_read_live.py",
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


async def _tenant(db: AsyncSession, name: str) -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name=name,
        email=f"{name.lower()}-{uuid.uuid4()}@example.test",
        password=PASSWORD,
    )


def _now() -> datetime:
    return datetime.now(UTC)


async def _history(db: AsyncSession, tenant: ProvisionedTenant, count: int) -> None:
    """`count` sign-ins spread over the last hour, plus whatever signup wrote."""
    start = _now() - timedelta(hours=1)
    for index in range(count):
        db.add(
            AuditLogEntry(
                org_id=tenant.organization.id,
                actor_user_id=tenant.user.id,
                action="session.created",
                target=f"user:{tenant.user.id}",
                at=start + timedelta(seconds=index),
            )
        )
    await db.flush()


async def test_pages_walked_by_cursor_return_every_entry_once_newest_first(
    db: AsyncSession,
) -> None:
    acme = await _tenant(db, "Acme")
    await _history(db, acme, 23)
    everything = await read.read_page(db, org_id=acme.organization.id, limit=200)

    walked: list[uuid.UUID] = []
    cursor: str | None = None
    while True:
        page = await read.read_page(db, org_id=acme.organization.id, cursor=cursor, limit=5)
        walked.extend(entry.id for entry in page.entries)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    assert walked == [entry.id for entry in everything.entries]
    assert len(walked) == len(set(walked))
    moments = [entry.at for entry in everything.entries]
    assert moments == sorted(moments, reverse=True)


async def test_each_filter_narrows_to_what_it_names(db: AsyncSession) -> None:
    acme = await _tenant(db, "Acme")
    await _history(db, acme, 3)
    member, token = await members.invite(
        db, actor=acme.user, email=f"t-{uuid.uuid4()}@example.test", role="member", now=_now()
    )
    await set_password.set_password(db, token, name="Priya", password=PASSWORD, now=_now())
    members.record_joined(db, member=member)
    await db.flush()

    people = await read.read_page(db, org_id=acme.organization.id, kind="people")
    assert [entry.action for entry in people.entries] == ["member.joined", "member.invited"]
    assert people.entries[1].target_name == "Priya"
    assert people.entries[1].detail == {"email": member.email, "role": "member"}

    by_priya = await read.read_page(db, org_id=acme.organization.id, actor_id=member.id)
    assert [entry.action for entry in by_priya.entries] == ["member.joined"]
    assert by_priya.entries[0].actor.name == "Priya"

    recent = await read.read_page(
        db, org_id=acme.organization.id, kind="sign_in", since=_now() - timedelta(seconds=1)
    )
    assert recent.entries == []


async def test_a_removed_person_is_still_named(db: AsyncSession) -> None:
    """BR6: removing someone never rewrites what they did."""
    acme = await _tenant(db, "Acme")
    member, token = await members.invite(
        db, actor=acme.user, email=f"t-{uuid.uuid4()}@example.test", role="admin", now=_now()
    )
    await set_password.set_password(db, token, name="Priya", password=PASSWORD, now=_now())
    members.record_joined(db, member=member)
    await members.remove(
        db, actor=acme.user, organization=acme.organization, member_id=member.id, now=_now()
    )
    await db.flush()

    by_priya = await read.read_page(db, org_id=acme.organization.id, actor_id=member.id)
    assert by_priya.entries[0].actor.name == "Priya"


async def test_another_organizations_history_never_appears(db: AsyncSession) -> None:
    acme = await _tenant(db, "Acme")
    globex = await _tenant(db, "Globex")
    await _history(db, globex, 4)

    seen = await read.read_page(db, org_id=acme.organization.id, limit=200)
    assert all(entry.actor.id != globex.user.id for entry in seen.entries)


async def test_a_cursor_this_endpoint_did_not_issue_is_refused(db: AsyncSession) -> None:
    acme = await _tenant(db, "Acme")
    with pytest.raises(InvalidRequestError, match="not one this endpoint issued"):
        await read.read_page(db, org_id=acme.organization.id, cursor="bm90LWEtY3Vyc29y")
