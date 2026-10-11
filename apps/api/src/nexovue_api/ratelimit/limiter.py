"""Rate limiting and concurrency capping, without a shared counter store (ADR-020).

**Most limits are counted in this process.** Each API process holds a sliding
window per caller and a count of what is in flight, and enforces its share —
the limit divided by `API_INSTANCES`. On one process that is exact; across
several it is exact only when traffic spreads evenly, and the founder accepted
that for reads, whose limits protect the cluster rather than an account.

**The auth tier is counted exactly, in Postgres.** Login, signup and reset are
where a limit stops password guessing, so they get one upsert per attempt on a
fixed window shared by every process — and when Postgres cannot answer, the
attempt is refused rather than waved through.

The in-process window is a **sliding log** of request times rather than a fixed
counter: a fixed window lets a caller spend a full allowance at 23:59:59 and
another at 00:00:00. The exact tier accepts that edge, because one database
round trip per attempt is what it can afford, and its limit is ten.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Final

from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexovue_api.auth.sessions import token_fingerprint
from nexovue_api.data.postgres.models import RateLimitWindow
from nexovue_api.ratelimit.policy import Quota

logger = get_logger(__name__)

# Returned when a caller is over its limit but the reset is sub-second; a client
# told to wait zero seconds retries immediately and stays blocked.
MINIMUM_RETRY_AFTER_SECONDS: Final = 1

# How many callers' windows one process remembers. Past this the least recently
# seen is forgotten, so a flood of invented addresses costs memory once.
MAX_TRACKED_CALLERS: Final = 100_000


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
    """Every limit a request is held to: exact quotas in Postgres, the rest here."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, instances: int
    ) -> None:
        self._exact = ExactWindows(session_factory)
        self._local = LocalLimits(instances=instances)

    async def consume(self, *, scope: str, identity: str, quota: Quota) -> Decision:
        """Count one request against ``quota`` for ``identity``."""
        if quota.exact:
            return await self._exact.consume(scope=scope, identity=identity, quota=quota)
        return self._local.consume(scope=scope, identity=identity, quota=quota)

    async def acquire_slot(
        self, *, pool: str, scope: str, identity: str, max_concurrent: int
    ) -> bool:
        """Claim one in-flight slot in `pool`; False when this caller is at the cap."""
        return self._local.acquire_slot(
            pool=pool, scope=scope, identity=identity, max_concurrent=max_concurrent
        )

    async def release_slot(self, *, pool: str, scope: str, identity: str) -> None:
        """Give back an in-flight slot claimed by `acquire_slot`."""
        self._local.release_slot(pool=pool, scope=scope, identity=identity)


class LocalLimits:
    """This process's share of each limit, held in memory.

    Nothing here awaits, so on one event loop each check-and-claim is atomic.
    """

    def __init__(self, *, instances: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._instances = instances
        self._clock = clock
        self._windows: OrderedDict[tuple[str, str, str], deque[float]] = OrderedDict()
        # Keyed by tier as well as caller: an organization's tokens and its people
        # each have their own ceiling, and neither can hold the other's (group 9, D5).
        self._in_flight: dict[tuple[str, str, str], int] = {}

    def consume(self, *, scope: str, identity: str, quota: Quota) -> Decision:
        share = self._share(quota)
        key = (quota.name, scope, identity)
        now = self._clock()
        window = self._windows.pop(key, None) or deque()
        self._windows[key] = window
        while window and window[0] <= now - share.window_seconds:
            window.popleft()
        while len(self._windows) > MAX_TRACKED_CALLERS:
            self._windows.popitem(last=False)

        if len(window) >= share.limit:
            reset = window[0] + share.window_seconds - now
            return Decision(
                allowed=False, quota=share, remaining=0, reset_seconds=_whole_seconds(reset)
            )
        window.append(now)
        return Decision(
            allowed=True,
            quota=share,
            remaining=share.limit - len(window),
            reset_seconds=share.window_seconds,
        )

    def acquire_slot(self, *, pool: str, scope: str, identity: str, max_concurrent: int) -> bool:
        key = (pool, scope, identity)
        held = self._in_flight.get(key, 0)
        if held >= max(1, max_concurrent // self._instances):
            return False
        self._in_flight[key] = held + 1
        return True

    def release_slot(self, *, pool: str, scope: str, identity: str) -> None:
        key = (pool, scope, identity)
        held = self._in_flight.get(key, 0) - 1
        if held > 0:
            self._in_flight[key] = held
        else:
            self._in_flight.pop(key, None)

    def _share(self, quota: Quota) -> Quota:
        return replace(quota, limit=max(1, quota.limit // self._instances))


class ExactWindows:
    """A fixed window per caller in `rate_limit_windows`, shared by every process.

    The key holds a fingerprint of the identity, never the identity: an address
    somebody is guessing passwords for is not stored.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def consume(self, *, scope: str, identity: str, quota: Quota) -> Decision:
        now = datetime.now(UTC)
        window = timedelta(seconds=quota.window_seconds)
        window_start = datetime.fromtimestamp(
            now.timestamp() // quota.window_seconds * quota.window_seconds, UTC
        )
        reset = _whole_seconds((window_start + window - now).total_seconds())
        counted = insert(RateLimitWindow).values(
            key=f"{quota.name}:{scope}:{token_fingerprint(identity)}",
            window_start=window_start,
            hits=1,
        )
        try:
            async with self._session_factory.begin() as db:
                hits = await db.scalar(
                    counted.on_conflict_do_update(
                        index_elements=[RateLimitWindow.key, RateLimitWindow.window_start],
                        set_={"hits": RateLimitWindow.hits + 1},
                    ).returning(RateLimitWindow.hits)
                )
        except (SQLAlchemyError, OSError):
            # Refused, not allowed: this tier exists to stop password guessing,
            # and a guesser must not be waved through because the count is down.
            logger.exception("the auth limit could not be counted; refusing", policy=quota.name)
            return Decision(allowed=False, quota=quota, remaining=0, reset_seconds=reset)
        used = hits or 0
        return Decision(
            allowed=used <= quota.limit,
            quota=quota,
            remaining=max(0, quota.limit - used),
            reset_seconds=reset,
        )


def _whole_seconds(seconds: float) -> int:
    """Round up: telling a client to wait 0 s when 400 ms remain just bounces it."""
    return max(0, -int(-seconds // 1))
