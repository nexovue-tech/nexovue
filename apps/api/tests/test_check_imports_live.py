"""An import against real Postgres: preview writes nothing, confirm writes it all (FR11).

Everything runs inside a transaction that is rolled back, so the suite leaves
nothing behind.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from nexovue_platform.checks.config import ApiCheckConfig, BearerAuth, parse_config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.checkimports import service
from nexovue_api.checkimports.schemas import (
    ImportConfirmRequest,
    ImportedCheck,
    ImportedSecret,
    ImportPreviewRequest,
)
from nexovue_api.checks import service as checks
from nexovue_api.data.postgres.models import AuditLogEntry, CheckSecret
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

# A development key, never a deployment's: 32 random bytes, base64.
CHECK_SECRET_KEY: Final = "qPYHqC1y6hD0s6knf12GgLTcvJsKkPdzaDgvE8sAFA4="
LOCATIONS: Final = ["local"]
TOKEN: Final = "tok-live-import-5566"
COMMAND: Final = (
    "curl -X POST https://api.example.com/v1/orders "
    f"-H 'Authorization: Bearer {TOKEN}' "
    """-H 'Content-Type: application/json' -d '{"sku": "A-1"}'"""
)


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection((parts.hostname or "", parts.port or 5432), timeout=2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="the live check import suite",
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
        organization_name=f"Imports Live {unique}",
        email=f"owner-{unique}@live.test",
        password="correct-horse-battery",
    )


async def _check_ids(db: AsyncSession, tenant: ProvisionedTenant) -> list[uuid.UUID]:
    listed = await checks.list_for_project(
        db,
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        available_locations=LOCATIONS,
    )
    return [check.id for check in listed.checks]


async def test_a_curl_import_is_previewed_without_saving_then_confirmed_with_its_secret(
    db: AsyncSession,
) -> None:
    tenant = await _new_tenant(db)

    plan = await service.preview(
        db,
        org_id=tenant.organization.id,
        request=ImportPreviewRequest(project_id=tenant.project.id, source="curl", content=COMMAND),
    )
    assert await _check_ids(db, tenant) == [], "a preview must save nothing (AC7)"

    item = plan.items[0]
    confirmed = await service.confirm(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        request=ImportConfirmRequest(
            project_id=tenant.project.id,
            checks=[ImportedCheck(name=item.name, config=item.config)],
            secrets=[ImportedSecret(name=s.name, value=s.value) for s in plan.secrets],
        ),
        check_secret_key=CHECK_SECRET_KEY,
        available_locations=LOCATIONS,
    )

    assert await _check_ids(db, tenant) == confirmed.created
    stored = await checks.get(db, org_id=tenant.organization.id, check_id=confirmed.created[0])
    config = parse_config(stored.config.model_dump(mode="json"))
    assert isinstance(config, ApiCheckConfig)
    assert config.request.auth == BearerAuth(token_secret="API_EXAMPLE_COM_TOKEN")

    secret = await db.scalar(
        select(CheckSecret).where(
            CheckSecret.org_id == tenant.organization.id,
            CheckSecret.project_id == tenant.project.id,
            CheckSecret.name == "API_EXAMPLE_COM_TOKEN",
        )
    )
    assert secret is not None
    assert TOKEN.encode() not in secret.sealed_value, "a secret is stored sealed"

    actions = await db.scalars(
        select(AuditLogEntry.action).where(AuditLogEntry.org_id == tenant.organization.id)
    )
    recorded = list(actions)
    assert "check.imported" in recorded
    assert "check.created" in recorded


async def test_a_refused_check_is_named_by_its_position_in_the_import(db: AsyncSession) -> None:
    tenant = await _new_tenant(db)
    plan = await service.preview(
        db,
        org_id=tenant.organization.id,
        request=ImportPreviewRequest(
            project_id=tenant.project.id,
            source="curl",
            content="curl http://169.254.169.254/latest/meta-data/",
        ),
    )

    with pytest.raises(InvalidRequestError) as refused:
        await service.confirm(
            db,
            org_id=tenant.organization.id,
            actor_user_id=tenant.user.id,
            request=ImportConfirmRequest(
                project_id=tenant.project.id,
                checks=[ImportedCheck(name=plan.items[0].name, config=plan.items[0].config)],
            ),
            check_secret_key=CHECK_SECRET_KEY,
            available_locations=LOCATIONS,
        )

    errors = refused.value.extensions["errors"]
    assert errors == [
        {
            "field": "checks.0.config.request.url",
            "reason": "A target must be a public address, not a private one.",
        }
    ]


async def test_another_organizations_project_cannot_be_previewed(db: AsyncSession) -> None:
    owner, stranger = await _new_tenant(db), await _new_tenant(db)

    with pytest.raises(NotFoundError):
        await service.preview(
            db,
            org_id=stranger.organization.id,
            request=ImportPreviewRequest(
                project_id=owner.project.id, source="curl", content=COMMAND
            ),
        )
