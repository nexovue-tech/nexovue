"""A person's own data (docs/features/personal-data/spec.md), against real Postgres.

Each test runs in a transaction that is rolled back. What it proves: the export
holds the person's categories and nobody else's (AC1); self-deletion ends the
account and leaves every audit entry naming the user id, never the address or
the name (AC2); the last owner is refused and nothing changes (AC3); an admin's
removal leaves the same audit result (AC4).

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from starlette.requests import Request

from nexovue_api.audit import service as audit
from nexovue_api.auth import second_factor, set_password
from nexovue_api.auth.dependencies import get_current_identity
from nexovue_api.auth.sessions import Session
from nexovue_api.auth.totp import SecondFactorStore
from nexovue_api.data.postgres.models import (
    ApiToken,
    AuditLogEntry,
    SessionRecord,
    User,
)
from nexovue_api.errors.exceptions import (
    ConflictError,
    InvalidRequestError,
    UnauthenticatedError,
)
from nexovue_api.members import service as members
from nexovue_api.members.schemas import Role
from nexovue_api.profile import personal_data
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

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
    suite="apps/api/tests/test_personal_data_live.py",
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


@pytest.fixture
async def acme(db: AsyncSession) -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name="Acme",
        email=f"acme-{uuid.uuid4()}@example.test",
        password=PASSWORD,
        name="Asha",
    )


# Never consulted: none of these accounts has a second factor on.
NO_SECOND_FACTORS: Final = cast(SecondFactorStore, object())


def _now() -> datetime:
    return datetime.now(UTC)


async def _join(db: AsyncSession, acme: ProvisionedTenant, role: Role, name: str) -> User:
    member, token = await members.invite(
        db,
        actor=acme.user,
        email=f"teammate-{uuid.uuid4()}@example.test",
        role=role,
        now=_now(),
    )
    await set_password.set_password(db, token, name=name, password=PASSWORD, now=_now())
    await db.flush()
    return member


def _session_row(user: User, *, network: str) -> SessionRecord:
    return SessionRecord(
        token_hash=uuid.uuid4().hex,
        user_id=user.id,
        org_id=user.org_id,
        last_seen_at=_now(),
        expires_at=_now() + timedelta(days=1),
        client_label="Firefox on Linux",
        client_network=network,
    )


async def _entries(db: AsyncSession, org_id: uuid.UUID) -> list[AuditLogEntry]:
    await db.flush()
    return list(await db.scalars(select(AuditLogEntry).where(AuditLogEntry.org_id == org_id)))


def _assert_scrubbed(entries: list[AuditLogEntry], *, address: str, name: str) -> None:
    """No entry in the organization carries the person's address or name anywhere."""
    for entry in entries:
        written = json.dumps(entry.detail or {}) + (entry.actor_label or "") + entry.target
        assert address not in written, entry.action
        assert name not in written, entry.action


# ---- export --------------------------------------------------------------------------


