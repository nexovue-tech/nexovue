"""Re-sealing after a rotation, against real Postgres (readiness blocker 8).

One tenant with a value in each of the four sealed columns, all sealed under an
"old" key. With the old key previous and a new key current, `reseal` must move
every one to the new key and each must still open to the same plaintext. The
command runs across the whole database, as it does in production; every other
row there is sealed under keys this test does not hold, so it reads them as
unreadable and touches none of them.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from nexovue_platform.checks.sealing import (
    CHECK_SECRET_PURPOSE,
    NOTIFICATION_CHANNEL_PURPOSE,
    ConfiguredKeys,
    Keyring,
    SealedBox,
    channel_secret_owner,
    check_secret_owner,
    decode_key,
    generate_key,
    key_id,
    sealed_key_id,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from nexovue_api import keys
from nexovue_api.auth.encryption import TOTP_PURPOSE
from nexovue_api.signup.service import provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

OLD_CHECK, NEW_CHECK = generate_key(), generate_key()
OLD_CREDENTIAL, NEW_CREDENTIAL = generate_key(), generate_key()
ROTATED: Final = keys.Keys(
    check=ConfiguredKeys(NEW_CHECK, previous=OLD_CHECK),
    credential=ConfiguredKeys(NEW_CREDENTIAL, previous=OLD_CREDENTIAL),
)
PLAINTEXT: Final = "JBSWY3DPEHPK3PXP"


def _reachable() -> bool:
    parts = urlsplit(POSTGRES_DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_keys_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@dataclass(frozen=True, slots=True)
class _Tenant:
    org_id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID
    channel_id: uuid.UUID


def _box(key: str, purpose: str) -> SealedBox:
    return SealedBox(Keyring(current=decode_key(key)), purpose=purpose)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    created = create_async_engine(ASYNC_DSN)
    try:
        yield created
    finally:
        await created.dispose()


@pytest.fixture
async def tenant(engine: AsyncEngine) -> AsyncIterator[_Tenant]:
    """One value per sealed column, every one under the old keys."""
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        made = await provision_tenant(
            session,
            organization_name="Keys Suite",
            email=f"keys-{uuid.uuid4().hex[:10]}@example.test",
            password="correct-horse-battery",
        )
        org, user, project = made.organization.id, made.user.id, made.project.id
    async with engine.begin() as connection:
        channel = (
            await connection.execute(
                text("SELECT id FROM notification_channels WHERE org_id = :org LIMIT 1"),
                {"org": org},
            )
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO check_secrets (org_id, project_id, name, sealed_value) "
                "VALUES (:org, :project, 'TOKEN', :sealed)"
            ),
            {
                "org": org,
                "project": project,
                "sealed": _box(OLD_CHECK, CHECK_SECRET_PURPOSE).seal(
                    PLAINTEXT, owner=check_secret_owner(str(project), "TOKEN")
                ),
            },
        )
        await connection.execute(
            text("UPDATE notification_channels SET sealed_secret = :sealed WHERE id = :id"),
            {
                "id": channel,
                "sealed": _box(OLD_CHECK, NOTIFICATION_CHANNEL_PURPOSE).seal(
                    PLAINTEXT, owner=channel_secret_owner(str(org), str(channel))
                ),
            },
        )
        totp = _box(OLD_CREDENTIAL, TOTP_PURPOSE)
        await connection.execute(
            text(
                "UPDATE users SET totp_secret = :sealed, totp_confirmed_at = now() WHERE id = :id"
            ),
            {"id": user, "sealed": totp.seal(PLAINTEXT, owner=str(user))},
        )
        await connection.execute(
            text(
                "INSERT INTO totp_enrolments (user_id, sealed_secret, expires_at) "
                "VALUES (:id, :sealed, :expires)"
            ),
            {
                "id": user,
                "sealed": totp.seal(PLAINTEXT, owner=str(user)),
                "expires": datetime.now(UTC) + timedelta(minutes=10),
            },
        )
    try:
        yield _Tenant(org_id=org, user_id=user, project_id=project, channel_id=channel)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": org})


async def _sealed(engine: AsyncEngine, tenant: _Tenant) -> dict[str, bytes]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT
                      (SELECT sealed_value FROM check_secrets
                        WHERE project_id = :project AND name = 'TOKEN') AS check_secret,
                      (SELECT sealed_secret FROM notification_channels WHERE id = :channel)
                        AS channel_secret,
                      (SELECT totp_secret FROM users WHERE id = :user) AS totp,
                      (SELECT sealed_secret FROM totp_enrolments WHERE user_id = :user)
                        AS enrolment
                    """
                ),
                {
                    "project": tenant.project_id,
                    "channel": tenant.channel_id,
                    "user": tenant.user_id,
                },
            )
        ).one()
    return {name: bytes(value) for name, value in row._mapping.items()}


