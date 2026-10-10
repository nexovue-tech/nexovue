"""The auth tier, counted exactly in Postgres (readiness group 2b).

Login, signup and reset are where a limit stops password guessing, so this tier
is the one that must hold across every API process at once, and the one that
must refuse rather than wave a guess through when its count cannot be taken.
These run against the real table: two limiters stand in for two processes.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nexovue_api.auth.sessions import token_fingerprint
from nexovue_api.data.postgres.models import RateLimitWindow
from nexovue_api.ratelimit.limiter import RateLimiter
from nexovue_api.ratelimit.policy import AUTH_TIER

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
QUOTA: Final = AUTH_TIER.quota


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_auth_limit_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def address(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[str]:
    """An address unique to the test; its window is removed afterwards."""
    chosen = f"192.0.2.{uuid.uuid4().int % 250}-{uuid.uuid4().hex[:8]}"
    yield chosen
    async with factory.begin() as db:
        await db.execute(
            delete(RateLimitWindow).where(RateLimitWindow.key.like(f"%{token_fingerprint(chosen)}"))
        )


async def _attempts(limiter: RateLimiter, address: str, count: int) -> list[bool]:
    return [
        (await limiter.consume(scope="ip", identity=address, quota=QUOTA)).allowed
        for _ in range(count)
    ]


async def test_the_eleventh_attempt_in_a_minute_is_refused(
    factory: async_sessionmaker[AsyncSession], address: str
) -> None:
    limiter = RateLimiter(factory, instances=1)

    assert await _attempts(limiter, address, QUOTA.limit + 1) == [True] * QUOTA.limit + [False]


async def test_two_processes_share_one_count(
    factory: async_sessionmaker[AsyncSession], address: str
) -> None:
    """The thing an in-memory count cannot do: a guesser spreading attempts across
    API processes still gets ten, not ten each. `API_INSTANCES` does not divide
    this tier, because it is not each process's share."""
    first = RateLimiter(factory, instances=3)
    second = RateLimiter(factory, instances=3)

    spent = [
        (
            await (first if attempt % 2 else second).consume(
                scope="ip", identity=address, quota=QUOTA
            )
        ).allowed
        for attempt in range(QUOTA.limit + 1)
    ]

    assert spent == [True] * QUOTA.limit + [False]


async def test_the_address_is_not_stored(
    factory: async_sessionmaker[AsyncSession], address: str
) -> None:
    """Somebody being guessed at should not have their address in a counter table."""
    await _attempts(RateLimiter(factory, instances=1), address, 1)

    async with factory() as db:
        keys = list(
            await db.scalars(
                select(RateLimitWindow.key).where(
                    RateLimitWindow.key.like(f"%{token_fingerprint(address)}")
                )
            )
        )
    assert len(keys) == 1
    assert address not in keys[0]


async def test_an_attempt_is_refused_when_its_count_cannot_be_taken() -> None:
    """Fails closed, on purpose: a guesser must not be waved through because the
    database is unreachable. A 429 rather than a 500, so a client backs off."""
    engine = create_async_engine(
        "postgresql+asyncpg://nexovue:nexovue_dev@127.0.0.1:1/nexovue",
        connect_args={"timeout": 1},
    )
    try:
        limiter = RateLimiter(async_sessionmaker(engine), instances=1)
        decision = await limiter.consume(scope="ip", identity="192.0.2.1", quota=QUOTA)
    finally:
        await engine.dispose()

    assert not decision.allowed
    assert decision.retry_after_seconds >= 1
