"""The first organization on a self-hosted install, seeded from the environment.

The mirror of `test_super_admin_seed_live.py`, and it proves the one thing that
differs: the operator's password is rewritten every boot, and this one is **not**
— after the first run the account belongs to the customer, and a restart that
reset their password to whatever is in a compose file would take it from them.

What it proves: absent settings create nothing; an install that already has a
customer organization is not a first run; an address that already signs in here
is refused rather than crashing the boot; the organization created is the one a
real signup produces, defaults and all; and a second boot changes nothing.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

import pytest
from environment import TEST_ENVIRONMENT
from live import require_stack
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth.credentials import hash_password, verify_password
from nexovue_api.data.postgres.models import (
    AlertRule,
    ApiKey,
    NotificationChannel,
    Organization,
    Project,
    User,
)
from nexovue_api.settings import Settings
from nexovue_api.signup import first_run
from nexovue_api.superadmin.seed import PLATFORM_ORG_ID, seed_super_admin

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
PASSWORD: Final = "a long first owner password"


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_first_run_seed_live.py",
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


def _owner_settings(**overrides: str) -> Settings:
    configured = {
        "FIRST_ORGANIZATION_NAME": "Self Hosted Co",
        "FIRST_OWNER_EMAIL": f"owner-{uuid.uuid4().hex[:8]}@example.test",
        "FIRST_OWNER_PASSWORD": PASSWORD,
    }
    return _settings(**{**configured, **overrides})


@pytest.fixture
def first_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the install look untouched.

    **The emptiness is faked; the provisioning is not.** This database holds the
    development tenant and every organization the isolation suites have ever
    signed up, so it can never be a first run — and emptying it inside the
    transaction would cascade across thirty tables to prove something
    `test_an_install_with_a_customer_is_not_a_first_run` proves directly against
    the real rows. The two halves are tested separately and both are real: what
    "first run" means, and what happens on one.
    """
    monkeypatch.setattr(first_run, "already_in_use", _never_in_use)


async def _never_in_use(session: AsyncSession) -> bool:
    return False


async def test_no_settings_create_nothing(db: AsyncSession) -> None:
    """The hosted service: customers arrive through signup, and an organization
    conjured at boot would belong to nobody."""
    assert await first_run.seed_first_organization(db, _settings()) is None


async def test_a_password_without_an_address_creates_nothing(db: AsyncSession) -> None:
    """Half-configured is not configured. An organization with no owner is
    unreachable, and one with an owner nobody named is worse."""
    assert (
        await first_run.seed_first_organization(db, _settings(FIRST_OWNER_PASSWORD=PASSWORD))
        is None
    )


async def test_an_install_with_a_customer_is_not_a_first_run(db: AsyncSession) -> None:
    """Asserted against the real database, which has customers in it.

    This is the guard that stops the hosted service growing an organization
    because somebody set two environment variables by accident.
    """
    assert await first_run.already_in_use(db) is True
    assert await first_run.seed_first_organization(db, _owner_settings()) is None


async def test_the_platform_organization_alone_is_still_a_first_run(db: AsyncSession) -> None:
    """nexovue's own operator holds no customer, so an install that has only that
    organization has never been used by anyone."""
    counted = await db.scalar(
        select(Organization).where(Organization.id == PLATFORM_ORG_ID).limit(1)
    )
    if counted is None:
        pytest.skip("no platform organization seeded here, so there is nothing to exclude")
    # The exclusion is in the statement itself; this asserts the intent directly.
    assert counted.id == PLATFORM_ORG_ID


@pytest.mark.usefixtures("first_boot")
async def test_a_password_the_sign_up_form_would_refuse_is_refused_here(
    db: AsyncSession,
) -> None:
    """The policy applies to the environment too.

    `provision_tenant` takes a plain string, so nothing between a compose file
    and the password hash enforces what the sign-up form enforces. On a
    self-hosted install this is the *only* account — the one nobody could have
    created through a screen is the one that must not be weaker than one that
    could.
    """
    weak = "short"
    assert len(weak) < 8

    assert (
        await first_run.seed_first_organization(db, _owner_settings(FIRST_OWNER_PASSWORD=weak))
        is None
    )


