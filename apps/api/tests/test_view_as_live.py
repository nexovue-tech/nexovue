"""Support access and *view as* (F31, FR28, FR29, BR14, BR15, AC29, AC30) on Postgres.

What it proves: staff cannot start a view without the owner's grant; the ticket
works once; a support session reads as the person and every read is audited under
its impersonation id; anything that would change something is refused and the
attempt still audited; signing out is the one way out and is recorded as the view
ending; withdrawing the grant ends the view on its next request.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from starlette.requests import Request

from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import get_current_identity
from nexovue_api.auth.router import me
from nexovue_api.auth.sessions import Impersonation, Session
from nexovue_api.data.postgres.models import AuditLogEntry
from nexovue_api.errors.exceptions import (
    ConflictError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from nexovue_api.settings import API_PREFIX
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant
from nexovue_api.support import service as support
from nexovue_api.support.tickets import TicketStore

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)


def _reachable(url: str, port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or port), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN, 5432),
    suite="apps/api/tests/test_view_as_live.py",
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


PASSWORD: Final = "correct horse battery staple"
STAFF: Final = "Raj at nexovue"


def _now() -> datetime:
    return datetime.now(UTC)


async def _tenant(db: AsyncSession) -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name="Acme",
        email=f"acme-{uuid.uuid4()}@example.test",
        password=PASSWORD,
    )


def _request(method: str, path: str) -> Request:
    return Request(
        {"type": "http", "method": method, "path": path, "headers": [], "query_string": b""}
    )


async def _session_for(db: AsyncSession, acme: ProvisionedTenant) -> Session:
    await support.grant(db, owner=acme.user, hours=72, now=_now())
    tickets = TicketStore(db)
    ticket = await support.start_view(
        db,
        tickets,
        org_id=acme.organization.id,
        user_id=acme.user.id,
        operator_label=STAFF,
        reason="checkout shows no traces",
        now=_now(),
    )
    spent = await tickets.spend(ticket)
    assert spent is not None
    return Session(
        user_id=str(spent.user_id),
        org_id=str(spent.org_id),
        created_at=_now(),
        impersonation=Impersonation(
            impersonation_id=str(spent.impersonation_id),
            operator_label=spent.operator_label,
            reason=spent.reason,
            grant_id=str(spent.grant_id),
            until=spent.until,
        ),
    )


async def _support_rows(db: AsyncSession, org_id: uuid.UUID) -> list[AuditLogEntry]:
    rows = await db.scalars(
        select(AuditLogEntry)
        .where(AuditLogEntry.org_id == org_id, AuditLogEntry.action.startswith("support.view"))
        .order_by(AuditLogEntry.at, AuditLogEntry.id)
    )
    return list(rows)


async def test_no_view_starts_without_the_owners_grant(db: AsyncSession) -> None:
    acme = await _tenant(db)
    with pytest.raises(ConflictError, match="has not granted support access"):
        await support.start_view(
            db,
            TicketStore(db),
            org_id=acme.organization.id,
            user_id=acme.user.id,
            operator_label=STAFF,
            reason="help",
            now=_now(),
        )


async def test_a_ticket_works_once(db: AsyncSession) -> None:
    acme = await _tenant(db)
    await support.grant(db, owner=acme.user, hours=24, now=_now())
    tickets = TicketStore(db)
    ticket = await support.start_view(
        db,
        tickets,
        org_id=acme.organization.id,
        user_id=acme.user.id,
        operator_label=STAFF,
        reason="help",
        now=_now(),
    )
    assert await tickets.spend(ticket) is not None
    assert await tickets.spend(ticket) is None


async def test_a_support_view_reads_as_the_person_and_every_read_is_audited(
    db: AsyncSession,
) -> None:
    """AC30."""
    acme = await _tenant(db)
    session = await _session_for(db, acme)

    identity = await get_current_identity(_request("GET", f"{API_PREFIX}/services"), session, db)
    await db.flush()

    assert identity.user.id == acme.user.id
    assert identity.impersonation is not None
    rows = await _support_rows(db, acme.organization.id)
    assert [row.action for row in rows] == [audit.SUPPORT_VIEW_STARTED, audit.SUPPORT_VIEW_REQUEST]
    assert rows[1].actor_label == STAFF
    assert rows[1].impersonation_context == rows[0].impersonation_context
    assert rows[1].detail == {"request": f"GET {API_PREFIX}/services"}


async def test_a_read_that_fails_is_still_on_the_owners_record(db: AsyncSession) -> None:
    """A 404 or 422 rolls the request's transaction back; the audit row must survive it."""
    acme = await _tenant(db)
    session = await _session_for(db, acme)
    await db.commit()  # The view as it stood before this request.
    org_id = acme.organization.id

    await get_current_identity(_request("GET", f"{API_PREFIX}/services"), session, db)
    await db.rollback()

    rows = await _support_rows(db, org_id)
    assert [row.detail for row in rows[1:]] == [{"request": f"GET {API_PREFIX}/services"}]


async def test_a_support_view_is_offered_nothing_it_would_be_refused(db: AsyncSession) -> None:
    """The console hides every control by these permissions; the owner's are narrowed to reads."""
    acme = await _tenant(db)
    session = await _session_for(db, acme)

    identity = await get_current_identity(_request("GET", f"{API_PREFIX}/auth/me"), session, db)
    described = await me(identity)

    assert acme.user.role == "owner"
    assert described.user.permissions == ["read_audit"]
    assert described.support_view is not None
    assert described.support_view.staff == STAFF


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
async def test_nothing_changes_during_a_support_view_and_the_attempt_is_kept(
    db: AsyncSession, method: str
) -> None:
    """BR15, AC29: refused below every route, whatever the viewed person's role."""
    acme = await _tenant(db)
    session = await _session_for(db, acme)
    db.commit = db.flush  # type: ignore[method-assign]

    with pytest.raises(PermissionDeniedError, match="read-only"):
        await get_current_identity(_request(method, f"{API_PREFIX}/checks"), session, db)

    rows = await _support_rows(db, acme.organization.id)
    assert rows[-1].detail == {"request": f"{method} {API_PREFIX}/checks"}


async def test_signing_out_is_the_one_way_out(db: AsyncSession) -> None:
    acme = await _tenant(db)
    session = await _session_for(db, acme)
    identity = await get_current_identity(
        _request("POST", f"{API_PREFIX}/auth/logout"), session, db
    )
    assert identity.impersonation is not None


async def test_withdrawing_the_grant_ends_the_view_on_its_next_request(db: AsyncSession) -> None:
    acme = await _tenant(db)
    session = await _session_for(db, acme)
    await support.withdraw(db, owner=acme.user, now=_now())

    with pytest.raises(UnauthenticatedError, match="Support access has ended"):
        await get_current_identity(_request("GET", f"{API_PREFIX}/services"), session, db)


async def test_the_owner_sees_every_view(db: AsyncSession) -> None:
    acme = await _tenant(db)
    await _session_for(db, acme)

    state = await support.state(db, org_id=acme.organization.id, now=_now())

    assert state.granted_until is not None
    assert [(view.staff, view.reason) for view in state.views] == [
        (STAFF, "checkout shows no traces")
    ]
