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
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection

from nexovue_api.auth.clients import ClientDescription, client_ip_of, describe_client
from nexovue_api.auth.encryption import CredentialBox, credential_box
from nexovue_api.auth.queries import load_identity
from nexovue_api.auth.sessions import Session
from nexovue_api.auth.totp import SecondFactorStore
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import Organization, User
from nexovue_api.deps import AppSettings, ClickHouseClient, CurrentSession, DbSession, RedisClient
from nexovue_api.errors.exceptions import DependencyUnavailableError, UnauthenticatedError

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
    if user.removed_at is not None:
        # Before the epoch check, which removal also trips: the person should be
        # told why, not that their session merely expired (F26, spec error cases).
        raise UnauthenticatedError(f"You are no longer a member of {organization.name}.")
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


def get_client_description(connection: HTTPConnection, settings: AppSettings) -> ClientDescription:
    """What this request's session would be recognised by, reduced on arrival.

    Reduced here rather than anywhere later: a raw User-Agent that never enters
    the process's data path cannot be stored by a future change that did not
    read `auth/clients.py`.
    """
    client = connection.scope.get("client")
    return describe_client(
        Headers(scope=connection.scope),
        client_ip_of(
            Headers(scope=connection.scope),
            direct_peer=str(client[0]) if client else None,
            trusted_proxy_count=settings.trusted_proxy_count,
        ),
    )


CurrentClient = Annotated[ClientDescription, Depends(get_client_description)]


def get_second_factor_store(redis: RedisClient) -> SecondFactorStore:
    """The Redis state around a second factor: offers, challenges, spent codes."""
    return SecondFactorStore(redis)


SecondFactors = Annotated[SecondFactorStore, Depends(get_second_factor_store)]


def get_credential_box(settings: AppSettings) -> CredentialBox:
    """The cipher a TOTP secret is sealed with, or a 503 before the handler runs.

    Not optional, on the same reasoning `EmailDelivery` is not: a route that
    needs to seal or open a secret cannot be allowed to forget to check, and an
    unconfigured deployment must refuse rather than store a secret in the clear.
    """
    return credential_box(settings.credential_encryption_key)


CredentialEncryption = Annotated[CredentialBox, Depends(get_credential_box)]


def get_optional_credential_box(settings: AppSettings) -> CredentialBox | None:
    """The cipher, or None, for the one caller that must still work without it.

    Sign-in takes this. If the key is missing or malformed, no stored TOTP
    secret can be opened — and a recovery code, which is an argon2 hash and
    needs no key, is then the only way into an enrolled account. Refusing the
    whole endpoint would strand every such customer instead of leaving them the
    fallback that exists for exactly this.
    """
    if not settings.credential_encryption_key:
        return None
    try:
        return credential_box(settings.credential_encryption_key)
    except DependencyUnavailableError:
        # A key that will not decode is the same as no key at all here: the
        # refusal belongs to the enrolment paths, not to somebody signing in
        # with a recovery code.
        return None


OptionalCredentialEncryption = Annotated[CredentialBox | None, Depends(get_optional_credential_box)]