async def test_the_export_holds_the_person_and_nobody_else(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC1."""
    priya = await _join(db, acme, "admin", "Priya")
    other = await _join(db, acme, "member", "Other")
    db.add_all([_session_row(priya, network="203.0.113.0/24"), _session_row(other, network="x")])
    db.add(
        ApiToken(
            org_id=acme.organization.id,
            name="Priya's CI",
            token_prefix="nxt_aaaa",
            token_hash=uuid.uuid4().hex * 2,
            created_by=priya.id,
            expires_at=_now() + timedelta(days=30),
        )
    )
    audit.record_password_changed(db, user=priya)
    audit.record_password_changed(db, user=other)
    await db.flush()

    export = await personal_data.export(db, user=priya, organization=acme.organization, now=_now())
    body = export.model_dump(mode="json")

    assert body["profile"]["email"] == priya.email
    assert body["profile"]["name"] == "Priya"
    assert body["profile"]["role"] == "admin"
    assert body["organization"]["name"] == "Acme"
    assert [one["network"] for one in body["sessions"]] == ["203.0.113.0/24"]
    assert [one["name"] for one in body["api_tokens"]] == ["Priya's CI"]
    assert body["invitation_received"]["invited_by"] == str(acme.user.id)
    assert body["invitations_sent"] == []
    actions = {entry["action"] for entry in body["activity"]}
    assert audit.PASSWORD_CHANGED in actions
    assert body["truncated"] is False
    # Nothing of anybody else's: the other member's session, address and acts.
    assert other.email not in json.dumps(body)
    assert all(entry["action"] != audit.MEMBER_INVITED for entry in body["activity"])

    owner = await personal_data.export(
        db, user=acme.user, organization=acme.organization, now=_now()
    )
    assert {one.email for one in owner.invitations_sent} == {priya.email, other.email}


async def test_the_export_is_cut_at_its_bound_and_says_so(
    db: AsyncSession, acme: ProvisionedTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(personal_data, "MAX_ACTIVITY_ENTRIES", 2)
    for _ in range(3):
        audit.record_password_changed(db, user=acme.user)
    await db.flush()

    export = await personal_data.export(
        db, user=acme.user, organization=acme.organization, now=_now()
    )

    assert len(export.activity) == 2
    assert export.truncated is True
    assert export.oldest_activity_at == export.activity[-1].at


# ---- deleting your own account -------------------------------------------------------


async def test_a_person_deletes_their_own_account(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC2."""
    priya = await _join(db, acme, "member", "Priya")
    address = priya.email
    db.add(_session_row(priya, network="203.0.113.0/24"))
    audit.record_password_changed(db, user=priya)
    await db.flush()

    await personal_data.delete_account(
        db,
        NO_SECOND_FACTORS,
        None,
        user=priya,
        organization=acme.organization,
        current_password=PASSWORD,
        code=None,
        now=_now(),
    )
    await db.flush()

    assert priya.removed_at is not None
    assert priya.name == members.FORMER_MEMBER_NAME
    assert priya.email != address
    assert priya.password_hash is None
    remaining = await db.scalars(select(SessionRecord).where(SessionRecord.user_id == priya.id))
    assert list(remaining) == []
    # Signed out: a session issued before now is refused.
    session = Session(user_id=str(priya.id), org_id=str(priya.org_id), created_at=_now())
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    with pytest.raises(UnauthenticatedError):
        await get_current_identity(request, session, db)
    # The address can sign up anew.
    again = await provision_tenant(db, organization_name="Own", email=address, password=PASSWORD)
    assert again.user.email == address

    entries = await _entries(db, acme.organization.id)
    _assert_scrubbed(entries, address=address, name="Priya")
    left = [entry for entry in entries if entry.action == audit.MEMBER_LEFT]
    assert len(left) == 1 and left[0].actor_user_id == priya.id
    invited = next(entry for entry in entries if entry.action == audit.MEMBER_INVITED)
    assert cast(dict[str, Any], invited.detail)["email"] == str(priya.id)


async def test_a_wrong_password_deletes_nothing(db: AsyncSession, acme: ProvisionedTenant) -> None:
    priya = await _join(db, acme, "member", "Priya")
    address = priya.email

    with pytest.raises(InvalidRequestError):
        await personal_data.delete_account(
            db,
            NO_SECOND_FACTORS,
            None,
            user=priya,
            organization=acme.organization,
            current_password="not the password",
            code=None,
            now=_now(),
        )

    assert priya.removed_at is None and priya.email == address and priya.name == "Priya"


async def test_a_second_factor_needs_its_code(
    db: AsyncSession, acme: ProvisionedTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    priya = await _join(db, acme, "member", "Priya")
    priya.totp_confirmed_at = _now()

    async def refuses(*_: object, **__: object) -> bool:
        return False

    monkeypatch.setattr(second_factor, "verify", refuses)

    for code in (None, "000000"):
        with pytest.raises(InvalidRequestError):
            await personal_data.delete_account(
                db,
                NO_SECOND_FACTORS,
                None,
                user=priya,
                organization=acme.organization,
                current_password=PASSWORD,
                code=code,
                now=_now(),
            )
    assert priya.removed_at is None


async def test_the_last_owner_is_refused(db: AsyncSession, acme: ProvisionedTenant) -> None:
    """AC3."""
    address = acme.user.email

    assert await personal_data.deletion_refusal(db, user=acme.user) == (
        personal_data.SOLE_OWNER_REFUSAL
    )
    with pytest.raises(ConflictError, match="only owner"):
        await personal_data.delete_account(
            db,
            NO_SECOND_FACTORS,
            None,
            user=acme.user,
            organization=acme.organization,
            current_password=PASSWORD,
            code=None,
            now=_now(),
        )

    assert acme.user.removed_at is None and acme.user.email == address
    await _join(db, acme, "owner", "Second owner")
    assert await personal_data.deletion_refusal(db, user=acme.user) is None


# ---- an admin's removal ---------------------------------------------------------------


async def test_an_admins_removal_scrubs_the_same_way(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC4."""
    priya = await _join(db, acme, "member", "Priya")
    address = priya.email
    db.add(_session_row(priya, network="203.0.113.0/24"))
    audit.record_password_changed(db, user=priya)
    await db.flush()

    await members.remove(
        db, actor=acme.user, organization=acme.organization, member_id=priya.id, now=_now()
    )

    assert priya.name == members.FORMER_MEMBER_NAME
    remaining = await db.scalars(select(SessionRecord).where(SessionRecord.user_id == priya.id))
    assert list(remaining) == []
    entries = await _entries(db, acme.organization.id)
    _assert_scrubbed(entries, address=address, name="Priya")
    removed = next(entry for entry in entries if entry.action == audit.MEMBER_REMOVED)
    assert removed.actor_user_id == acme.user.id
    assert removed.detail == {"role": "member"}
