"""What a token has spent, without a Postgres write per request (F36, AC18).

Every request is counted in this process's memory, which costs nothing. Once a
minute — and once more as the process stops — the counts are added to
`api_token_usage_daily` and each token's `last_used_at` is moved forward, so the
list a customer reads is never more than a minute stale and a nightly pull of ten
thousand requests costs a handful of writes.

Adding rather than overwriting is what makes this safe across processes: each
adds only what it counted. What a process that crashes had not yet written is
lost — at most a minute of one token's counts, which the founder accepted
(readiness group 2b).
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from datetime import date, datetime
from typing import Final

from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger(__name__)

# How often counts are written, which is also how stale the list can be.
FLUSH_INTERVAL_SECONDS: Final = 60

# A token deleted since it was counted is skipped, so its counts cannot hold
# every later flush hostage to a foreign key.
_ADD_REQUESTS: Final = text(
    """
    INSERT INTO api_token_usage_daily (token_id, day, requests)
    SELECT :token_id, :day, :requests
    WHERE EXISTS (SELECT 1 FROM api_tokens WHERE id = :token_id)
    ON CONFLICT (token_id, day)
    DO UPDATE SET requests = api_token_usage_daily.requests + EXCLUDED.requests
    """
)
# Never backwards: two processes flushing out of order must not move it back.
_LAST_USED: Final = text(
    """
    UPDATE api_tokens SET last_used_at = GREATEST(last_used_at, :used_at)
    WHERE id = :token_id
    """
)


class TokenUsage:
    """Counts held in memory until the next flush."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._requests: Counter[tuple[uuid.UUID, date]] = Counter()
        self._last_used: dict[uuid.UUID, datetime] = {}

    def count(self, token_id: uuid.UUID, now: datetime) -> None:
        """One request on one token. Never touches the database."""
        self._requests[(token_id, now.date())] += 1
        self._last_used[token_id] = now

    async def flush(self) -> None:
        """Write what has been counted since the last flush.

        Never raises: a failed write keeps the counts for the next one, because a
        usage list a minute late is better than one that lost them.
        """
        if not self._requests:
            return
        requests, self._requests = self._requests, Counter()
        last_used, self._last_used = self._last_used, {}
        try:
            async with self._session_factory.begin() as db:
                for (token_id, day), spent in requests.items():
                    await db.execute(
                        _ADD_REQUESTS, {"token_id": token_id, "day": day, "requests": spent}
                    )
                for token_id, used_at in last_used.items():
                    await db.execute(_LAST_USED, {"token_id": token_id, "used_at": used_at})
        except Exception:
            logger.exception("token usage could not be written; keeping it for the next flush")
            self._requests.update(requests)
            for token_id, used_at in last_used.items():
                self._last_used[token_id] = max(used_at, self._last_used.get(token_id, used_at))

    async def flush_every_minute(self) -> None:
        """Run until cancelled, flushing once a minute."""
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            await self.flush()
