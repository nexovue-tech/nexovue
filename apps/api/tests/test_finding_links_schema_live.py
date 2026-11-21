"""`finding_links`' indexes and checks, against real Postgres (F22 plan T4.2; AC72's link
half, FR41). Every test runs in a transaction that is rolled back.

What the database itself must refuse or allow, whatever the code above it does:

- one synced issue per case, and one case per synced issue; a `stopped` link (a
  disconnect, FR32) frees both slots;
- one issue as a reference on two cases, once each;
- a link naming another organization's finding, connection or place;
- an address where a strict key belongs, and facts kept past `creating`.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.signup.service import provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
NOW: Final = datetime.now(UTC)


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_finding_links_schema_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@dataclass(frozen=True)
class Org:
    org_id: uuid.UUID
    project_id: uuid.UUID
    connection_id: uuid.UUID
    place_id: uuid.UUID


async def _org(db: AsyncSession) -> Org:
    tenant = await provision_tenant(
        db,
        organization_name="Links",
        email=f"links-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )
    org_id = tenant.organization.id
    connection_id = (
        await db.execute(
            text(
                "INSERT INTO connections (org_id, kind, state, github_app_id) "
                "VALUES (:org, 'github', 'working', :app) RETURNING id"
            ),
            {"org": org_id, "app": uuid.uuid4().int % 10**12},
        )
    ).scalar_one()
    place_id = (
        await db.execute(
            text(
                "INSERT INTO connection_repositories "
                "(org_id, connection_id, external_id, key, is_private) "
                "VALUES (:org, :connection, '42', 'acme/api', true) RETURNING id"
            ),
            {"org": org_id, "connection": connection_id},
        )
    ).scalar_one()
    return Org(org_id, tenant.project.id, connection_id, place_id)


async def _finding(db: AsyncSession, org: Org, service: str = "checkout") -> uuid.UUID:
    finding_id: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO findings (org_id, project_id, scope, service_name, severity, "
                "title, started_at, state, closed_at, closed_reason) "
                "VALUES (:org, :project, 'service', :service, 'critical', 'x', :now, "
                "'closed', :now, 'recovered') RETURNING id"
            ),
            {"org": org.org_id, "project": org.project_id, "service": service, "now": NOW},
        )
    ).scalar_one()
    return finding_id


async def _link(
    db: AsyncSession,
    org: Org,
    finding_id: uuid.UUID,
    *,
    role: str = "synced",
    status: str = "in_sync",
    external_id: str | None = "1001",
    external_key: str | None = "acme/api#12",
    connection_id: uuid.UUID | None = None,
    place_id: uuid.UUID | None = None,
    frozen_facts: str | None = None,
) -> uuid.UUID:
    link_id: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO finding_links (org_id, finding_id, connection_id, place_id, role, "
                "sync_status, external_id, external_key, frozen_facts) "
                "VALUES (:org, :finding, :connection, :place, :role, :status, :external_id, "
                ":external_key, CAST(:facts AS jsonb)) RETURNING id"
            ),
            {
                "org": org.org_id,
                "finding": finding_id,
                "connection": connection_id or org.connection_id,
                "place": place_id or org.place_id,
                "role": role,
                "status": status,
                "external_id": external_id,
                "external_key": external_key,
                "facts": frozen_facts,
            },
        )
    ).scalar_one()
    return link_id


async def _refused(db: AsyncSession, **link: Any) -> str:
    """The constraint that refused the insert; the savepoint keeps the transaction usable."""
    with pytest.raises(IntegrityError) as refused:
        async with db.begin_nested():
            await _link(db, **link)
    return str(refused.value.orig)


# ------------------------------------------------------------ the synced slot


async def test_a_case_holds_one_synced_issue(db: AsyncSession) -> None:
    org = await _org(db)
    finding = await _finding(db, org)
    await _link(db, org, finding)
    refusal = await _refused(
        db, org=org, finding_id=finding, external_id="1002", external_key="acme/api#13"
    )
    assert "uq_finding_links_one_synced_per_case" in refusal


async def test_an_issue_is_synced_on_one_case(db: AsyncSession) -> None:
    org = await _org(db)
    first, second = await _finding(db, org), await _finding(db, org, "payments")
    await _link(db, org, first)
    refusal = await _refused(db, org=org, finding_id=second)
    assert "uq_finding_links_synced_once" in refusal


async def test_a_stopped_link_frees_both_slots(db: AsyncSession) -> None:
    """FR32: a disconnect stops each link and frees its case's synced slot."""
    org = await _org(db)
    first, second = await _finding(db, org), await _finding(db, org, "payments")
    await _link(db, org, first, status="stopped")
    # The same case may hold a new synced issue, and the stopped issue may be synced elsewhere.
    await _link(db, org, first, external_id="1002", external_key="acme/api#13")
    await _link(db, org, second)


