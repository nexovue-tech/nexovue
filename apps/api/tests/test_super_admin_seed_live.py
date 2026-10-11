"""nexovue's own operator, seeded from the environment (super-admin-in-the-console).

What it proves: the account is created once and only from settings; a second
boot changes nothing a caller can see; a password changed in the environment is
a password changed here; absent settings create nothing at all; and the role
grants nothing inside any organization.
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth.credentials import verify_password
from nexovue_api.auth.permissions import permissions_of
from nexovue_api.data.postgres.models import SUPER_ADMIN_ROLE, Organization, User
from nexovue_api.settings import Settings
from nexovue_api.superadmin.seed import PLATFORM_ORG_ID, platform_organization, seed_super_admin

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_super_admin_seed_live.py",
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


def _settings(**overrides: str) -> Settings:
    return Settings(**{**TEST_ENVIRONMENT, **overrides})  # type: ignore[arg-type]


def _operator_settings(password: str = "a long operator password") -> Settings:
    return _settings(
        SUPER_ADMIN_EMAIL=f"operator-{uuid.uuid4().hex[:8]}@nexovue.com",
        SUPER_ADMIN_PASSWORD=password,
    )


async def test_a_password_under_the_minimum_seeds_no_operator(db: AsyncSession) -> None:
    """The account that administers every customer must not be weaker than one a
    customer could create. Nothing stands between the environment and the hash
    here, so the sign-up form's own minimum is applied at this boundary."""
    settings = _operator_settings(password="short")

    assert await seed_super_admin(db, settings) is None


async def test_no_settings_seed_no_operator(db: AsyncSession) -> None:
    """A self-hosted install has no nexovue to administer it."""
    assert await seed_super_admin(db, _settings()) is None


async def test_the_operator_is_created_with_its_own_role(db: AsyncSession) -> None:
    settings = _operator_settings()

    operator = await seed_super_admin(db, settings)

    assert operator is not None
    assert operator.email == settings.super_admin_email
    assert operator.role == SUPER_ADMIN_ROLE
    assert verify_password(operator.password_hash, "a long operator password")
    organization = await platform_organization(db)
    assert organization is not None
    assert organization.id == PLATFORM_ORG_ID


async def test_the_operator_is_verified_on_sight(db: AsyncSession) -> None:
    """D9 (`docs/quality/console-review-2026-09-20.md`): the address comes from
    the environment, so there is nobody to confirm it — and the console was
    asking the operator to verify a `.test` address nothing can deliver to."""
    operator = await seed_super_admin(db, _operator_settings())

    assert operator is not None
    assert operator.email_verified_at is not None


async def test_seeding_twice_changes_nothing_a_caller_can_see(db: AsyncSession) -> None:
    settings = _operator_settings()
    first = await seed_super_admin(db, settings)
    assert first is not None

    second = await seed_super_admin(db, settings)

    assert second is not None
    assert second.id == first.id
    operators = (
        await db.scalars(select(User).where(User.email == settings.super_admin_email))
    ).all()
    assert len(operators) == 1
    organizations = (
        await db.scalars(select(Organization).where(Organization.id == PLATFORM_ORG_ID))
    ).all()
    assert len(organizations) == 1


async def test_the_environment_sets_the_password_once_and_never_again(db: AsyncSession) -> None:
    """Group 9, A3. Rewriting it every boot undid a password the operator changed in
    the console; it is set when the operator is made, as the first owner's is."""
    settings = _operator_settings()
    await seed_super_admin(db, settings)

    rotated = _settings(
        SUPER_ADMIN_EMAIL=settings.super_admin_email,
        SUPER_ADMIN_PASSWORD="an entirely different password",
    )
    operator = await seed_super_admin(db, rotated)

    assert operator is not None
    assert verify_password(operator.password_hash, "a long operator password")
    assert not verify_password(operator.password_hash, "an entirely different password")


async def test_a_removed_operator_stays_removed(db: AsyncSession) -> None:
    settings = _operator_settings()
    operator = await seed_super_admin(db, settings)
    assert operator is not None
    operator.removed_at = datetime.now(UTC)
    await db.flush()

    again = await seed_super_admin(db, settings)

    assert again is not None
    assert again.removed_at is not None


def test_the_platform_role_grants_nothing_inside_an_organization() -> None:
    """It is not a tenant role: every gate in `auth/permissions.py` refuses it,
    exactly as it refuses a viewer."""
    assert permissions_of(SUPER_ADMIN_ROLE) == []
