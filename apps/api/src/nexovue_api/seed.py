"""Create the local development tenant.

One organization, one owner, a `production` project and one API key — created
by calling signup's own service, so what a developer works against is the same
four rows a real customer gets, produced by the same code (P1-49).

Two things differ from a real signup, and both are passed in explicitly rather
than reimplemented: the organization keeps the short `acme` slug, and the API
key is a fixed constant. The demo app, the load generator and the e2e suite all
need to know that key without a handshake. That is safe only because this
database is local; nothing here may ever run against a deployed environment.

    just seed
"""

import asyncio
import os
import uuid
from typing import Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.data.postgres import postgres_dsn
from nexovue_api.data.postgres.models import Organization, SiteKey
from nexovue_api.settings import DEFAULT_OTLP_ENDPOINT
from nexovue_api.signup.service import provision_tenant

DEV_ORG_SLUG: Final = "acme"
DEV_ORG_NAME: Final = "Acme Inc"
DEV_OWNER_EMAIL: Final = "nexovue@nexovue.com"
DEV_OWNER_NAME: Final = "Dev Owner"
DEV_OWNER_PASSWORD: Final = "nexovue-dev"
DEV_PROJECT_SLUG: Final = "production"
DEV_API_KEY_NAME: Final = "local development"

# `nvk_live_` + 22 base62 characters, per the documented key format.
DEV_API_KEY: Final = "nvk_live_devlocaldevlocaldevloc"

# Door 3's key, the same idea one door over: a known public value so the browser
# SDK, the e2e suite and the load generator can all reach a fresh stack without
# minting one by hand first. `nvp_live_` + 22 base62 characters.
#
# Created here rather than in `provision_tenant`, which is the real signup path:
# a customer signing up should not silently receive a public browser key scoped
# to localhost.
DEV_SITE_KEY: Final = "nvp_live_devlocaldevlocaldevloc"
DEV_SITE_KEY_NAME: Final = "local development"

# Every origin the dev stack serves a page from. A site key with an origin list
# that misses one refuses 100% of that origin's traffic, and the failure looks
# exactly like the SDK not working.
#
# The last two are the OpenTelemetry Demo storefront, which `just otel-demo up`
# serves through Envoy. Two origins for one page: a person opens it on localhost,
# and the demo's own load generator opens it by compose service name from inside
# the demo's network.
DEV_SITE_KEY_ORIGINS: Final = [
    "http://localhost:15173",
    "http://127.0.0.1:15173",
    "http://localhost:8080",
    "http://frontend-proxy:8080",
]

_log = structlog.get_logger(__name__)


async def seed(session: AsyncSession) -> bool:
    """Create the development tenant. Returns False if it already existed."""
    existing = await session.scalar(select(Organization).where(Organization.slug == DEV_ORG_SLUG))
    if existing is not None:
        await _reconcile_site_key_origins(session, org_id=existing.id)
        return False

    tenant = await provision_tenant(
        session,
        organization_name=DEV_ORG_NAME,
        email=DEV_OWNER_EMAIL,
        password=DEV_OWNER_PASSWORD,
        name=DEV_OWNER_NAME,
        organization_slug=DEV_ORG_SLUG,
        api_key=DEV_API_KEY,
        api_key_name=DEV_API_KEY_NAME,
    )
    session.add(
        SiteKey(
            org_id=tenant.organization.id,
            project_id=tenant.project.id,
            name=DEV_SITE_KEY_NAME,
            public_key=DEV_SITE_KEY,
            allowed_origins=list(DEV_SITE_KEY_ORIGINS),
        )
    )
    return True


async def _reconcile_site_key_origins(session: AsyncSession, *, org_id: uuid.UUID) -> None:
    """Bring the dev site key's origins up to date on an already-seeded database.

    The one deliberate exception to "the seed creates, it does not update", and
    it earns it: the origin list is the only seeded value that changes as the dev
    stack grows a page to serve. A stale list refuses 100% of that origin's
    traffic with a 403 that reads exactly like a broken SDK — a failure nobody
    would think to blame on a seed that ran months ago.

    Everything else here stays create-once. A developer who has revoked or edited
    the key on purpose keeps that: a revoked key is left alone.

    `org_id` is taken rather than trusted to the unique constraint on
    `public_key`. The caller has the organization already, so it costs no query,
    and `site_keys` is tenant-scoped — a statement against it that names no
    tenant is the shape `test_postgres_tenancy_structure.py` exists to refuse,
    and that guard is worth more than the one exemption it would take to silence
    it here.
    """
    key = await session.scalar(
        select(SiteKey).where(
            SiteKey.org_id == org_id,
            SiteKey.public_key == DEV_SITE_KEY,
            SiteKey.revoked_at.is_(None),
        )
    )
    if key is None or list(key.allowed_origins) == DEV_SITE_KEY_ORIGINS:
        return

    added = [origin for origin in DEV_SITE_KEY_ORIGINS if origin not in key.allowed_origins]
    key.allowed_origins = list(DEV_SITE_KEY_ORIGINS)
    _log.info(
        "development site key origins reconciled",
        public_key=DEV_SITE_KEY,
        added=added,
        origins=DEV_SITE_KEY_ORIGINS,
    )


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
        otlp_endpoint=os.environ.get("OTLP_ENDPOINT", DEFAULT_OTLP_ENDPOINT),
        # The header this travels in is P1-13's to decide; only the key is known here.
        api_key=DEV_API_KEY,
        site_key=DEV_SITE_KEY,
        site_key_origins=DEV_SITE_KEY_ORIGINS,
    )


if __name__ == "__main__":
    asyncio.run(main())
