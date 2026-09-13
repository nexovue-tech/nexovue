"""Sessions in Redis, expired by Redis.

The store holds a hash of the token, never the token itself: a dump of Redis is
then a list of expiry times, not a set of usable credentials. Expiry is the key's
TTL rather than a timestamp we compare — a store that forgets on its own cannot
accumulate live sessions nobody cleans up.

**There is now a per-user index, and migration 0019 argued against one.** That
argument was about *revocation*, and it still holds: ending every session is one
write to `users.sessions_valid_from`, with nothing to enumerate and nothing to
drift, and this file has not taken that job back. What 0019 did not have to
answer is *listing*, and listing has no equivalent trick — a customer asking
"what is signed in as me?" is asking for the set itself, and a set has to exist
somewhere. So the index is added for the question that needs it and not for the
one that does not.

0019's two objections are answered rather than dismissed:

*A second write on every login.* It is one `ZADD` beside a `SET` that is already
happening, on the rarest request the API serves.

*Drift as entries expire underneath it.* The score is the session's own expiry, so
`ZREMRANGEBYSCORE` removes everything that has lapsed in a single call, and a
member whose key has nevertheless gone is dropped when the list is read. The
index is therefore a cache of the truth, never the truth: a session is live
because its key is live, and the list is filtered by that.

**Revoking one session is proved to be the caller's own.** The id a client holds
is a member of exactly one user's index, so membership *is* the authorization
check — there is no path where a caller supplies an id and the store looks it up
without first asking whether that id belongs to them.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from redis.asyncio import Redis
from starlette.datastructures import Headers

from nexovue_api.auth.clients import ClientDescription

SESSION_KEY_PREFIX: Final = "session"
# Distinct from the prefix above rather than a child of it: a session key ends in
# a 64-character fingerprint, and a scan or a pattern delete written for one must
# not sweep up the other.
SESSION_INDEX_PREFIX: Final = "session-index"
SESSION_COOKIE_NAME: Final = "nexovue_session"
AUTHORIZATION_HEADER: Final = "authorization"
BEARER_PREFIX: Final = "bearer "

# 256 bits of entropy. The token is never stretched — it is random, not chosen,
# so key stretching buys nothing against a brute-force search.
TOKEN_BYTES: Final = 32

# How stale "last used" is allowed to be before a read writes it back. Every
# authenticated request passes through `get`, so recording the moment exactly
# would mean a Redis write per request to serve a column nobody reads at that
# resolution. A minute is finer than any sentence the screen says.
LAST_SEEN_RESOLUTION_SECONDS: Final = 60

_USER_ID: Final = "user_id"
_ORG_ID: Final = "org_id"
_CREATED_AT: Final = "created_at"
_LAST_SEEN_AT: Final = "last_seen_at"
_CLIENT_LABEL: Final = "client_label"
_CLIENT_NETWORK: Final = "client_network"


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

    @property
    def last_used_at(self) -> datetime:
        """When a request last arrived on this session."""
        return self.last_seen_at or self.created_at


@dataclass(frozen=True, slots=True)
class ActiveSession:
    """One row of the list a customer reads.

    `id` is the token's fingerprint — SHA-256 of it, which `token_fingerprint`
    already describes as a stable, non-reversible id. It cannot be turned back
    into the token, and holding one grants nothing: ending a session by id
    requires it to be in the caller's own index first.
    """

    id: str
    created_at: datetime
    last_seen_at: datetime
    client: ClientDescription


class SessionStore:
    """Create, read, list, extend and revoke sessions."""

    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    async def create(
        self, *, user_id: str, org_id: str, client: ClientDescription | None = None
    ) -> tuple[str, Session]:
        """Mint a session and return the token to put in the cookie."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = datetime.now(UTC)
        session = Session(
            user_id=user_id,
            org_id=org_id,
            created_at=now,
            last_seen_at=now,
            client=client or ClientDescription.unknown(),
        )
        fingerprint = token_fingerprint(token)
        await self._redis.set(_key(fingerprint), _encode(session), ex=self._ttl_seconds)
        await self._index(
            user_id, fingerprint, expires_at=now + timedelta(seconds=self._ttl_seconds)
        )
        return token, session

    async def get(self, token: str) -> Session | None:
        """The session behind a token, or None if it never existed or has expired.

        **A read that occasionally writes**, which is unusual enough to say why:
        this is the one place every authenticated request already passes through,
        so it is the only place "last used" can be observed without adding a
        round trip to some other path. The write is skipped unless the stored
        moment is more than `LAST_SEEN_RESOLUTION_SECONDS` old, and it keeps the
        key's remaining TTL rather than restarting it — a session's lifetime is
        fixed at creation and being seen must not extend it.
        """
        fingerprint = token_fingerprint(token)
        raw = await self._redis.get(_key(fingerprint))
        if raw is None:
            return None
        session = _decode(raw)
        if session is None:
            return None
        return await self._touch(fingerprint, session)

    async def list_for(self, user_id: str) -> list[ActiveSession]:
        """Every live session for one user, newest first.

        Scoped by construction: the index key is built from the user id the
        caller's own session names, so there is no argument a request could
        supply that would reach another user's set.
        """
        index_key = _index_key(user_id)
        now = datetime.now(UTC)
        await self._redis.zremrangebyscore(index_key, "-inf", now.timestamp())

        fingerprints = [_text(member) for member in await self._redis.zrange(index_key, 0, -1)]
        if not fingerprints:
            return []

        stored = await self._redis.mget([_key(fingerprint) for fingerprint in fingerprints])
        live: list[ActiveSession] = []
        lapsed: list[str] = []
        for fingerprint, raw in zip(fingerprints, stored, strict=True):
            session = _decode(raw) if raw is not None else None
            # A member whose key has gone is an index entry the key's own TTL
            # outran. Dropped here rather than trusted: the key is the truth.
            if session is None or session.user_id != user_id:
                lapsed.append(fingerprint)
                continue
            live.append(
                ActiveSession(
                    id=fingerprint,
                    created_at=session.created_at,
                    last_seen_at=session.last_used_at,
                    client=session.client,
                )
            )
        if lapsed:
            await self._redis.zrem(index_key, *lapsed)
        return sorted(live, key=lambda entry: entry.created_at, reverse=True)

    async def revoke_one_of(self, *, user_id: str, session_id: str) -> bool:
        """End a single session of this user's. False when it is not theirs.

        The membership test is the authorization: an id that is not in this
        user's index is answered identically to one that never existed, so a
        caller cannot use this endpoint to learn that somebody else's session
        is live.
        """
        if await self._redis.zscore(_index_key(user_id), session_id) is None:
            return False
        await self._redis.delete(_key(session_id))
        await self._redis.zrem(_index_key(user_id), session_id)
        return True

    async def extend(self, token: str) -> bool:
        """Slide the expiry forward for an active session.

        The index score moves with it. A key that outlived its index entry would
        keep working while vanishing from the customer's own list, which is a
        worse failure than either half alone.
        """
        fingerprint = token_fingerprint(token)
        raw = await self._redis.get(_key(fingerprint))
        session = _decode(raw) if raw is not None else None
        if session is None:
            return False
        if not await self._redis.expire(_key(fingerprint), self._ttl_seconds):
            return False
        await self._index(
            session.user_id,
            fingerprint,
            expires_at=datetime.now(UTC) + timedelta(seconds=self._ttl_seconds),
        )
        return True

    async def revoke(self, token: str) -> None:
        """End a session now. Safe to call for one already gone.

        The owner is read before the delete so the index entry goes with it.
        A stale member would be dropped on the next read anyway; removing it
        here keeps the list honest for a customer who is watching it.
        """
        fingerprint = token_fingerprint(token)
        raw = await self._redis.get(_key(fingerprint))
        session = _decode(raw) if raw is not None else None
        await self._redis.delete(_key(fingerprint))
        if session is not None:
            await self._redis.zrem(_index_key(session.user_id), fingerprint)

    async def _index(self, user_id: str, fingerprint: str, *, expires_at: datetime) -> None:
        """Record a live session against its user, scored by when it lapses."""
        index_key = _index_key(user_id)
        await self._redis.zadd(index_key, {fingerprint: expires_at.timestamp()})
        # The index must outlive its longest member and no longer, so a user who
        # never returns leaves nothing behind.
        await self._redis.expire(index_key, self._ttl_seconds)

    async def _touch(self, fingerprint: str, session: Session) -> Session:
        """Record that this session was used, at minute resolution."""
        now = datetime.now(UTC)
        if (now - session.last_used_at).total_seconds() < LAST_SEEN_RESOLUTION_SECONDS:
            return session
        seen = Session(
            user_id=session.user_id,
            org_id=session.org_id,
            created_at=session.created_at,
            last_seen_at=now,
            client=session.client,
        )
        await self._redis.set(_key(fingerprint), _encode(seen), keepttl=True)
        return seen


