"""Create the local development tenant.

One organization, one owner, a `production` project and one API key — the same
four rows signup creates in a single transaction (P1-49), so what a developer
works against has the shape a real customer gets.

The API key is a fixed constant rather than a random string: the demo app, the
load generator and the e2e suite all need to know it without a handshake. That
is safe only because this database is local; nothing here may ever run against
a deployed environment.

    just seed
"""

import asyncio
import hashlib
from typing import Final

import structlog
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.data.postgres import postgres_dsn
from nexovue_api.data.postgres.models import (
    API_KEY_PREFIX_LENGTH,
    ApiKey,
    Organization,
    Project,
    User,
)

DEV_ORG_SLUG: Final = "acme"
DEV_ORG_NAME: Final = "Acme Inc"
DEV_OWNER_EMAIL: Final = "dev@nexovue.local"
DEV_OWNER_NAME: Final = "Dev Owner"
DEV_OWNER_PASSWORD: Final = "nexovue-dev"
DEV_PROJECT_SLUG: Final = "production"
DEV_PROJECT_NAME: Final = "Production"
DEV_API_KEY_NAME: Final = "local development"

# `nvk_live_` + 22 base62 characters, per the documented key format.
DEV_API_KEY: Final = "nvk_live_devlocaldevlocaldevloc"

_log = structlog.get_logger(__name__)


def hash_api_key(key: str) -> str:
    """SHA-256, never argon2 — the receiver verifies this on every request."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


async def seed(session: AsyncSession) -> bool:
    """Create the development tenant. Returns False if it already existed."""
    existing = await session.scalar(select(Organization).where(Organization.slug == DEV_ORG_SLUG))
    if existing is not None:
        return False

    organization = Organization(slug=DEV_ORG_SLUG, name=DEV_ORG_NAME)
    session.add(organization)
    # Flush rather than commit: the ids are wanted, the transaction is not done.
    await session.flush()

    session.add(
        User(
            org_id=organization.id,
            email=DEV_OWNER_EMAIL,
            name=DEV_OWNER_NAME,
            password_hash=PasswordHasher().hash(DEV_OWNER_PASSWORD),
            role="owner",
        )
    )

    project = Project(org_id=organization.id, slug=DEV_PROJECT_SLUG, name=DEV_PROJECT_NAME)
    session.add(project)
    await session.flush()

    session.add(
        ApiKey(
            org_id=organization.id,
            project_id=project.id,
            name=DEV_API_KEY_NAME,
            key_prefix=DEV_API_KEY[:API_KEY_PREFIX_LENGTH],
            key_hash=hash_api_key(DEV_API_KEY),
        )
    )
    return True


async def main() -> None:
    engine = create_async_engine(postgres_dsn())
    try:
        async with AsyncSession(engine) as session, session.begin():
            created = await seed(session)
    finally:
        await engine.dispose()

    if not created:
        _log.info("seed skipped: development tenant already exists", org_slug=DEV_ORG_SLUG)
        return

    _log.info(
        "development tenant created",
        organization=DEV_ORG_NAME,
        project=DEV_PROJECT_SLUG,
        login_email=DEV_OWNER_EMAIL,
        login_password=DEV_OWNER_PASSWORD,
        otlp_endpoint="http://localhost:4318",
        # The header this travels in is P1-13's to decide; only the key is known here.
        api_key=DEV_API_KEY,
    )


if __name__ == "__main__":
    asyncio.run(main())
