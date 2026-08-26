"""Sessions in Redis, expired by Redis.

The store holds a hash of the token, never the token itself: a dump of Redis is
then a list of expiry times, not a set of usable credentials. Expiry is the key's
TTL rather than a timestamp we compare — a store that forgets on its own cannot
accumulate live sessions nobody cleans up.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from redis.asyncio import Redis
from starlette.datastructures import Headers

SESSION_KEY_PREFIX: Final = "session"
SESSION_COOKIE_NAME: Final = "nexovue_session"
AUTHORIZATION_HEADER: Final = "authorization"
BEARER_PREFIX: Final = "bearer "

# 256 bits of entropy. The token is never stretched — it is random, not chosen,
# so key stretching buys nothing against a brute-force search.
TOKEN_BYTES: Final = 32


@dataclass(frozen=True, slots=True)
class Session:
    """Who a session belongs to."""

    user_id: str
    org_id: str
    created_at: datetime


class SessionStore:
    """Create, read, extend and revoke sessions."""

    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    async def create(self, *, user_id: str, org_id: str) -> tuple[str, Session]:
        """Mint a session and return the token to put in the cookie."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        session = Session(user_id=user_id, org_id=org_id, created_at=datetime.now(UTC))
        await self._redis.set(_key(token), _encode(session), ex=self._ttl_seconds)
        return token, session

    async def get(self, token: str) -> Session | None:
        """The session behind a token, or None if it never existed or has expired."""
        raw = await self._redis.get(_key(token))
        return _decode(raw) if raw else None

    async def extend(self, token: str) -> bool:
        """Slide the expiry forward for an active session."""
        return bool(await self._redis.expire(_key(token), self._ttl_seconds))

    async def revoke(self, token: str) -> None:
        """End a session now. Safe to call for one already gone."""
        await self._redis.delete(_key(token))


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


def _key(token: str) -> str:
    return f"{SESSION_KEY_PREFIX}:{token_fingerprint(token)}"


def _encode(session: Session) -> str:
    return json.dumps(
        {
            "user_id": session.user_id,
            "org_id": session.org_id,
            "created_at": session.created_at.isoformat(),
        }
    )


def _decode(raw: str | bytes) -> Session | None:
    text = raw.decode() if isinstance(raw, bytes) else raw
    try:
        payload: Any = json.loads(text)
        return Session(
            user_id=payload["user_id"],
            org_id=payload["org_id"],
            created_at=datetime.fromisoformat(payload["created_at"]),
        )
    except (ValueError, KeyError, TypeError):
        # A value we cannot read is a session we cannot trust.
        return None
