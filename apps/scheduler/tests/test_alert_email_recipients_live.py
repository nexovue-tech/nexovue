"""Who the *Organization owners* channel reaches, now that an organization can
have several people (F26, FR12, AC11).

Resolved at send time from `users`, so a second owner is included the moment they
are made one and a removed owner the moment they are removed — with no change to
the channel. This suite fails rather than skips when the stack is down.
"""

from __future__ import annotations

import uuid

from alert_fixtures import AlertTenant, postgres_reachable
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts.senders.email import verified_owners

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_alert_email_recipients_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


async def _person(
    engine: AsyncEngine,
    tenant: AlertTenant,
    *,
    role: str,
    verified: bool = True,
    removed: bool = False,
) -> str:
    email = f"{role}-{uuid.uuid4().hex[:10]}@example.test"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO users (org_id, email, role, password_hash, email_verified_at,
                                   removed_at)
                VALUES (:org_id, :email, :role, 'not-a-hash',
                        CASE WHEN :verified THEN now() END,
                        CASE WHEN :removed THEN now() END)
                """
            ),
            {
                "org_id": tenant.org_id,
                "email": email,
                "role": role,
                "verified": verified,
                "removed": removed,
            },
        )
    return email


async def _owners(engine: AsyncEngine, tenant: AlertTenant) -> list[str]:
    async with engine.connect() as connection:
        return await verified_owners(connection, tenant.org_id)


async def test_a_second_verified_owner_is_told(engine: AsyncEngine, tenant: AlertTenant) -> None:
    second = await _person(engine, tenant, role="owner")
    assert sorted(await _owners(engine, tenant)) == sorted([tenant.owner_email, second])


async def test_a_removed_owner_is_not_told(engine: AsyncEngine, tenant: AlertTenant) -> None:
    await _person(engine, tenant, role="owner", removed=True)
    assert await _owners(engine, tenant) == [tenant.owner_email]


async def test_admins_members_and_unverified_owners_are_not_told(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    await _person(engine, tenant, role="admin")
    await _person(engine, tenant, role="member")
    await _person(engine, tenant, role="owner", verified=False)
    assert await _owners(engine, tenant) == [tenant.owner_email]
