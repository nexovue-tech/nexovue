"""What a token has spent, without a Postgres write per request (F36, AC18).

Every request increments a counter in Redis, which is cheap and approximate by
design. At most once a minute per token, the request that wins a `SET NX` also
writes `last_used_at` and folds the day's Redis count into
`api_token_usage_daily`. So the list a customer reads is never more than a
minute stale, and a nightly pull of ten thousand requests costs ten thousand
`INCR`s and a handful of `UPDATE`s.

Redis is a cache here, not the record: if it is flushed, the fold takes the
larger of the two counts, so the stored total never goes backwards.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Final

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import ApiToken

TOKEN_USE_PREFIX: Final = "token-use"
TOKEN_SEEN_PREFIX: Final = "token-seen"

# One fold per token per minute, which is also how stale the list can be.
FOLD_INTERVAL_SECONDS: Final = 60
# The counter outlives its day by a day, so a fold just after midnight still
# finds yesterday's number.
COUNTER_TTL_SECONDS: Final = 48 * 60 * 60

_FOLD: Final = text(
    """
    INSERT INTO api_token_usage_daily (token_id, day, requests)
    VALUES (:token_id, :day, :requests)
    ON CONFLICT (token_id, day)
    DO UPDATE SET requests = GREATEST(api_token_usage_daily.requests, EXCLUDED.requests)
    """
)


def _use_key(token: ApiToken, day: date) -> str:
    return f"{TOKEN_USE_PREFIX}:{token.id}:{day.isoformat()}"


async def count_request(
    redis: Redis,
    db: AsyncSession,
    *,
    token: ApiToken,
    now: datetime,
) -> None:
    """Count one request, and fold the day's count if this is the minute's first.

    Never raises on the caller's behalf: a read that worked must not fail because
    a counter did not. Redis being down means the list is stale, which is a worse
    answer than the truth and a much better one than a 500.
    """
    day = now.date()
    try:
        spent = await redis.incr(_use_key(token, day))
        await redis.expire(_use_key(token, day), COUNTER_TTL_SECONDS)
        first_this_minute = await redis.set(
            f"{TOKEN_SEEN_PREFIX}:{token.id}", "1", nx=True, ex=FOLD_INTERVAL_SECONDS
        )
    except Exception:
        return
    if not first_this_minute:
        return

    # Through the row rather than a statement, so a caller holding this token
    # reads the value it just wrote rather than a stale copy of it.
    token.last_used_at = now
    await db.execute(_FOLD, {"token_id": token.id, "day": day, "requests": int(spent)})
