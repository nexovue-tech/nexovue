"""The one account that administers the platform (`docs/changes/super-admin-in-the-console/`).

It is an ordinary `users` row — same sessions, same password rules, same second
factor, same audit log — with a role that grants nothing inside any
organization and everything on the platform surface, and only while the session
has been re-authenticated recently.

It is seeded from the environment rather than created by a screen, for the
reason the terminal command it replaces had: the most powerful account in the
system should not be creatable by anything a request can reach. Absent settings
seed nothing, which is the right answer for a self-hosted install — it has no
nexovue to administer it.

The reserved organization exists because `users.org_id` is NOT NULL and every
session names a tenant. It holds the operator and nothing else: no projects, no
telemetry, no bill.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.credentials import hash_password, normalize_email
from nexovue_api.data.postgres.models import SUPER_ADMIN_ROLE, Organization, User
from nexovue_api.settings import Settings

_log: Final = structlog.get_logger(__name__)

# A fixed id, not a slug, because a slug can be taken: signup turns a company
# name into `[a-z0-9-]`, so a customer calling themselves "nexovue" would reach
# that slug first and this would then adopt *their* organization as the
# platform's. An id nothing else can produce cannot be confused with anyone.
PLATFORM_ORG_ID: Final = uuid.UUID("00000000-0000-7000-8000-000000000001")
PLATFORM_SLUG: Final = "nexovue"
PLATFORM_NAME: Final = "nexovue"
_SLUG_ATTEMPTS: Final = 10


async def platform_organization(session: AsyncSession) -> Organization | None:
    """The organization the operator belongs to, if it has been seeded."""
    return await session.get(Organization, PLATFORM_ORG_ID)


async def seed_super_admin(session: AsyncSession, settings: Settings) -> User | None:
    """Create or update the super admin. Idempotent; returns None when unset.

    Updating matters as much as creating: the environment is where the password
    lives, so changing it there and restarting is how it is rotated. Nothing
    here is logged but the address.
    """
    email, password = settings.super_admin_email, settings.super_admin_password.get_secret_value()
    if not email or not password:
        return None

    address = normalize_email(email)
    organization = await platform_organization(session)
    if organization is None:
        organization = Organization(
            id=PLATFORM_ORG_ID, slug=await _free_slug(session), name=PLATFORM_NAME
        )
        session.add(organization)
        await session.flush()

    operator = await session.scalar(select(User).where(User.email == address))
    if operator is None:
        operator = User(
            org_id=organization.id,
            email=address,
            name=PLATFORM_NAME,
            password_hash=hash_password(password),
            role=SUPER_ADMIN_ROLE,
            # Verified on sight. The address comes from the environment, not from
            # somebody typing it, so there is nothing to confirm — and the console
            # was asking the operator to verify an address that is often a `.test`
            # one nothing can deliver to
            # (`docs/quality/console-review-2026-09-20.md`, D9).
            email_verified_at=datetime.now(UTC),
        )
        session.add(operator)
        await session.flush()
        _log.info("super admin seeded", email=address)
        return operator

    _adopt(operator, organization_id=organization.id, password=password)
    return operator


async def _free_slug(session: AsyncSession) -> str:
    """`nexovue`, or a suffixed one if a customer got there first.

    The slug is a URL label and nothing here depends on its value; what must not
    happen is the insert failing on a unique constraint and leaving the platform
    with no operator.
    """
    for attempt in range(_SLUG_ATTEMPTS):
        candidate = PLATFORM_SLUG if attempt == 0 else f"{PLATFORM_SLUG}-{attempt + 1}"
        taken = await session.scalar(select(Organization.id).where(Organization.slug == candidate))
        if taken is None:
            return candidate
    raise RuntimeError("no free slug for the platform organization")


def _adopt(operator: User, *, organization_id: uuid.UUID, password: str) -> None:
    """Make an existing row match the environment.

    A password that has not changed is re-hashed to the same *verifier*, not the
    same string — Argon2 salts every hash — so this cannot be compared. It is
    written every boot, which is cheap and makes rotation one restart.
    """
    operator.org_id = organization_id
    operator.role = SUPER_ADMIN_ROLE
    operator.password_hash = hash_password(password)
    operator.removed_at = None
    if operator.email_verified_at is None:
        operator.email_verified_at = datetime.now(UTC)
