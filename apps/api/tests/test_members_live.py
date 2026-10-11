"""People in an organization (F26) through the members service, against real Postgres.

Each test runs in a transaction that is rolled back. What it proves is the
specification's Stage 1 acceptance criteria that live below the HTTP layer:
an invite link works once and only while it is live (AC1, AC2); a role change
reaches an open session on its next request (AC5); removal ends every session
and keeps the person's work (AC6, AC12); the organization always keeps an
owner (AC7); nobody grants more than they hold (AC8); an address that belongs
elsewhere is refused (AC9); and each event is audited (AC10).

This suite fails rather than skips when the stack is down; see tests/helpers/live.
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from starlette.requests import Request

from nexovue_api.audit import service as audit
from nexovue_api.auth import set_password
from nexovue_api.auth.credentials import verify_password
from nexovue_api.auth.dependencies import Identity, get_current_identity
from nexovue_api.auth.permissions import Action
from nexovue_api.auth.policy import require
from nexovue_api.auth.sessions import Session
from nexovue_api.data.postgres.models import AuditLogEntry, Check, User
from nexovue_api.errors.exceptions import (
    ConflictError,
    InvalidRequestError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from nexovue_api.members import service
from nexovue_api.members.schemas import Role
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
    suite="apps/api/tests/test_members_live.py",
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


async def _tenant(db: AsyncSession, name: str = "Acme") -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name=name,
        email=f"{name.lower()}-{uuid.uuid4()}@example.test",
        password=PASSWORD,
    )


@pytest.fixture
async def acme(db: AsyncSession) -> ProvisionedTenant:
    return await _tenant(db)


def _now() -> datetime:
    return datetime.now(UTC)


def _address() -> str:
    return f"teammate-{uuid.uuid4()}@example.test"


async def _join(db: AsyncSession, acme: ProvisionedTenant, role: Role) -> User:
    """Invite and accept: a person who can sign in, with `role`."""
    member, token = await service.invite(
        db,
        actor=acme.user,
        email=_address(),
        role=role,
        now=_now(),
    )
    await set_password.set_password(db, token, name="Teammate", password=PASSWORD, now=_now())
    await db.flush()
    return member


async def _identity_of(db: AsyncSession, user: User, *, issued: datetime) -> Identity:
    session = Session(user_id=str(user.id), org_id=str(user.org_id), created_at=issued)
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    return await get_current_identity(request, session, db)


async def _actions(db: AsyncSession, org_id: uuid.UUID) -> list[str]:
    rows = await db.scalars(
        select(AuditLogEntry.action)
        .where(AuditLogEntry.org_id == org_id)
        .order_by(AuditLogEntry.at)
    )
    return [row for row in rows if row.startswith("member.")]


# ---- invite and accept -------------------------------------------------------------


async def test_an_invited_person_sets_a_password_and_can_sign_in(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC1: the link sets a name and password, proves the address, keeps the role."""
    member, token = await service.invite(
        db, actor=acme.user, email=_address(), role="member", now=_now()
    )
    was_without_password = member.password_hash is None
    assert member.set_password_token_hash != token, "only the hash is stored"

    holder = await set_password.set_password(db, token, name="Priya", password=PASSWORD, now=_now())

    assert was_without_password
    assert holder.user.id == member.id
    assert holder.organization.id == acme.organization.id
    assert member.name == "Priya"
    assert member.role == "member"
    assert member.email_verified_at is not None
    assert member.password_hash is not None and verify_password(member.password_hash, PASSWORD)
    assert member.set_password_token_hash is None


async def test_a_link_works_once(db: AsyncSession, acme: ProvisionedTenant) -> None:
    """AC2."""
    _, token = await service.invite(
        db, actor=acme.user, email=_address(), role="viewer", now=_now()
    )
    await set_password.set_password(db, token, name="A", password=PASSWORD, now=_now())
    await db.flush()

    with pytest.raises(InvalidRequestError, match="no longer valid"):
        await set_password.set_password(db, token, name="B", password=PASSWORD, now=_now())


