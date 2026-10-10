"""Turning a session into a tenant.

This module is where "who is calling" becomes "which tenant's data may be
read". The `TenantScopedRepository` is built here, from the session's
organization, and never from anything a caller supplied — a router that could
construct its own would be a router that could read another tenant.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Final

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection

from nexovue_api.apitokens import service as api_tokens
from nexovue_api.apitokens.tokens import (
    INGEST_KEY_SCHEMES,
    INGEST_KEYS_CANNOT_READ,
    TOKENS_ARE_READ_ONLY,
    looks_like_api_token,
)
from nexovue_api.apitokens.usage import TokenUsage
from nexovue_api.audit import service as audit
from nexovue_api.auth.clients import ClientDescription, client_ip_of, describe_client
from nexovue_api.auth.encryption import CredentialBox, credential_box
from nexovue_api.auth.queries import load_identity
from nexovue_api.auth.sessions import Impersonation, Session, bearer_token_from_headers
from nexovue_api.auth.totp import SecondFactorStore
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import ApiToken, Organization, User
from nexovue_api.deps import (
    AppSettings,
    ClickHouseClient,
    CurrentSession,
    DbSession,
    SessionStoreDep,
    get_current_session,
    get_resources,
)
from nexovue_api.errors.exceptions import (
    DependencyUnavailableError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from nexovue_api.lifespan import Resources
from nexovue_api.settings import API_PREFIX
from nexovue_api.support import queries as support_queries

SESSION_NO_LONGER_VALID: Final = "The session is no longer valid. Sign in again."


@dataclass(frozen=True, slots=True)
class Identity:
    """The signed-in user and the organization every query is scoped to."""

    user: User
    organization: Organization
    # Set while nexovue support views the console as this person (F31, FR29).
    impersonation: Impersonation | None = None

    @property
    def org_id(self) -> uuid.UUID:
        return self.organization.id


# What a support view may do besides read: sign out, which is how it ends.
_SUPPORT_VIEW_MAY: Final = frozenset({f"{API_PREFIX}/auth/logout"})
_READS: Final = frozenset({"GET", "HEAD", "OPTIONS"})
SUPPORT_VIEW_READ_ONLY: Final = "Support view is read-only."
SUPPORT_ACCESS_ENDED: Final = "Support access has ended. Ask the owner to grant it again."


async def get_current_identity(
    connection: HTTPConnection, session: CurrentSession, db: DbSession
) -> Identity:
    """Resolve the session into a live user and organization, or a 401."""
    _refuse_a_token_here(connection)
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
        # P1-71: a password reset ends every session it did not issue. The epoch
        # on the user row is the revocation — one write, with no list of sessions
        # to walk and none made mid-walk to miss — and this is where it bites.
        raise UnauthenticatedError(SESSION_NO_LONGER_VALID)
    if session.impersonation is not None:
        await _hold_support_view(connection, session.impersonation, user, organization, db)
    return Identity(user=user, organization=organization, impersonation=session.impersonation)


async def _hold_support_view(
    connection: HTTPConnection,
    impersonation: Impersonation,
    user: User,
    organization: Organization,
    db: AsyncSession,
) -> None:
    """Every request of a support view (F31, BR14, BR15, FR29): the owner's grant
    must still stand, nothing may change, and the request is audited.

    Enforced here, below every route, so no route can forget to: a route that
    changes something is refused whatever role the viewed person holds.
    """
    now = datetime.now(UTC)
    if impersonation.until <= now or not await support_queries.grant_is_live(
        db,
        org_id=organization.id,
        grant_id=uuid.UUID(impersonation.grant_id),
        at=now,
    ):
        raise UnauthenticatedError(SUPPORT_ACCESS_ENDED)
    method = connection.scope.get("method", "GET")
    path = connection.url.path
    audit.record_support_view_request(
        db,
        org_id=organization.id,
        viewed_id=user.id,
        operator_label=impersonation.operator_label,
        impersonation_id=uuid.UUID(impersonation.impersonation_id),
        method=method,
        path=path,
    )
    # Committed now, not with the request: a read that fails later — a 404, a 422 —
    # rolls its transaction back, and the owner's record must still show the attempt.
    await db.commit()
    if method not in _READS and path not in _SUPPORT_VIEW_MAY:
        raise PermissionDeniedError(SUPPORT_VIEW_READ_ONLY)


def _issued_before_reset(session: Session, user: User) -> bool:
    """Whether this session predates the last moment that invalidated them all.

    NULL means no reset has happened, which is every user until one does.
    """
    if user.sessions_valid_from is None:
        return False
    return session.created_at < user.sessions_valid_from


CurrentIdentity = Annotated[Identity, Depends(get_current_identity)]


@dataclass(frozen=True, slots=True)
class Reader:
    """Who may read one organization's data: a person, or one of its tokens.

    The read surface (FR17) depends on this instead of `Identity`, which is what
    makes the surface a *list*: a route reachable by a token is a route that asked
    for a `Reader`, and every other route still asks for a person and refuses a
    token outright. `project_scope` is the token's, and is None for a person and
    for a token that reads every project (BR11).
    """

    org_id: uuid.UUID
    project_scope: uuid.UUID | None = None
    token_id: uuid.UUID | None = None
    # The person, when it is one. None for a token — there is no user behind it.
    identity: Identity | None = None

    @property
    def is_token(self) -> bool:
        return self.token_id is not None

    def may_read_project(self, project_id: uuid.UUID) -> bool:
        """Whether this caller's scope covers a project. A person's covers all."""
        return self.project_scope is None or self.project_scope == project_id


def _presented_bearer(connection: HTTPConnection) -> str | None:
    return bearer_token_from_headers(connection.headers)


def _refuse_a_token_here(connection: HTTPConnection) -> None:
    """A token on a route that asks for a person: 403, saying where tokens work.

    Below every such route rather than inside each one, so a route added next
    month cannot forget that a token is not a session.
    """
    presented = _presented_bearer(connection)
    if presented is not None and looks_like_api_token(presented):
        raise PermissionDeniedError(TOKENS_ARE_READ_ONLY)


def _usage_for(connection: HTTPConnection) -> TokenUsage | None:
    """The process's usage counter, when it has one.

    Read from the app rather than taken as a dependency, deliberately: counting
    a token's use must not be a *precondition* of reading. A test app, or a
    process without the pools open, reads perfectly well and counts nothing.
    """
    resources = getattr(getattr(connection.scope.get("app"), "state", None), "resources", None)
    usage: TokenUsage | None = getattr(resources, "token_usage", None)
    return usage


async def get_current_reader(
    connection: HTTPConnection, db: DbSession, sessions: SessionStoreDep
) -> Reader:
    """The caller of a read-surface route: a signed-in person, or a live token.

    An ingest key is refused in its own sentence (AC21): it is a credential the
    customer holds, and "the session has expired" would send them looking in the
    wrong place.
    """
    presented = _presented_bearer(connection)
    if presented is not None and presented.startswith(INGEST_KEY_SCHEMES):
        raise UnauthenticatedError(INGEST_KEYS_CANNOT_READ)
    if presented is not None and looks_like_api_token(presented):
        now = datetime.now(UTC)
        token = await api_tokens.token_for(db, presented, now=now)
        usage = _usage_for(connection)
        if usage is not None:
            usage.count(token.id, now)
        return _reader_for_token(token)
    session = await get_current_session(connection, sessions)
    identity = await get_current_identity(connection, session, db)
    return Reader(org_id=identity.org_id, identity=identity)


def _reader_for_token(token: ApiToken) -> Reader:
    return Reader(org_id=token.org_id, project_scope=token.project_id, token_id=token.id)


CurrentReader = Annotated[Reader, Depends(get_current_reader)]


def get_tenant_repository(
    reader: CurrentReader, clickhouse: ClickHouseClient
) -> TenantScopedRepository:
    """A telemetry reader bound to the caller's organization, and only to it.

    Built from a `Reader`, so one construction site serves a person and a token
    alike — `test_tenant_chokepoint_structure.py` holds it to exactly one site,
    which is why the assistant's tools call *this* function with a reader they
    already have rather than building their own: a second site would be a second
    answer to "which tenant is this?".
    """
    return TenantScopedRepository(clickhouse, tenant_id=str(reader.org_id))


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


def get_second_factor_store(
    resources: Annotated[Resources, Depends(get_resources)],
) -> SecondFactorStore:
    """The short-lived state around a second factor: offers, challenges, spent codes."""
    return SecondFactorStore(resources.session_factory)


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
