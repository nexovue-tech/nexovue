"""Sessions in Postgres, read on every request (ADR-020).

The store holds a hash of the token, never the token itself: a dump of the
table is a list of expiry times, not a set of usable credentials. A session's
lifetime is `expires_at`, fixed when it is made; every read filters on it, so a
lapsed row is already gone to every caller, and the scheduler's expiry sweep
deletes it later.

**Revoking every session is still one write to `users.sessions_valid_from`**
(migration 0019), with nothing to enumerate. The `(user_id, expires_at)` index
is for the other question — a customer asking "what is signed in as me?" is
asking for the set itself.

**Revoking one session is proved to be the caller's own.** The delete names
both the session and the caller's user, so an id that is not theirs is answered
exactly like one that never existed.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.datastructures import Headers

from nexovue_api.auth.clients import ClientDescription
from nexovue_api.data.postgres.models import SessionRecord

SESSION_COOKIE_NAME: Final = "nexovue_session"
AUTHORIZATION_HEADER: Final = "authorization"
BEARER_PREFIX: Final = "bearer "

# 256 bits of entropy. The token is never stretched — it is random, not chosen,
# so key stretching buys nothing against a brute-force search.
TOKEN_BYTES: Final = 32

# How stale "last used" is allowed to be before a read writes it back. Every
# authenticated request passes through `get`, so recording the moment exactly
# would mean a write per request to serve a column nobody reads at that
# resolution. A minute is finer than any sentence the screen says.
LAST_SEEN_RESOLUTION_SECONDS: Final = 60


@dataclass(frozen=True, slots=True)
class Impersonation:
    """nexovue support viewing the console as this session's person (F31, FR29).

    Carried on an ordinary customer session so every screen renders as that person;
    `get_current_identity` reads it to refuse every change, re-check the owner's
    grant, and audit each request under `impersonation_id`.
    """

    impersonation_id: str
    operator_label: str
    reason: str
    grant_id: str
    until: datetime


@dataclass(frozen=True, slots=True)
class Session:
    """Who a session belongs to, and what it is recognised by."""

    user_id: str
    org_id: str
    created_at: datetime
    # Both default, because both have an honest absent state and neither is
    # worth making every caller state. A session nothing has been told about the
    # client for is an unknown device; one nothing has seen since it was made was
    # last used when it was made — which `last_used_at` says rather than leaving
    # a caller to remember it.
    last_seen_at: datetime | None = None
    client: ClientDescription = field(default_factory=ClientDescription.unknown)
    impersonation: Impersonation | None = None
    # Until when this session has recently proved who holds it (the platform
    # surface, `docs/changes/super-admin-in-the-console/`). None means never.
    # A session is a seven-day credential; the platform is not something a
    # seven-day credential should reach on its own.
    admin_until: datetime | None = None

    @property
    def last_used_at(self) -> datetime:
        """When a request last arrived on this session."""
        return self.last_seen_at or self.created_at

    def in_admin_mode(self, now: datetime) -> bool:
        """Whether the holder re-authenticated recently enough to administer."""
        return self.admin_until is not None and now < self.admin_until


@dataclass(frozen=True, slots=True)
class ActiveSession:
    """One row of the list a customer reads.

    `id` is the token's fingerprint — SHA-256 of it, which `token_fingerprint`
    already describes as a stable, non-reversible id. It cannot be turned back
    into the token, and holding one grants nothing: ending a session by id
    requires it to be one of the caller's own.
    """

    id: str
    created_at: datetime
    last_seen_at: datetime
    client: ClientDescription


class SessionStore:
    """Create, read, list and revoke sessions.

    Each call is its own short transaction from the factory, not the request's:
    the rate limiter reads sessions before any request transaction exists, and
    signup mints one after committing the tenant it names.
    """

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, ttl_seconds: int
    ) -> None:
        self._session_factory = session_factory
        self._ttl_seconds = ttl_seconds

    async def create(
        self,
        *,
        user_id: str,
        org_id: str,
        client: ClientDescription | None = None,
        impersonation: Impersonation | None = None,
    ) -> tuple[str, Session]:
        """Mint a session and return the token to put in the cookie.

        A support session (F31) lives only until its `until`, however long an
        ordinary session would.
        """
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = datetime.now(UTC)
        session = Session(
            user_id=user_id,
            org_id=org_id,
            created_at=now,
            last_seen_at=now,
            client=client or ClientDescription.unknown(),
            impersonation=impersonation,
        )
        expires_at = now + timedelta(seconds=self._ttl_seconds)
        if impersonation is not None:
            expires_at = max(now + timedelta(seconds=1), min(expires_at, impersonation.until))
        async with self._session_factory.begin() as db:
            await db.execute(
                insert(SessionRecord).values(
                    token_hash=token_fingerprint(token),
                    user_id=uuid.UUID(user_id),
                    org_id=uuid.UUID(org_id),
                    created_at=now,
                    last_seen_at=now,
                    expires_at=expires_at,
                    client_label=session.client.label,
                    client_network=session.client.network,
                    **_impersonation_columns(impersonation),
                )
            )
        return token, session

    async def get(self, token: str) -> Session | None:
        """The session behind a token, or None if it never existed or has expired.

        **A read that occasionally writes**, which is unusual enough to say why:
        this is the one place every authenticated request already passes through,
        so it is the only place "last used" can be observed without adding a
        round trip to some other path. The write is skipped unless the stored
        moment is more than `LAST_SEEN_RESOLUTION_SECONDS` old, and it never
        touches `expires_at` — a session's lifetime is fixed at creation and
        being seen must not extend it.
        """
        fingerprint = token_fingerprint(token)
        session = await self._session_by_token_hash(fingerprint)
        if session is None:
            return None
        now = datetime.now(UTC)
        if (now - session.last_used_at).total_seconds() < LAST_SEEN_RESOLUTION_SECONDS:
            return session
        await self._update_by_token_hash(fingerprint, last_seen_at=now)
        return replace(session, last_seen_at=now)

    async def peek(self, token: str) -> Session | None:
        """The session behind a token, read without recording that it was seen.

        For the rate limiter, which runs before authentication and must not turn
        every counted request into a "last used" write.
        """
        return await self._session_by_token_hash(token_fingerprint(token))

    async def list_for(self, *, org_id: str, user_id: str) -> list[ActiveSession]:
        """Every live session for one user, newest first.

        Scoped by construction: the user and organization are the ones the
        caller's own session names, so there is no argument a request could
        supply that would reach another user's sessions.
        """
        async with self._session_factory() as db:
            rows = await db.scalars(
                select(SessionRecord)
                .where(
                    SessionRecord.org_id == uuid.UUID(org_id),
                    SessionRecord.user_id == uuid.UUID(user_id),
                    SessionRecord.expires_at > datetime.now(UTC),
                )
                .order_by(SessionRecord.created_at.desc())
            )
            return [
                ActiveSession(
                    id=row.token_hash,
                    created_at=row.created_at,
                    last_seen_at=row.last_seen_at,
                    client=ClientDescription(label=row.client_label, network=row.client_network),
                )
                for row in rows
            ]

    async def revoke_one_of(self, *, org_id: str, user_id: str, session_id: str) -> bool:
        """End a single session of this user's. False when it is not theirs.

        The user is part of the predicate, which is the authorization: an id
        that is not this user's is answered identically to one that never
        existed, so a caller cannot use this to learn that somebody else's
        session is live.
        """
        async with self._session_factory.begin() as db:
            ended = await db.scalar(
                delete(SessionRecord)
                .where(
                    SessionRecord.token_hash == session_id,
                    SessionRecord.org_id == uuid.UUID(org_id),
                    SessionRecord.user_id == uuid.UUID(user_id),
                    SessionRecord.expires_at > datetime.now(UTC),
                )
                .returning(SessionRecord.token_hash)
            )
        return ended is not None

    async def set_admin_until(self, token: str, until: datetime | None) -> Session | None:
        """Stamp — or clear — this session's re-authentication (the platform gate).

        Written onto the session's own row, so it dies with the session:
        revoking the session revokes the platform access, and no second store
        can disagree about either. `expires_at` is untouched, because proving
        who you are does not lengthen a session.
        """
        return await self._update_by_token_hash(token_fingerprint(token), admin_until=until)

    async def revoke(self, token: str) -> None:
        """End a session now. Safe to call for one already gone."""
        await self._end_by_token_hash(token_fingerprint(token))

    # The token is what *establishes* the tenant, as an ingest key's prefix does,
    # so these three are found by its hash alone; `test_postgres_tenancy_structure`
    # carries them in EXEMPT with that reason.

    async def _session_by_token_hash(self, fingerprint: str) -> Session | None:
        async with self._session_factory() as db:
            row = await db.scalar(
                select(SessionRecord).where(
                    SessionRecord.token_hash == fingerprint,
                    SessionRecord.expires_at > datetime.now(UTC),
                )
            )
            return None if row is None else _session(row)

    async def _update_by_token_hash(self, fingerprint: str, **values: object) -> Session | None:
        """Change a live session's row, and return it as it now stands."""
        async with self._session_factory.begin() as db:
            row = await db.scalar(
                update(SessionRecord)
                .where(
                    SessionRecord.token_hash == fingerprint,
                    SessionRecord.expires_at > datetime.now(UTC),
                )
                .values(**values)
                .returning(SessionRecord)
            )
            return None if row is None else _session(row)

    async def _end_by_token_hash(self, fingerprint: str) -> None:
        async with self._session_factory.begin() as db:
            await db.execute(delete(SessionRecord).where(SessionRecord.token_hash == fingerprint))


