"""Deploy webhooks against real Postgres: signed, stored, and tenant-scoped (P1-36).

The offline suite proves the queries carry a tenant predicate and that nothing
unsigned reaches the store. This proves the database agrees — two real
organizations, real rows, and a real signature computed the way GitHub computes
one, over the exact bytes.

The cross-tenant case is the one that matters: a customer holding their own
project's deploy secret must not be able to plant a deploy in somebody else's
project. A global secret would let them, which is why the secret is derived per
project.

Everything runs inside a transaction that is rolled back, so the suite leaves
nothing behind.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.data.postgres.models import AuditLogEntry, DeployEvent
from nexovue_api.deploys import service
from nexovue_api.errors.exceptions import NotFoundError, UnauthenticatedError
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

VERSION: Final = "2026.08.29-1"
SERVICE: Final = "checkout"


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection((parts.hostname or "", parts.port or 5432), timeout=2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="the live deploy webhook suite",
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
        organization_name=f"Deploys Live {unique}",
        email=f"owner-{unique}@live.test",
        password="correct-horse-battery",
    )


async def _secret_for(db: AsyncSession, tenant: ProvisionedTenant) -> str:
    """The secret a customer would copy out of the console, read the same way."""
    response = await service.webhook_secrets(
        db,
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        actor_user_id=tenant.user.id,
    )
    assert len(response.secrets) == 1, "signup mints exactly one ingest key"
    return response.secrets[0].secret


def _body(**overrides: Any) -> bytes:
    payload: dict[str, Any] = {"version": VERSION, "service_name": SERVICE}
    payload.update(overrides)
    return json.dumps(payload).encode()


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def _stored_deploys(db: AsyncSession, org_id: uuid.UUID) -> Sequence[DeployEvent]:
    return (await db.scalars(select(DeployEvent).where(DeployEvent.org_id == org_id))).all()


async def _audit_actions(db: AsyncSession, org_id: uuid.UUID) -> Sequence[str]:
    rows = await db.scalars(select(AuditLogEntry).where(AuditLogEntry.org_id == org_id))
    return [entry.action for entry in rows]


async def test_a_signed_deploy_is_stored_under_the_projects_own_tenancy(
    db: AsyncSession,
) -> None:
    tenant = await _new_tenant(db)
    body = _body()

    summary = await service.record_from_webhook(
        db,
        project_id=tenant.project.id,
        body=body,
        signature=_sign(body, await _secret_for(db, tenant)),
    )

    assert summary.version == VERSION
    stored = await _stored_deploys(db, tenant.organization.id)
    assert [row.id for row in stored] == [summary.id]
    assert stored[0].org_id == tenant.organization.id
    assert stored[0].project_id == tenant.project.id


async def test_a_deploy_signed_with_another_projects_secret_is_refused(
    db: AsyncSession,
) -> None:
    """The cross-tenant attempt. A stranger holds a real secret — their own."""
    owner, stranger = await _new_tenant(db), await _new_tenant(db)
    body = _body()
    strangers_secret = await _secret_for(db, stranger)

    with pytest.raises(UnauthenticatedError):
        await service.record_from_webhook(
            db,
            project_id=owner.project.id,
            body=body,
            signature=_sign(body, strangers_secret),
        )

    assert await _stored_deploys(db, owner.organization.id) == []


async def test_an_unsigned_deploy_is_refused(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)

    with pytest.raises(UnauthenticatedError):
        await service.record_from_webhook(
            db, project_id=tenant.project.id, body=_body(), signature=None
        )

    assert await _stored_deploys(db, tenant.organization.id) == []


async def test_a_body_altered_after_signing_is_refused(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    body = _body()
    signature = _sign(body, await _secret_for(db, tenant))

    with pytest.raises(UnauthenticatedError):
        await service.record_from_webhook(
            db,
            project_id=tenant.project.id,
            body=body.replace(SERVICE.encode(), b"payments"),
            signature=signature,
        )

    assert await _stored_deploys(db, tenant.organization.id) == []


async def test_a_deploy_aimed_at_a_project_that_does_not_exist_is_refused(
    db: AsyncSession,
) -> None:
    tenant = await _new_tenant(db)
    body = _body()

    with pytest.raises(UnauthenticatedError):
        await service.record_from_webhook(
            db,
            project_id=uuid.uuid4(),
            body=body,
            signature=_sign(body, await _secret_for(db, tenant)),
        )


async def test_another_organizations_deploys_cannot_be_listed(db: AsyncSession) -> None:
    """A 404, not a 403: whether the project exists is not theirs to learn."""
    owner, stranger = await _new_tenant(db), await _new_tenant(db)
    body = _body()
    await service.record_from_webhook(
        db,
        project_id=owner.project.id,
        body=body,
        signature=_sign(body, await _secret_for(db, owner)),
    )

    with pytest.raises(NotFoundError):
        await service.list_for_project(
            db,
            org_id=stranger.organization.id,
            project_id=owner.project.id,
            limit=10,
        )

    mine = await service.list_for_project(
        db, org_id=owner.organization.id, project_id=owner.project.id, limit=10
    )
    assert [row.version for row in mine.deploys] == [VERSION]


async def test_another_organizations_webhook_secret_cannot_be_read(db: AsyncSession) -> None:
    """The one that would make every other guard here pointless."""
    owner, stranger = await _new_tenant(db), await _new_tenant(db)

    with pytest.raises(NotFoundError):
        await service.webhook_secrets(
            db,
            org_id=stranger.organization.id,
            project_id=owner.project.id,
            actor_user_id=stranger.user.id,
        )


async def test_storing_a_deploy_and_reading_a_secret_are_both_recorded(
    db: AsyncSession,
) -> None:
    """The trail commits with the action, so there is no deploy without one."""
    tenant = await _new_tenant(db)
    body = _body()
    await service.record_from_webhook(
        db,
        project_id=tenant.project.id,
        body=body,
        signature=_sign(body, await _secret_for(db, tenant)),
    )

    actions = await _audit_actions(db, tenant.organization.id)
    assert "deploy.recorded" in actions
    assert "deploy.webhook_secret_viewed" in actions


async def test_no_audit_entry_carries_the_secret(db: AsyncSession) -> None:
    """A log that records the credential is worse than no log."""
    tenant = await _new_tenant(db)
    secret = await _secret_for(db, tenant)
    body = _body()
    await service.record_from_webhook(
        db, project_id=tenant.project.id, body=body, signature=_sign(body, secret)
    )
    await db.flush()

    statement = select(AuditLogEntry).where(AuditLogEntry.org_id == tenant.organization.id)
    for row in (await db.scalars(statement)).all():
        assert secret not in row.target
        assert secret not in (row.actor_label or "")