async def test_a_resent_invite_kills_the_old_link(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC2."""
    member, first = await service.invite(
        db, actor=acme.user, email=_address(), role="member", now=_now()
    )
    _, second = await service.resend(db, actor=acme.user, member_id=member.id, now=_now())
    await db.flush()

    with pytest.raises(InvalidRequestError, match="no longer valid"):
        await set_password.holder_of(db, first, now=_now())
    assert (await set_password.holder_of(db, second, now=_now())).user.id == member.id


async def test_a_withdrawn_invite_leaves_nothing_and_its_link_is_dead(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC2, FR6."""
    member, token = await service.invite(
        db, actor=acme.user, email=_address(), role="member", now=_now()
    )
    await service.withdraw(db, actor=acme.user, member_id=member.id)
    await db.flush()

    assert await db.get(User, member.id) is None
    with pytest.raises(InvalidRequestError, match="no longer valid"):
        await set_password.holder_of(db, token, now=_now())


async def test_an_expired_link_says_when_and_whom_to_ask(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC2: refused after seven days, with the expiry message."""
    acme.user.name = "Raj"
    sent = _now() - timedelta(days=8)
    _, token = await service.invite(db, actor=acme.user, email=_address(), role="member", now=sent)
    await db.flush()

    with pytest.raises(InvalidRequestError, match=r"expired on .* Ask Raj to send a new one"):
        await set_password.holder_of(db, token, now=_now())


async def test_an_address_in_another_organization_is_refused_and_nothing_is_made(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC9, D1."""
    globex = await _tenant(db, "Globex")

    with pytest.raises(ConflictError, match="another nexovue organization"):
        await service.invite(
            db, actor=acme.user, email=globex.user.email, role="member", now=_now()
        )
    assert (await service.list_members(db, org_id=acme.organization.id, now=_now())).members[
        -1
    ].id == acme.user.id


async def test_an_existing_member_or_invite_is_named_as_such(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    address = _address()
    await service.invite(db, actor=acme.user, email=address, role="member", now=_now())

    with pytest.raises(ConflictError, match="Already invited"):
        await service.invite(db, actor=acme.user, email=address, role="member", now=_now())
    with pytest.raises(ConflictError, match="Already a member"):
        await service.invite(db, actor=acme.user, email=acme.user.email, role="member", now=_now())


async def test_the_list_shows_people_and_pending_invites(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """FR8."""
    acme.user.name = "Raj"
    await _join(db, acme, "admin")
    await service.invite(db, actor=acme.user, email=_address(), role="viewer", now=_now())

    listed = (await service.list_members(db, org_id=acme.organization.id, now=_now())).members

    assert [(member.role, member.status) for member in listed] == [
        ("owner", "active"),
        ("admin", "active"),
        ("viewer", "invited"),
    ]
    assert listed[2].invited_by == "Raj"
    assert listed[2].invite_sent_at is None, "nothing marked it sent"


# ---- roles -------------------------------------------------------------------------


async def test_a_role_change_reaches_an_open_session_on_its_next_request(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC5: nothing caches a role past the next request."""
    member = await _join(db, acme, "member")
    issued = _now()
    identity = await _identity_of(db, member, issued=issued)
    require(Action.OPERATE_CHECKS)(identity)

    await service.change_role(
        db,
        actor=acme.user,
        organization=acme.organization,
        member_id=member.id,
        role="viewer",
        now=_now(),
    )
    await db.flush()

    again = await _identity_of(db, member, issued=issued)
    with pytest.raises(PermissionDeniedError):
        require(Action.OPERATE_CHECKS)(again)


async def test_nobody_grants_more_than_they_hold(db: AsyncSession, acme: ProvisionedTenant) -> None:
    """AC8."""
    admin = await _join(db, acme, "admin")
    member = await _join(db, acme, "member")

    with pytest.raises(PermissionDeniedError, match="Only an owner"):
        await service.invite(db, actor=admin, email=_address(), role="owner", now=_now())
    with pytest.raises(PermissionDeniedError, match="Only an owner"):
        await service.change_role(
            db,
            actor=admin,
            organization=acme.organization,
            member_id=member.id,
            role="owner",
            now=_now(),
        )
    with pytest.raises(PermissionDeniedError, match="Only an owner"):
        await service.remove(
            db, actor=admin, organization=acme.organization, member_id=acme.user.id, now=_now()
        )
    # An admin may make another admin: at most their own role, not less.
    await service.change_role(
        db,
        actor=admin,
        organization=acme.organization,
        member_id=member.id,
        role="admin",
        now=_now(),
    )


async def test_the_last_owner_cannot_be_demoted_or_removed(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC7."""
    with pytest.raises(ConflictError, match="needs at least one owner"):
        await service.change_role(
            db,
            actor=acme.user,
            organization=acme.organization,
            member_id=acme.user.id,
            role="admin",
            now=_now(),
        )
    with pytest.raises(ConflictError, match="needs at least one owner"):
        await service.remove(
            db,
            actor=acme.user,
            organization=acme.organization,
            member_id=acme.user.id,
            now=_now(),
        )


async def test_an_invited_owner_who_has_not_joined_does_not_count(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC7: the owner who remains must be able to sign in."""
    await service.invite(db, actor=acme.user, email=_address(), role="owner", now=_now())

    with pytest.raises(ConflictError, match="needs at least one owner"):
        await service.change_role(
            db,
            actor=acme.user,
            organization=acme.organization,
            member_id=acme.user.id,
            role="member",
            now=_now(),
        )


async def test_ownership_is_handed_on_by_making_an_owner_then_stepping_down(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """US26.7."""
    successor = await _join(db, acme, "admin")
    await service.change_role(
        db,
        actor=acme.user,
        organization=acme.organization,
        member_id=successor.id,
        role="owner",
        now=_now(),
    )
    await service.change_role(
        db,
        actor=acme.user,
        organization=acme.organization,
        member_id=acme.user.id,
        role="admin",
        now=_now(),
    )

    assert (successor.role, acme.user.role) == ("owner", "admin")


# ---- removal -----------------------------------------------------------------------


async def test_removal_ends_every_session_with_a_reason(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC6, FR9."""
    member = await _join(db, acme, "member")
    issued = _now() - timedelta(minutes=5)

    await service.remove(
        db, actor=acme.user, organization=acme.organization, member_id=member.id, now=_now()
    )
    await db.flush()

    with pytest.raises(UnauthenticatedError, match="no longer a member of Acme"):
        await _identity_of(db, member, issued=issued)


async def test_removal_keeps_the_work_and_frees_the_address(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC6, AC12: the check stays; the address can sign up again; history names their id.

    The name and address go too (personal-data spec, BR4); `test_personal_data_live.py`
    proves no entry keeps either."""
    member = await _join(db, acme, "member")
    member.name = "Priya"
    address = member.email
    check = Check(
        org_id=acme.organization.id,
        project_id=acme.project.id,
        name="Made by Priya",
        type="tcp",
        config={"type": "tcp", "host": "example.test", "port": 443},
        interval_seconds=60,
    )
    db.add(check)
    await db.flush()

    await service.remove(
        db, actor=acme.user, organization=acme.organization, member_id=member.id, now=_now()
    )
    await db.flush()

    assert await db.get(Check, check.id) is not None
    assert member.email != address and member.name == service.FORMER_MEMBER_NAME
    again = await provision_tenant(
        db, organization_name="Priya's Own", email=address, password=PASSWORD
    )
    assert again.user.email == address
    removed = (
        await db.scalars(
            select(AuditLogEntry).where(
                AuditLogEntry.org_id == acme.organization.id,
                AuditLogEntry.action == audit.MEMBER_REMOVED,
            )
        )
    ).one()
    assert removed.detail == {"role": "member"}


async def test_a_pending_invite_is_withdrawn_not_removed(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    member, _ = await service.invite(
        db, actor=acme.user, email=_address(), role="member", now=_now()
    )

    with pytest.raises(ConflictError, match="Withdraw the invite instead"):
        await service.remove(
            db,
            actor=acme.user,
            organization=acme.organization,
            member_id=member.id,
            now=_now(),
        )


async def test_every_membership_event_is_audited_with_what_changed(
    db: AsyncSession, acme: ProvisionedTenant
) -> None:
    """AC10."""
    member = await _join(db, acme, "member")
    service.record_joined(db, member=member)
    await service.change_role(
        db,
        actor=acme.user,
        organization=acme.organization,
        member_id=member.id,
        role="viewer",
        now=_now(),
    )
    await service.remove(
        db, actor=acme.user, organization=acme.organization, member_id=member.id, now=_now()
    )
    await db.flush()

    assert await _actions(db, acme.organization.id) == [
        "member.invited",
        "member.joined",
        "member.role_changed",
        "member.removed",
    ]
    changed = (
        await db.scalars(
            select(AuditLogEntry.detail).where(
                AuditLogEntry.org_id == acme.organization.id,
                AuditLogEntry.action == audit.MEMBER_ROLE_CHANGED,
            )
        )
    ).one()
    assert changed == {"before": "member", "after": "viewer"}