def session_token_from_headers(
    headers: Headers, *, cookie_name: str = SESSION_COOKIE_NAME
) -> str | None:
    """The session token a request carries, from its cookie or bearer header."""
    authorization = headers.get(AUTHORIZATION_HEADER, "")
    if authorization.lower().startswith(BEARER_PREFIX):
        return authorization[len(BEARER_PREFIX) :].strip() or None
    return _cookie(headers, cookie_name)


def _cookie(headers: Headers, name: str) -> str | None:
    for part in headers.get("cookie", "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value or None
    return None


def token_fingerprint(token: str) -> str:
    """A stable, non-reversible id for a token, safe to put in a key or a log."""
    return hashlib.sha256(token.encode()).hexdigest()


def _key(fingerprint: str) -> str:
    return f"{SESSION_KEY_PREFIX}:{fingerprint}"


def _index_key(user_id: str) -> str:
    return f"{SESSION_INDEX_PREFIX}:{user_id}"


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _encode(session: Session) -> str:
    return json.dumps(
        {
            _USER_ID: session.user_id,
            _ORG_ID: session.org_id,
            _CREATED_AT: session.created_at.isoformat(),
            _LAST_SEEN_AT: session.last_used_at.isoformat(),
            _CLIENT_LABEL: session.client.label,
            _CLIENT_NETWORK: session.client.network,
        }
    )


def _decode(raw: str | bytes) -> Session | None:
    text = _text(raw)
    try:
        payload: Any = json.loads(text)
        created_at = datetime.fromisoformat(payload[_CREATED_AT])
        return Session(
            user_id=payload[_USER_ID],
            org_id=payload[_ORG_ID],
            created_at=created_at,
            # A session minted before this field existed has only ever been
            # created; `Session.last_used_at` then reads it as its creation.
            last_seen_at=_optional_moment(payload.get(_LAST_SEEN_AT)),
            client=ClientDescription(
                label=_optional_text(payload.get(_CLIENT_LABEL)),
                network=_optional_text(payload.get(_CLIENT_NETWORK)),
            ),
        )
    except (ValueError, KeyError, TypeError):
        # A value we cannot read is a session we cannot trust.
        return None


def _optional_moment(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
