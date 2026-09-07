"""Turning a session into a tenant.

This module is where "who is calling" becomes "which tenant's data may be
read". The `TenantScopedRepository` is built here, from the session's
organization, and never from anything a caller supplied — a router that could
construct its own would be a router that could read another tenant.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends

from nexovue_api.auth.queries import load_identity
from nexovue_api.auth.sessions import Session
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import Organization, User
from nexovue_api.deps import ClickHouseClient, CurrentSession, DbSession
from nexovue_api.errors.exceptions import UnauthenticatedError

SESSION_NO_LONGER_VALID: Final = "The session is no longer valid. Sign in again."


@dataclass(frozen=True, slots=True)
class Identity:
    """The signed-in user and the organization every query is scoped to."""

    user: User
    organization: Organization

    @property
    def org_id(self) -> uuid.UUID:
        return self.organization.id


async def get_current_identity(session: CurrentSession, db: DbSession) -> Identity:
    """Resolve the session into a live user and organization, or a 401."""
    try:
        user_id = uuid.UUID(session.user_id)
        org_id = uuid.UUID(session.org_id)
    except ValueError as error:
        # A token whose payload is not a pair of ids cannot name a tenant.
        raise UnauthenticatedError(SESSION_NO_LONGER_VALID) from error

    found = await load_identity(db, user_id=user_id, org_id=org_id)
    if found is None:
        raise UnauthenticatedError(SESSION_NO_LONGER_VALID)

    user, organization = found
    if _issued_before_reset(session, user):
        # P1-71: a password reset ends every session it did not issue. There is
        # nothing to enumerate — Redis keys sessions by token fingerprint — so
        # the epoch on the user row is the revocation, and this is where it bites.
        raise UnauthenticatedError(SESSION_NO_LONGER_VALID)
    return Identity(user=user, organization=organization)


def _issued_before_reset(session: Session, user: User) -> bool:
    """Whether this session predates the last moment that invalidated them all.

    NULL means no reset has happened, which is every user until one does.
    """
    if user.sessions_valid_from is None:
        return False
    return session.created_at < user.sessions_valid_from


CurrentIdentity = Annotated[Identity, Depends(get_current_identity)]


def get_tenant_repository(
    identity: CurrentIdentity, clickhouse: ClickHouseClient
) -> TenantScopedRepository:
    """A telemetry reader bound to the caller's organization, and only to it."""
    return TenantScopedRepository(clickhouse, tenant_id=str(identity.org_id))


TenantRepository = Annotated[TenantScopedRepository, Depends(get_tenant_repository)]
