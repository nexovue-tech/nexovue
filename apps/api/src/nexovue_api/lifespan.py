"""The process-wide resources: created once at startup, closed once at shutdown.

Every pool size is set here explicitly. A library default is a number chosen by
someone who did not know how many replicas we run or how many connections the
database will accept, and it is usually derived from the CPU count of whatever
machine happens to be running.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Final

import clickhouse_connect
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import FastAPI
from nexovue_platform.email import EMAIL_VARIABLES, EmailSender, build_email_sender
from nexovue_platform.telemetry.logging import get_logger
from redis.asyncio import ConnectionPool, Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nexovue_api.alerts.evaluation import EvaluationReader, RedisEvaluationReader
from nexovue_api.auth.sessions import SessionStore
from nexovue_api.channels.send_test import CeleryChannelTester, ChannelTester
from nexovue_api.checks.run_now import CeleryProbeBroker, ProbeBroker
from nexovue_api.data.objectstore import ObjectStore
from nexovue_api.health.checks import dependency_checks
from nexovue_api.health.registry import CheckRegistry
from nexovue_api.ratelimit.limiter import RateLimiter
from nexovue_api.ratelimit.policy import FixedPlanLimits
from nexovue_api.settings import Settings

logger = get_logger(__name__)

# SQLAlchemy needs the driver in the URL; every service names the database with
# the same plain postgresql:// DSN, so the driver is added here instead of being
# baked into an environment variable nobody can reuse.
ASYNC_POSTGRES_DRIVER: Final = "postgresql+asyncpg"

# The two guards on every ClickHouse query, applied at the client so no caller
# can forget them. `result_overflow_mode=throw` turns the row cap into an error
# rather than a silently truncated answer.
CLICKHOUSE_RESULT_OVERFLOW_MODE: Final = "throw"


class ClickHouseConnection:
    """The analytics store, contacted on first use rather than at boot.

    **Every other dependency here is already lazy.** `create_async_engine` and
    `ConnectionPool.from_url` open nothing until something asks; only
    `get_async_client` reaches out during construction, running its own
    `SELECT version(), timezone()` before it will hand back a client.

    That made a control plane which merely *reads* ClickHouse refuse to start
    without it. On a developer's machine the cost was concrete: `uvicorn
    --reload` restarts the process on every edit, and an edit made while
    ClickHouse was restarting left the API dead — "Application startup failed.
    Exiting." — until somebody restarted the container by hand.

    Lazy instead, so the API boots and every screen that needs Postgres or Redis
    keeps working. A screen that needs ClickHouse gets a 503 naming a dependency,
    which is both true and actionable.
    """

    def __init__(self, connect: Callable[[], Awaitable[AsyncClient]]) -> None:
        self._connect = connect
        self._client: AsyncClient | None = None
        # One connection, however many requests arrive together on a cold start.
        self._opening = asyncio.Lock()

    async def client(self) -> AsyncClient:
        if self._client is not None:
            return self._client
        async with self._opening:
            # Checked again inside the lock: whoever waited may have been
            # waiting for exactly this.
            if self._client is None:
                self._client = await self._connect()
            return self._client

    async def ping(self) -> bool:
        """For the health check, which reports *not ready* rather than raising
        when the store cannot be reached at all."""
        try:
            return bool(await (await self.client()).ping())
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


@dataclass(slots=True)
class Resources:
    """Everything with a connection, owned by the application's lifetime."""

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis
    clickhouse: ClickHouseConnection
    sessions: SessionStore
    rate_limiter: RateLimiter
    # None when no object store is configured. Source map upload and
    # symbolication refuse by name in that case; nothing else needs one.
    object_store: ObjectStore | None
    # None when mail is not configured, on the same principle: every screen
    # still serves, and the two flows that must send — verification and password
    # reset — refuse by naming the variables that are missing.
    email: EmailSender | None
    # None when no probe broker is configured; Run now refuses with 503.
    probes: ProbeBroker | None = None
    # The same broker, for a channel's send test (F21); None refuses the same way.
    channel_tests: ChannelTester | None = None
    # When the scheduler last evaluated the service conditions (F21).
    evaluations: EvaluationReader | None = None


def build_lifespan(
    settings: Settings,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """A lifespan that opens the pools, publishes them on the app, and closes them."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resources = await open_resources(settings)
        app.state.resources = resources
        app.state.rate_limiter = resources.rate_limiter
        app.state.plan_limits = FixedPlanLimits()
        app.state.health = CheckRegistry(dependency_checks(resources))
        logger.info("api started", env=settings.env)
        try:
            yield
        finally:
            await close_resources(resources)
            logger.info("api stopped")

    return lifespan


async def open_resources(settings: Settings) -> Resources:
    """Open every pool the API needs."""
    engine = create_async_engine(
        _async_postgres_url(str(settings.postgres_dsn)),
        pool_size=settings.postgres_pool_size,
        max_overflow=settings.postgres_max_overflow,
        pool_timeout=settings.postgres_pool_timeout_seconds,
        pool_recycle=settings.postgres_pool_recycle_seconds,
        # A connection idle in the pool while the database restarted is dead;
        # without this the first request after a failover gets the corpse.
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    redis = Redis(
        connection_pool=ConnectionPool.from_url(
            str(settings.redis_url), max_connections=settings.postgres_pool_size
        ),
        decode_responses=True,
    )

    async def connect_clickhouse() -> AsyncClient:
        return await clickhouse_connect.get_async_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_dsn.username or "",
            password=settings.clickhouse_dsn.password or "",
            database=settings.clickhouse_database,
            secure=settings.clickhouse_secure,
            connector_limit=settings.clickhouse_pool_size,
            connector_limit_per_host=settings.clickhouse_pool_size,
            settings={
                "max_execution_time": settings.clickhouse_query_timeout_seconds,
                "max_result_rows": settings.clickhouse_max_result_rows,
                "result_overflow_mode": CLICKHOUSE_RESULT_OVERFLOW_MODE,
            },
        )

    return Resources(
        object_store=await _open_object_store(settings),
        email=_open_email(settings),
        engine=engine,
        session_factory=session_factory,
        redis=redis,
        clickhouse=ClickHouseConnection(connect_clickhouse),
        sessions=SessionStore(redis, ttl_seconds=settings.session_ttl_seconds),
        rate_limiter=RateLimiter(redis),
        probes=(
            CeleryProbeBroker(str(settings.probe_broker_url)) if settings.probe_broker_url else None
        ),
        channel_tests=(
            CeleryChannelTester(str(settings.probe_broker_url))
            if settings.probe_broker_url
            else None
        ),
        evaluations=(
            RedisEvaluationReader(str(settings.probe_broker_url))
            if settings.probe_broker_url
            else None
        ),
    )


def _open_email(settings: Settings) -> EmailSender | None:
    """The mail sender, or None when mail is not configured.

    `build_email_sender` is the composition root and the only place a
    provider is chosen; this is the only place it is called. Nothing here learns
    which provider answered.
    """
    configured = settings.email
    if configured is None:
        logger.info(
            "no email provider configured; verification and password reset are unavailable",
            variables=list(EMAIL_VARIABLES),
        )
        return None

    logger.info("email sender ready", sending_domain=configured.sending_domain)
    return build_email_sender(configured)


async def _open_object_store(settings: Settings) -> ObjectStore | None:
    """Build the object store client, or None when it is not configured.

    The bucket is created only in development. Elsewhere it is provisioned with
    the rest of the infrastructure, and an API that could create its own bucket
    would need a permission it should not hold.
    """
    configured = settings.object_store
    if configured is None:
        logger.info("no object store configured; source map upload is unavailable")
        return None

    store = ObjectStore(configured)
    if settings.is_development:
        await store.ensure_bucket()
    logger.info("object store ready", bucket=configured.bucket)
    return store


async def close_resources(resources: Resources) -> None:
    """Close every pool, in the reverse order they were opened."""
    if resources.probes is not None:
        await resources.probes.close()
    if resources.channel_tests is not None:
        await resources.channel_tests.close()
    if resources.evaluations is not None:
        await resources.evaluations.close()
    await resources.clickhouse.close()
    await resources.redis.aclose()
    await resources.engine.dispose()


def _async_postgres_url(dsn: str) -> str:
    scheme, separator, rest = dsn.partition("://")
    if not separator:
        return dsn
    return f"{ASYNC_POSTGRES_DRIVER}://{rest}" if "+" not in scheme else dsn