async def test_reseal_moves_every_value_to_the_current_key_and_each_still_opens(
    engine: AsyncEngine, tenant: _Tenant
) -> None:
    before = await _sealed(engine, tenant)
    assert sealed_key_id(before["check_secret"]) == key_id(decode_key(OLD_CHECK))
    assert sealed_key_id(before["enrolment"]) == key_id(decode_key(OLD_CREDENTIAL))

    async with engine.connect() as connection:
        reports = await keys.survey(connection, ROTATED, reseal=True)

    after = await _sealed(engine, tenant)
    assert sealed_key_id(after["check_secret"]) == key_id(decode_key(NEW_CHECK))
    assert sealed_key_id(after["channel_secret"]) == key_id(decode_key(NEW_CHECK))
    assert sealed_key_id(after["totp"]) == key_id(decode_key(NEW_CREDENTIAL))
    assert sealed_key_id(after["enrolment"]) == key_id(decode_key(NEW_CREDENTIAL))
    rotated = {
        "check": SealedBox(
            Keyring(current=decode_key(NEW_CHECK)), purpose=CHECK_SECRET_PURPOSE
        ).open(after["check_secret"], owner=check_secret_owner(str(tenant.project_id), "TOKEN")),
        "channel": SealedBox(
            Keyring(current=decode_key(NEW_CHECK)), purpose=NOTIFICATION_CHANNEL_PURPOSE
        ).open(
            after["channel_secret"],
            owner=channel_secret_owner(str(tenant.org_id), str(tenant.channel_id)),
        ),
        "totp": _box(NEW_CREDENTIAL, TOTP_PURPOSE).open(after["totp"], owner=str(tenant.user_id)),
        "enrolment": _box(NEW_CREDENTIAL, TOTP_PURPOSE).open(
            after["enrolment"], owner=str(tenant.user_id)
        ),
    }
    assert set(rotated.values()) == {PLAINTEXT}
    assert all(report.resealed >= 1 for report in reports), [r.line() for r in reports]

    async with engine.connect() as connection:
        again = await keys.survey(connection, ROTATED, reseal=False)
    assert all(report.counts[keys.PREVIOUS] == 0 for report in again), [r.line() for r in again]


async def test_a_value_changed_after_it_was_read_is_left_alone(
    engine: AsyncEngine, tenant: _Tenant
) -> None:
    """The re-seal replaces a value only if it still holds what was read."""
    before = await _sealed(engine, tenant)
    column = next(c for c in keys.SEALED_COLUMNS if c.name == "users.totp_secret")

    async with engine.begin() as connection:
        replaced = await connection.execute(
            column.replace, {"pk": tenant.user_id, "old": b"something else", "new": b"\x02new"}
        )

    assert replaced.rowcount == 0
    assert (await _sealed(engine, tenant))["totp"] == before["totp"]


async def test_a_value_in_the_format_before_key_ids_is_reported_and_never_touched(
    engine: AsyncEngine, tenant: _Tenant
) -> None:
    version_one = bytes([1]) + os.urandom(40)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE users SET totp_secret = :sealed, totp_confirmed_at = now() WHERE id = :id"
            ),
            {"id": tenant.user_id, "sealed": version_one},
        )

    async with engine.connect() as connection:
        reports = await keys.survey(connection, ROTATED, reseal=True)

    totp = next(report for report in reports if report.name == "users.totp_secret")
    assert totp.counts[keys.UNREADABLE] >= 1
    assert (await _sealed(engine, tenant))["totp"] == version_one
