"""Suspending and comping from the admin console (F31, FR26, FR27, AC28) on real Postgres.

Rolled back per test. What it proves: the console and the terminal close the same
valve — a reason is required, a second close is refused, and each is audited with
who did it; a complimentary plan is the plan in force until it ends, one at a time;
and a grant cannot end in the past or name a plan that does not exist.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.admin import service as admin
from nexovue_api.admin.schemas import GrantRequest
from nexovue_api.data.postgres.models import StaffAccount
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant
from nexovue_api.usage import queries as usage_queries

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
PASSWORD: Final = "correct horse battery staple"


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_admin_actions_live.py",
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


async def _tenant(db: AsyncSession, name: str) -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name=name,
        email=f"{name.lower()}-{uuid.uuid4()}@example.test",
        password=PASSWORD,
    )


def _now() -> datetime:
    return datetime.now(UTC)


async def _staff(db: AsyncSession) -> StaffAccount:
    staff = StaffAccount(
        email=f"staff-{uuid.uuid4().hex[:8]}@nexovue.test",
        name="Raj",
        password_hash="x",
        totp_secret=b"sealed",
    )
    db.add(staff)
    await db.flush()
    return staff


async def _audit(db: AsyncSession, org_id: uuid.UUID) -> list[tuple[str, str | None]]:
    rows = await db.execute(
        text(
            "SELECT action, actor_label FROM audit_log WHERE org_id = :org "
            "AND action LIKE 'organization.ingest%' ORDER BY at, id"
        ),
        {"org": org_id},
    )
    return [(action, label) for action, label in rows.tuples()]


def _valve(tenant: ProvisionedTenant) -> tuple[bool, str | None]:
    """Whether ingest is suspended, and why — read fresh each time."""
    organization = tenant.organization
    return organization.ingest_suspended_at is not None, organization.ingest_suspended_reason


async def test_the_console_suspends_and_resumes_with_a_reason_on_the_record(
    db: AsyncSession,
) -> None:
    """AC28: the same columns the terminal writes; who and why recorded."""
    acme = await _tenant(db, "Acme")
    staff = await _staff(db)

    await admin.suspend(db, staff=staff, org_id=acme.organization.id, reason="abuse", now=_now())
    assert _valve(acme) == (True, "abuse")

    with pytest.raises(ConflictError, match="already suspended"):
        await admin.suspend(db, staff=staff, org_id=acme.organization.id, reason="x", now=_now())

    await admin.resume(db, staff=staff, org_id=acme.organization.id)
    assert _valve(acme) == (False, None)
    assert await _audit(db, acme.organization.id) == [
        ("organization.ingest_suspended", "Raj at nexovue"),
        ("organization.ingest_resumed", "Raj at nexovue"),
    ]


async def test_a_suspension_needs_a_reason(db: AsyncSession) -> None:
    acme = await _tenant(db, "Acme")
    with pytest.raises(InvalidRequestError, match="Say why"):
        await admin.suspend(
            db, staff=await _staff(db), org_id=acme.organization.id, reason="  ", now=_now()
        )


async def test_the_terminal_closes_the_same_valve_and_is_named_as_such(db: AsyncSession) -> None:
    from nexovue_api import suspend as terminal

    acme = await _tenant(db, "Acme")
    # `close_valve` commits; bind it to this rolled-back transaction by flushing first.
    db.commit = db.flush  # type: ignore[method-assign]
    await terminal.close_valve(db, acme.organization.slug, "noisy tenant")

    assert acme.organization.ingest_suspended_reason == "noisy tenant"
    assert await _audit(db, acme.organization.id) == [
        ("organization.ingest_suspended", terminal.TERMINAL_OPERATOR)
    ]


async def test_a_grant_is_the_plan_in_force_until_it_ends(db: AsyncSession) -> None:
    acme = await _tenant(db, "Acme")
    staff = await _staff(db)
    paid = acme.organization.plan

    await admin.grant_plan(
        db,
        staff=staff,
        org_id=acme.organization.id,
        request=GrantRequest(
            plan="pro", services=4, ends_on=(_now() + timedelta(days=30)).date(), reason="pilot"
        ),
        now=_now(),
    )
    in_force = await usage_queries.plan_in_force(
        db, org_id=acme.organization.id, paid_plan=paid, at=_now()
    )
    assert (in_force.slug, in_force.services_committed, in_force.granted) == ("pro", 4, True)
    assert acme.organization.plan == paid, "a grant never rewrites the paid plan"
    page = await admin.page(db, org_id=acme.organization.id)
    assert page.plan_grant is not None and page.plan_grant.plan == "pro"

    await admin.end_grant(db, staff=staff, org_id=acme.organization.id, now=_now())
    after = await usage_queries.plan_in_force(
        db, org_id=acme.organization.id, paid_plan=paid, at=_now()
    )
    assert (after.slug, after.granted) == (paid, False)


async def test_a_new_grant_replaces_the_one_in_force(db: AsyncSession) -> None:
    acme = await _tenant(db, "Acme")
    staff = await _staff(db)
    until = (_now() + timedelta(days=30)).date()
    for plan in ("pro", "scale"):
        await admin.grant_plan(
            db,
            staff=staff,
            org_id=acme.organization.id,
            request=GrantRequest(plan=plan, services=2, ends_on=until, reason="upgrade"),
            now=_now(),
        )

    active = await db.scalar(
        text("SELECT count(*) FROM plan_grants WHERE org_id = :org AND ended_at IS NULL"),
        {"org": acme.organization.id},
    )
    assert active == 1
    in_force = await usage_queries.plan_in_force(
        db, org_id=acme.organization.id, paid_plan=acme.organization.plan, at=_now()
    )
    assert in_force.slug == "scale"


@pytest.mark.parametrize(
    ("plan", "days", "refusal"),
    [("pro", -1, "must end after today"), ("platinum", 30, "No such plan")],
)
async def test_a_grant_that_cannot_apply_is_refused(
    db: AsyncSession, plan: str, days: int, refusal: str
) -> None:
    acme = await _tenant(db, "Acme")
    with pytest.raises(InvalidRequestError, match=refusal):
        await admin.grant_plan(
            db,
            staff=await _staff(db),
            org_id=acme.organization.id,
            request=GrantRequest(
                plan=plan,
                services=2,
                ends_on=(_now() + timedelta(days=days)).date(),
                reason="x",
            ),
            now=_now(),
        )