def bearer_token_from_headers(headers: Headers) -> str | None:
    """The `Authorization: Bearer` value, whatever kind of credential it is.

    Separate from `session_token_from_headers` because the caller here is asking
    "what did they present?", not "which session is this?": an API token and an
    ingest key are both bearer values and neither names a session.
    """
    authorization = headers.get(AUTHORIZATION_HEADER, "")
    if authorization.lower().startswith(BEARER_PREFIX):
        return authorization[len(BEARER_PREFIX) :].strip() or None
    return None


def session_token_from_headers(
    headers: Headers, *, cookie_name: str = SESSION_COOKIE_NAME
) -> str | None:
    """The session token a request carries, from its cookie or bearer header."""
    bearer = bearer_token_from_headers(headers)
    return bearer if bearer is not None else _cookie(headers, cookie_name)


def _cookie(headers: Headers, name: str) -> str | None:
    for part in headers.get("cookie", "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value or None
    return None


def token_fingerprint(token: str) -> str:
    """A stable, non-reversible id for a token, safe to put in a key or a log."""
    return hashlib.sha256(token.encode()).hexdigest()


def _session(row: SessionRecord) -> Session:
    return Session(
        user_id=str(row.user_id),
        org_id=str(row.org_id),
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        client=ClientDescription(label=row.client_label, network=row.client_network),
        admin_until=row.admin_until,
        impersonation=_impersonation(row),
    )


def _impersonation(row: SessionRecord) -> Impersonation | None:
    # The table's CHECK holds the five columns all set or all empty.
    if (
        row.impersonation_id is None
        or row.impersonation_operator is None
        or row.impersonation_reason is None
        or row.impersonation_grant_id is None
        or row.impersonation_until is None
    ):
        return None
    return Impersonation(
        impersonation_id=str(row.impersonation_id),
        operator_label=row.impersonation_operator,
        reason=row.impersonation_reason,
        grant_id=str(row.impersonation_grant_id),
        until=row.impersonation_until,
    )


def _impersonation_columns(impersonation: Impersonation | None) -> dict[str, object]:
    if impersonation is None:
        return {}
    return {
        "impersonation_id": uuid.UUID(impersonation.impersonation_id),
        "impersonation_operator": impersonation.operator_label,
        "impersonation_reason": impersonation.reason,
        "impersonation_grant_id": uuid.UUID(impersonation.grant_id),
        "impersonation_until": impersonation.until,
    }
