"""Staff sessions: their own Redis prefix, their own cookie, their own lifetime.

Nothing here is shared with customer sessions (ADR-016). A customer token is
never looked up under this prefix, and this cookie is never read by the
customer app.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from redis.asyncio import Redis

from nexovue_api.auth.sessions import token_fingerprint

STAFF_COOKIE_NAME: Final = "nexovue_staff"
KEY_PREFIX: Final = "staff-session"
# A working day. Staff sign in again rather than carry a week-long session to a
# console that can suspend any customer.
STAFF_SESSION_TTL_SECONDS: Final = 8 * 60 * 60
TOKEN_BYTES: Final = 32


@dataclass(frozen=True, slots=True)
class StaffSession:
    staff_id: uuid.UUID
    created_at: datetime


class StaffSessionStore:
    def __init__(self, redis: Redis, *, ttl_seconds: int = STAFF_SESSION_TTL_SECONDS) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    async def create(self, *, staff_id: uuid.UUID) -> str:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        record = {"staff_id": str(staff_id), "created_at": datetime.now(UTC).isoformat()}
        await self._redis.set(_key(token), json.dumps(record), ex=self._ttl_seconds)
        return token

    async def get(self, token: str) -> StaffSession | None:
        raw = await self._redis.get(_key(token))
        if raw is None:
            return None
        try:
            record = json.loads(raw)
            return StaffSession(
                staff_id=uuid.UUID(record["staff_id"]),
                created_at=datetime.fromisoformat(record["created_at"]),
            )
        except (ValueError, KeyError, TypeError):
            return None

    async def revoke(self, token: str) -> None:
        await self._redis.delete(_key(token))


def _key(token: str) -> str:
    # The fingerprint, never the token: a Redis key is not a place for a credential.
    return f"{KEY_PREFIX}:{token_fingerprint(token)}"
