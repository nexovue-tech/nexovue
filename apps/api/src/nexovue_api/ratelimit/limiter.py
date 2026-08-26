"""Redis-backed rate limiting and concurrency capping.

Both algorithms run as Lua scripts because both are read-then-write: with the
steps split across round trips, two API replicas racing on the same key each see
the pre-write count and both let the request through. Lua runs atomically inside
Redis, so the check and the claim cannot be interleaved.

The window is a **sliding log** — a sorted set of request timestamps — rather
than a fixed counter. A fixed window lets a caller spend a full allowance at
23:59:59 and another at 00:00:00; on a login endpoint that is exactly the burst
the limit exists to stop.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Final

from redis.asyncio import Redis

from nexovue_api.ratelimit.policy import Quota

KEY_PREFIX: Final = "ratelimit"
CONCURRENCY_PREFIX: Final = "concurrency"

MILLISECONDS_PER_SECOND: Final = 1000

# A slot held by a process that died must not be lost forever. Nothing on the
# control plane should take longer than this, so anything older is a leak.
CONCURRENCY_SLOT_TTL_SECONDS: Final = 60

# Returned when a caller is over its limit but the reset is sub-second; a client
# told to wait zero seconds retries immediately and stays blocked.
MINIMUM_RETRY_AFTER_SECONDS: Final = 1

_SLIDING_WINDOW_SCRIPT: Final = """
local key        = KEYS[1]
local now_ms     = tonumber(ARGV[1])
local window_ms  = tonumber(ARGV[2])
local limit      = tonumber(ARGV[3])
local member     = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
local used = redis.call('ZCARD', key)

if used >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local reset_ms = window_ms - (now_ms - tonumber(oldest[2]))
  return {0, 0, reset_ms}
end

redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms)
return {1, limit - used - 1, window_ms}
"""

_ACQUIRE_SLOT_SCRIPT: Final = """
local key    = KEYS[1]
local now_ms = tonumber(ARGV[1])
local ttl_ms = tonumber(ARGV[2])
local cap    = tonumber(ARGV[3])
local token  = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - ttl_ms)
if redis.call('ZCARD', key) >= cap then
  return 0
end

redis.call('ZADD', key, now_ms, token)
redis.call('PEXPIRE', key, ttl_ms)
return 1
"""


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome of one limit check."""

    allowed: bool
    quota: Quota
    remaining: int
    reset_seconds: int

    @property
    def retry_after_seconds(self) -> int:
        return max(MINIMUM_RETRY_AFTER_SECONDS, self.reset_seconds)


class RateLimiter:
    """Sliding-window limits and concurrency slots over one Redis connection."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._consume = redis.register_script(_SLIDING_WINDOW_SCRIPT)
        self._acquire = redis.register_script(_ACQUIRE_SLOT_SCRIPT)

    async def consume(self, *, scope: str, identity: str, quota: Quota) -> Decision:
        """Count one request against ``quota`` for ``identity``."""
        now_ms = _now_ms()
        window_ms = quota.window_seconds * MILLISECONDS_PER_SECOND
        allowed, remaining, reset_ms = await self._consume(
            keys=[_window_key(scope, quota.name, identity)],
            # The clock comes from the caller rather than Redis TIME so the
            # script stays deterministic and safe to replicate.
            args=[now_ms, window_ms, quota.limit, uuid.uuid4().hex],
        )
        return Decision(
            allowed=bool(allowed),
            quota=quota,
            remaining=int(remaining),
            reset_seconds=_ms_to_whole_seconds(int(reset_ms)),
        )

    async def acquire_slot(self, *, scope: str, identity: str, max_concurrent: int) -> str | None:
        """Claim one in-flight slot, returning its token, or None when full."""
        token = uuid.uuid4().hex
        granted = await self._acquire(
            keys=[_concurrency_key(scope, identity)],
            args=[
                _now_ms(),
                CONCURRENCY_SLOT_TTL_SECONDS * MILLISECONDS_PER_SECOND,
                max_concurrent,
                token,
            ],
        )
        return token if granted else None

    async def release_slot(self, *, scope: str, identity: str, token: str) -> None:
        """Give back an in-flight slot. Safe to call for a slot already expired."""
        await self._redis.zrem(_concurrency_key(scope, identity), token)


def _window_key(scope: str, quota_name: str, identity: str) -> str:
    return f"{KEY_PREFIX}:{quota_name}:{scope}:{identity}"


def _concurrency_key(scope: str, identity: str) -> str:
    return f"{CONCURRENCY_PREFIX}:{scope}:{identity}"


def _now_ms() -> int:
    return int(time.time() * MILLISECONDS_PER_SECOND)


def _ms_to_whole_seconds(milliseconds: int) -> int:
    """Round up: telling a client to wait 0 s when 400 ms remain just bounces it."""
    return -(-max(0, milliseconds) // MILLISECONDS_PER_SECOND)
