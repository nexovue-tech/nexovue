"""The development seed, run against real Postgres.

The offline assertions check the key's shape. They cannot check that the row is
written, that its foreign keys resolve, or that the origins survive the ARRAY
column — and a seed that silently writes no site key looks exactly like a seed
that wrote one until the browser door refuses.

The whole thing runs inside a transaction that is rolled back, including the
delete that makes the seed think it is running on a fresh database. Nothing here
touches the development tenant a developer is using.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.data.postgres.models import Organization, SiteKey
from nexovue_api.seed import (
    DEV_ORG_SLUG,
    DEV_SITE_KEY,
    DEV_SITE_KEY_ORIGINS,
    seed,
)

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

REACHABILITY_TIMEOUT_SECONDS: Final = 1.0


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or 5432), REACHABILITY_TIMEOUT_SECONDS
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_seed_live.py",
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


@pytest.fixture
async def fresh_database(db: AsyncSession) -> AsyncSession:
    """A database with no development tenant, which is what the seed expects.

    The delete is inside the rolled-back transaction, so the tenant a developer
    is logged in as is still there when this returns.
    """
    await db.execute(delete(Organization).where(Organization.slug == DEV_ORG_SLUG))
    await db.flush()
    return db


async def test_the_seed_writes_a_usable_browser_key(fresh_database: AsyncSession) -> None:
    created = await seed(fresh_database)
    assert created, "the seed reported the tenant already existed"
    await fresh_database.flush()

    key = await fresh_database.scalar(select(SiteKey).where(SiteKey.public_key == DEV_SITE_KEY))

    assert key is not None, "the seed wrote no site key, so the browser door has no way in"
    assert key.revoked_at is None
    # The ARRAY column round trip: a key with no origins accepts nothing, and
    # that failure is indistinguishable from the SDK being broken.
    assert list(key.allowed_origins) == list(DEV_SITE_KEY_ORIGINS)


async def test_the_browser_key_belongs_to_the_tenant_the_seed_made(
    fresh_database: AsyncSession,
) -> None:
    """The foreign keys resolve to the seeded organization, not to nothing.

    A key on the wrong project authenticates and then writes another tenant's
    data, which is the one failure worse than not working.
    """
    await seed(fresh_database)
    await fresh_database.flush()

    organization = await fresh_database.scalar(
        select(Organization).where(Organization.slug == DEV_ORG_SLUG)
    )
    key = await fresh_database.scalar(select(SiteKey).where(SiteKey.public_key == DEV_SITE_KEY))

    assert organization is not None
    assert key is not None
    assert key.org_id == organization.id


async def test_a_second_seed_brings_a_stale_origin_list_up_to_date(db: AsyncSession) -> None:
    """The one thing a second seed does change, and why it has to.

    A dev database seeded before the demo storefront existed carries an origin
    list that refuses it. Nothing else would fix that: there is no update
    endpoint for a site key's origins, and the failure is a 403 that reads like a
    broken SDK rather than like stale seed data.
    """
    key = await db.scalar(select(SiteKey).where(SiteKey.public_key == DEV_SITE_KEY))
    assert key is not None, "the dev site key is missing; seed the database first"
    key.allowed_origins = ["http://localhost:15173"]
    await db.flush()

    await seed(db)
    await db.flush()

    refreshed = await db.scalar(select(SiteKey).where(SiteKey.public_key == DEV_SITE_KEY))
    assert refreshed is not None
    assert list(refreshed.allowed_origins) == list(DEV_SITE_KEY_ORIGINS)
    # The demo storefront is reachable from two spellings and both must be there.
    assert "http://localhost:8080" in refreshed.allowed_origins
    assert "http://frontend-proxy:8080" in refreshed.allowed_origins


async def test_a_revoked_key_is_left_alone(db: AsyncSession) -> None:
    """A developer who retired the key on purpose keeps that decision."""
    key = await db.scalar(select(SiteKey).where(SiteKey.public_key == DEV_SITE_KEY))
    assert key is not None
    key.allowed_origins = ["http://localhost:15173"]
    key.revoked_at = datetime.now(UTC)
    await db.flush()

    await seed(db)
    await db.flush()

    refreshed = await db.scalar(select(SiteKey).where(SiteKey.public_key == DEV_SITE_KEY))
    assert refreshed is not None
    assert list(refreshed.allowed_origins) == ["http://localhost:15173"]


async def test_seeding_a_database_that_already_has_the_tenant_changes_nothing(
    db: AsyncSession,
) -> None:
    """`just seed` is run repeatedly, so a second run must not mint a second key."""
    created = await seed(db)

    assert created is False
