"""The admin console's organization search and page (F31, FR25) against real Postgres.

Rolled back per test. What it proves: staff find an organization by name, slug,
id or any member's address; the page shows its members (an invite as invited),
its projects, its region, and whether ingest is suspended and why; an unknown id
is a 404.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
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
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.admin import service as admin
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
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
    suite="apps/api/tests/test_admin_live.py",
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


@pytest.mark.parametrize("how", ["name", "slug", "id", "member address"])
async def test_staff_find_an_organization_every_way_they_might_know_it(
    db: AsyncSession, how: str
) -> None:
    acme = await _tenant(db, f"Findable{uuid.uuid4().hex[:6]}")
    needle = {
        "name": acme.organization.name,
        "slug": acme.organization.slug,
        "id": str(acme.organization.id),
        "member address": acme.user.email,
    }[how]

    found = await admin.search(db, needle)

    assert acme.organization.id in {row.id for row in found.organizations}


async def test_a_search_of_one_character_is_refused(db: AsyncSession) -> None:
    with pytest.raises(InvalidRequestError):
        await admin.search(db, "a")


async def test_the_page_shows_people_projects_region_and_suspension(db: AsyncSession) -> None:
    acme = await _tenant(db, "Acme")
    await members.invite(
        db, actor=acme.user, email=f"t-{uuid.uuid4()}@example.test", role="viewer", now=_now()
    )
    acme.organization.ingest_suspended_at = _now()
    acme.organization.ingest_suspended_reason = "runaway exporter"
    await db.flush()

    page = await admin.page(db, org_id=acme.organization.id)

    assert page.organization.region == "india"
    assert [(member.role, member.status) for member in page.members] == [
        ("owner", "active"),
        ("viewer", "invited"),
    ]
    assert [project.slug for project in page.projects] == ["production"]
    assert page.projects[0].last_ingest_day is None
    assert page.suspension is not None and page.suspension.reason == "runaway exporter"
    assert page.usage is not None


async def test_an_unknown_organization_is_not_found(db: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await admin.page(db, org_id=uuid.uuid4())