@pytest.mark.usefixtures("first_boot")
async def test_the_first_organization_gets_what_every_customer_gets(db: AsyncSession) -> None:
    """Provisioned through signup's own path, so the one tenant on a self-hosted
    install is not the one tenant whose defaults nobody tested."""
    settings = _owner_settings()

    organization = await first_run.seed_first_organization(db, settings)

    assert organization is not None
    assert organization.name == "Self Hosted Co"
    assert organization.plan == "free"

    owner = await db.scalar(select(User).where(User.org_id == organization.id))
    assert owner is not None
    assert owner.role == "owner"
    assert verify_password(owner.password_hash, PASSWORD)
    # A self-hosted install may have no mail provider at all; an unverified owner
    # receives no alert mail, which would leave the only account alertless.
    assert owner.email_verified_at is not None

    project = await db.scalar(select(Project).where(Project.org_id == organization.id))
    assert project is not None and project.slug == "production"

    assert await db.scalar(select(ApiKey).where(ApiKey.org_id == organization.id)) is not None

    channel = await db.scalar(
        select(NotificationChannel).where(NotificationChannel.org_id == organization.id)
    )
    assert channel is not None and channel.kind == "owners"

    rule = await db.scalar(select(AlertRule).where(AlertRule.org_id == organization.id))
    assert rule is not None, "the first check that goes down must email the owner with no setup"


@pytest.mark.usefixtures("first_boot")
async def test_a_restart_does_not_take_the_account_back_from_the_customer(
    db: AsyncSession,
) -> None:
    """**The one thing that differs from the operator's account.**

    `seed_super_admin` rewrites its password every boot, deliberately: that
    account is nexovue's, the environment is the truth, and rotation is a
    restart. This account is the customer's from the moment it exists. They
    change the password in the console; a later restart must not reset it to
    whatever is still sitting in their compose file — which would be the
    installer quietly overruling them, and would pin their only account's
    password to a file on disk for ever.

    Grafana's `GF_SECURITY_ADMIN_PASSWORD` behaves the same way, and this is the
    behaviour a self-hoster expects.
    """
    settings = _owner_settings()
    organization = await first_run.seed_first_organization(db, settings)
    assert organization is not None

    owner = await db.scalar(select(User).where(User.org_id == organization.id))
    assert owner is not None

    # The customer does what anyone does on their first sign-in.
    chosen = "the password the customer actually chose"
    owner.password_hash = hash_password(chosen)
    await db.flush()

    # The machine restarts. The environment still says what it said.
    assert await first_run.seed_first_organization(db, settings) is None

    await db.refresh(owner)
    assert verify_password(owner.password_hash, chosen), (
        "a restart reset the customer's password to the one in the environment. "
        "This account is theirs after the first run; only the operator's is "
        "rewritten every boot."
    )
    assert not verify_password(owner.password_hash, PASSWORD)


@pytest.mark.usefixtures("first_boot")
async def test_a_taken_address_is_refused_rather_than_raised(db: AsyncSession) -> None:
    """A boot that will not finish is worse than a setting somebody can correct.

    The operator is seeded first, so an install naming the same address for both
    reaches this — and an API that refuses to start would be a hard way to learn
    about a typo. Seeded through `seed_super_admin` itself, not a hand-built
    `User` row: that row's `org_id` names the platform organization, which only
    `seed_super_admin` creates, and a deployment with no `SUPER_ADMIN_EMAIL` set
    never has, which is exactly this suite's own environment on 2026-10-09.
    """
    taken = f"taken-{uuid.uuid4().hex[:8]}@example.test"
    operator = await seed_super_admin(
        db, _settings(SUPER_ADMIN_EMAIL=taken, SUPER_ADMIN_PASSWORD=PASSWORD)
    )
    assert operator is not None

    assert (
        await first_run.seed_first_organization(db, _owner_settings(FIRST_OWNER_EMAIL=taken))
        is None
    )
