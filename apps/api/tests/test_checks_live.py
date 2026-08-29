"""Checks against real Postgres: one tenant cannot reach another's (P1-33).

The offline suite proves the queries carry a tenant predicate. This proves the
database agrees — two real organizations, real rows, and every cross-tenant
attempt answered as though the row did not exist.

Everything runs inside a transaction that is rolled back, so the suite leaves
nothing behind.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.checks import service
from nexovue_api.checks.schemas import CheckSummary, CreateCheckRequest, UpdateCheckRequest
from nexovue_api.data.postgres.models import AuditLogEntry
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

TARGET: Final = "https://example.com/health"


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection((parts.hostname or "", parts.port or 5432), timeout=2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="the live check isolation suite",
    remedy="Postgres is not running — start it with `just dev`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A session in a transaction that is always rolled back."""
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _new_tenant(db: AsyncSession) -> ProvisionedTenant:
    unique = uuid.uuid4().hex[:12]
    return await provision_tenant(
        db,
        organization_name=f"Checks Live {unique}",
        email=f"owner-{unique}@live.test",
        password="correct-horse-battery",
    )


async def _create(
    db: AsyncSession, tenant: ProvisionedTenant, *, target: str = TARGET
) -> CheckSummary:
    return await service.create(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        request=CreateCheckRequest(
            project_id=tenant.project.id,
            type="http",
            target=target,
            interval_seconds=60,
        ),
    )


async def _audit_actions(db: AsyncSession, org_id: uuid.UUID) -> Sequence[str]:
    rows = await db.scalars(select(AuditLogEntry).where(AuditLogEntry.org_id == org_id))
    return [entry.action for entry in rows]


async def test_a_check_is_created_and_listed_for_its_own_project(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    created = await _create(db, tenant)

    listed = await service.list_for_project(
        db,
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
    )

    assert [check.id for check in listed.checks] == [created.id]
    assert listed.checks[0].enabled is True


async def test_another_organizations_check_cannot_be_read(db: AsyncSession) -> None:
    """A 404, not a 403: whether the row exists is itself not theirs to learn."""
    owner, stranger = await _new_tenant(db), await _new_tenant(db)
    theirs = await _create(db, owner)

    with pytest.raises(NotFoundError):
        await service.update(
            db,
            org_id=stranger.organization.id,
            actor_user_id=stranger.user.id,
            check_id=theirs.id,
            request=UpdateCheckRequest(enabled=False),
        )


async def test_another_organizations_check_cannot_be_deleted(db: AsyncSession) -> None:
    """The one that destroys data rather than merely leaking it."""
    owner, stranger = await _new_tenant(db), await _new_tenant(db)
    theirs = await _create(db, owner)

    with pytest.raises(NotFoundError):
        await service.remove(
            db,
            org_id=stranger.organization.id,
            actor_user_id=stranger.user.id,
            check_id=theirs.id,
        )

    still_there = await service.list_for_project(
        db,
        org_id=owner.organization.id,
        project_id=owner.project.id,
    )
    assert [check.id for check in still_there.checks] == [theirs.id]


async def test_a_check_cannot_be_created_on_another_organizations_project(
    db: AsyncSession,
) -> None:
    """project_id arrives in the request body, so it is a value the caller chose.

    Without the project gate a stranger could plant a check inside somebody
    else's project and have its readings stored under their tenancy.
    """
    owner, stranger = await _new_tenant(db), await _new_tenant(db)

    with pytest.raises(NotFoundError):
        await service.create(
            db,
            org_id=stranger.organization.id,
            actor_user_id=stranger.user.id,
            request=CreateCheckRequest(
                project_id=owner.project.id,
                target=TARGET,
                interval_seconds=60,
            ),
        )


async def test_a_target_inside_our_network_is_refused_before_it_is_stored(
    db: AsyncSession,
) -> None:
    """The check would otherwise have our own prober read the metadata service."""
    tenant = await _new_tenant(db)

    with pytest.raises(InvalidRequestError):
        await _create(db, tenant, target="http://169.254.169.254/latest/meta-data/")

    listed = await service.list_for_project(
        db,
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
    )
    assert listed.checks == []


async def test_creating_and_deleting_record_who_did_it(db: AsyncSession) -> None:
    """The trail commits with the action, so there is no check without one."""
    tenant = await _new_tenant(db)
    created = await _create(db, tenant)
    await service.remove(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        check_id=created.id,
    )

    actions = await _audit_actions(db, tenant.organization.id)
    assert "check.created" in actions
    assert "check.deleted" in actions
