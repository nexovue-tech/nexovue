"""The admin console's organization search and page (F31, FR25) against real Postgres.

Rolled back per test. What it proves: the operator finds an organization by name, slug,
id or any member's address, and never nexovue's own organization; the page shows
its members (an invite as invited),
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
from environment import TEST_ENVIRONMENT
from live import require_stack
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.admin import queries
from nexovue_api.admin import service as admin
from nexovue_api.data.postgres.models import User
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
from nexovue_api.members import service as members
from nexovue_api.settings import Settings
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant
from nexovue_api.superadmin.seed import PLATFORM_NAME, PLATFORM_ORG_ID, seed_super_admin

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


def _operator_settings() -> Settings:
    """The environment nexovue's own operator is seeded from."""
    environment = {
        **TEST_ENVIRONMENT,
        "SUPER_ADMIN_EMAIL": f"operator-{uuid.uuid4().hex[:8]}@nexovue.com",
        "SUPER_ADMIN_PASSWORD": PASSWORD,
    }
    return Settings(**environment)  # type: ignore[arg-type]


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.mark.parametrize("how", ["name", "slug", "id", "member address"])
async def test_the_operator_finds_an_organization_every_way_they_might_know_it(
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


async def test_the_search_never_returns_nexovues_own_organization(db: AsyncSession) -> None:
    """It is not a customer — no project, no usage, nothing to suspend or comp —
    and it matches the word an operator is most likely to type."""
    settings = _operator_settings()
    await seed_super_admin(db, settings)

    found = await admin.search(db, PLATFORM_NAME)

    assert PLATFORM_ORG_ID not in {row.id for row in found.organizations}


async def test_the_screen_opens_as_a_list_of_every_customer(db: AsyncSession) -> None:
    """Founder, 2026-09-21: the platform screen opens as a list and the search
    narrows it, rather than opening as a search box over nothing."""
    acme = await _tenant(db, f"Listed{uuid.uuid4().hex[:6]}")

    found = await admin.search(db)

    # Newest first, so one created a moment ago is on the first page whether or
    # not the development store holds more than the cap — it usually does, which
    # is what `truncated` is for and what the next test covers.
    assert acme.organization.id in {row.id for row in found.organizations}


async def test_the_list_says_when_it_is_only_the_newest(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capped list read as the whole list is how an operator concludes a
    customer does not exist."""
    for index in range(2):
        await _tenant(db, f"Capped{index}{uuid.uuid4().hex[:6]}")
    monkeypatch.setattr(queries, "SEARCH_LIMIT", 1)
    found = await admin.search(db)

    assert len(found.organizations) == 1
    assert found.truncated is True


def test_the_operator_is_named_for_what_they_are_when_they_carry_the_company_name() -> None:
    """A customer's audit log read "nexovue at nexovue viewed as Dev Owner"
    (`docs/changes/console-review-fixes/`)."""
    named = User(email="raj@nexovue.com", name="Raj", org_id=uuid.uuid4(), password_hash="x")
    company = User(
        email="operator@nexovue.test", name="nexovue", org_id=uuid.uuid4(), password_hash="x"
    )
    nameless = User(
        email="operator@nexovue.test", name=None, org_id=uuid.uuid4(), password_hash="x"
    )

    assert admin.operator_label(named) == "Raj at nexovue"
    assert admin.operator_label(company) == "nexovue support"
    assert admin.operator_label(nameless) == "nexovue support"


async def test_a_search_of_one_character_is_refused(db: AsyncSession) -> None:
    with pytest.raises(InvalidRequestError):
        await admin.search(db, "a")


async def test_the_member_list_holds_its_order_when_a_clock_cannot_separate_two_rows(
    db: AsyncSession,
) -> None:
    """The order of a list a human reads must not depend on the query planner.

    **The premise is the point.** `created_at` defaults to Postgres `now()`,
    which is *transaction* start time, so an owner and the teammate they invited
    in the same request share it to the microsecond. `ORDER BY created_at` alone
    is then unordered between them, and the admin's member list silently
    reordered itself between two loads of the same page.

    Found 2026-09-21 by the parallel gate — `test_the_page_shows_people_...`
    returned `[viewer, owner]` where it had returned `[owner, viewer]` for weeks.
    Nothing had changed but the plan. This asserts the tie exists *and* that it
    is broken the same way every time, so the next person to drop the tiebreak
    fails here rather than shipping a list that shuffles.
    """
    acme = await _tenant(db, "Acme")
    await members.invite(
        db, actor=acme.user, email=f"t-{uuid.uuid4()}@example.test", role="viewer", now=_now()
    )
    await db.flush()

    page = await admin.page(db, org_id=acme.organization.id)
    stamps = {member.id: member for member in page.members}
    assert len(page.members) == 2

    owner, viewer = page.members
    assert (owner.role, viewer.role) == ("owner", "viewer"), (
        "the owner is the older row and must come first"
    )

    # The premise: were these separable by their clocks, the tiebreak would not
    # be doing anything and this test would be proving nothing.
    rows = await queries.members_of(db, org_id=acme.organization.id)
    assert rows[0].created_at == rows[1].created_at, (
        "these two rows no longer share a `created_at`, so this test no longer "
        "exercises the tie it exists for. If `CreatedAt` stopped defaulting to "
        "`now()`, say so here rather than deleting the ordering."
    )
    # uuidv7 is time-ordered, so the tiebreak agrees with the order of creation
    # rather than being merely stable.
    assert rows[0].id < rows[1].id
    assert list(stamps) == [row.id for row in rows]


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