async def test_one_issue_is_a_reference_on_two_cases_once_each(db: AsyncSession) -> None:
    org = await _org(db)
    first, second = await _finding(db, org), await _finding(db, org, "payments")
    await _link(db, org, first, role="reference")
    await _link(db, org, second, role="reference")
    refusal = await _refused(db, org=org, finding_id=first, role="reference")
    assert "uq_finding_links_issue_per_case" in refusal


# ------------------------------------------------------------ across tenants (AC72)


async def test_a_link_to_another_organization_s_finding_is_refused(db: AsyncSession) -> None:
    ours, theirs = await _org(db), await _org(db)
    their_finding = await _finding(db, theirs)
    assert "fk_finding_links_finding" in await _refused(db, org=ours, finding_id=their_finding)


async def test_a_link_through_another_organization_s_connection_is_refused(
    db: AsyncSession,
) -> None:
    ours, theirs = await _org(db), await _org(db)
    finding = await _finding(db, ours)
    refusal = await _refused(db, org=ours, finding_id=finding, connection_id=theirs.connection_id)
    assert "fk_finding_links_connection" in refusal


async def test_a_link_into_another_organization_s_repository_is_refused(db: AsyncSession) -> None:
    ours, theirs = await _org(db), await _org(db)
    finding = await _finding(db, ours)
    refusal = await _refused(db, org=ours, finding_id=finding, place_id=theirs.place_id)
    assert "fk_finding_links_place" in refusal


# ------------------------------------------------------------------ the checks


@pytest.mark.parametrize(
    "key",
    [
        "https://github.com/acme/api/issues/12",
        "acme/api#12 ",
        "OPS-1/../../myself",
        "acme/api",
    ],
)
async def test_an_address_where_a_key_belongs_is_refused(db: AsyncSession, key: str) -> None:
    org = await _org(db)
    finding = await _finding(db, org)
    assert "key_is_strict" in await _refused(db, org=org, finding_id=finding, external_key=key)


async def test_facts_live_only_while_creating(db: AsyncSession) -> None:
    org = await _org(db)
    finding = await _finding(db, org)
    refusal = await _refused(db, org=org, finding_id=finding, frozen_facts='{"signal": "slower"}')
    assert "facts_frozen_while_creating" in refusal
    refusal = await _refused(
        db, org=org, finding_id=finding, status="creating", external_id=None, external_key=None
    )
    assert "facts_frozen_while_creating" in refusal
    await _link(
        db,
        org,
        finding,
        status="creating",
        external_id=None,
        external_key=None,
        frozen_facts='{"signal": "slower"}',
    )


async def test_only_a_synced_link_is_created(db: AsyncSession) -> None:
    org = await _org(db)
    finding = await _finding(db, org)
    refusal = await _refused(
        db,
        org=org,
        finding_id=finding,
        role="reference",
        status="creating",
        external_id=None,
        external_key=None,
        frozen_facts="{}",
    )
    assert "only_a_synced_link_is_created" in refusal
