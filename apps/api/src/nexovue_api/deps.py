"""Request-scoped dependencies.

The database session is the important one: one session per request, and the
dependency — not the endpoint — owns the commit and the rollback. An endpoint
that has to remember to commit will eventually forget, and an endpoint that
raises after a partial write must never leave that write behind.

The connection is typed `HTTPConnection`, the base of both `Request` and
`WebSocket`, rather than `Request`. FastAPI fills a `Request` parameter only for
an HTTP scope, so annotating it `Request` made every dependency below unusable
from a WebSocket route — the log tail could not reach the session or the pools.
`HTTPConnection` resolves in both scopes and carries everything used here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Final

import structlog
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import Depends
from nexovue_platform.telemetry.context import bind_tenant
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from nexovue_api.auth.sessions import Session, SessionStore, session_token_from_headers
from nexovue_api.auth.tokens import OneTimeTokenStore
from nexovue_api.data.objectstore import ObjectStore
from nexovue_api.errors.exceptions import DependencyUnavailableError, UnauthenticatedError
from nexovue_api.lifespan import Resources
from nexovue_api.notifications import EmailSender
from nexovue_api.settings import EMAIL_VARIABLES, Settings

logger = structlog.get_logger(__name__)


def get_app_settings(connection: HTTPConnection) -> Settings:
    """The configuration this app was built with, not a fresh read of the env."""
    settings: Settings = connection.app.state.settings
    return settings


def get_resources(connection: HTTPConnection) -> Resources:
    """The pools the lifespan opened."""
    resources: Resources = connection.app.state.resources
    return resources


async def get_db_session(
    resources: Annotated[Resources, Depends(get_resources)],
) -> AsyncIterator[AsyncSession]:
    """One session for the request: committed on success, rolled back on failure."""
    async with resources.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


CLICKHOUSE_UNAVAILABLE_DETAIL: Final = (
    "The store this reads is not available right now, so this screen cannot be shown. "
    "Nothing has been lost — telemetry is still being received."
)


def get_redis(resources: Annotated[Resources, Depends(get_resources)]) -> Redis:
    return resources.redis


async def get_clickhouse(resources: Annotated[Resources, Depends(get_resources)]) -> AsyncClient:
    """The analytics client, connected on the first request that needs one.

    Async because the connection is made here rather than at boot — see
    `ClickHouseConnection`. A store that cannot be reached is a dependency
    failure, not a bug in this product, and it is reported as one: the route
    answers 503 naming the dependency instead of a 500 naming nothing.
    """
    try:
        return await resources.clickhouse.client()
    except Exception as unreachable:
        raise DependencyUnavailableError(CLICKHOUSE_UNAVAILABLE_DETAIL) from unreachable


def get_object_store(
    resources: Annotated[Resources, Depends(get_resources)],
) -> ObjectStore | None:
    """The object store, or None when the deployment configured none."""
    return resources.object_store


def get_email_sender(
    resources: Annotated[Resources, Depends(get_resources)],
) -> EmailSender:
    """The mail sender, or a 503 that does not pretend we crashed.

    Raising here rather than handing back None means a route that needs mail
    cannot forget to check: the dependency either supplies a sender or the
    request never reaches the handler.

    **It answers 503, not 500, and that was wrong until 2026-09-08.**
    The error it used to raise had no handler, so it escaped as an unhandled
    internal error — `POST /auth/verify-email/resend` returned 500 on an
    unconfigured stack. A 500 tells the caller we crashed; the truth is that a
    feature is unavailable, which is what 503 says and what a client can
    sensibly retry.

    **The variable names go to the log, not to the caller.** They are what an
    operator needs and `/auth/password-reset` is unauthenticated, so listing our
    environment in a public response tells a stranger about our deployment to no
    one's benefit.
    """
    if resources.email is None:
        logger.error(
            "a route needing mail was called and no provider is configured",
            variables=list(EMAIL_VARIABLES),
        )
        raise DependencyUnavailableError(
            "Email is not available right now, so this could not be sent. Try again shortly."
        )
    return resources.email


def get_optional_email_sender(
    resources: Annotated[Resources, Depends(get_resources)],
) -> EmailSender | None:
    """The mail sender, or None, for the one caller that must not fail without it.

    Signup sends a verification link and must still create the account when mail
    is unconfigured or refused — see `signup/router._mail_verification`. Every
    other caller wants `EmailDelivery`, which refuses before the handler runs.
    """
    return resources.email


def get_one_time_tokens(
    resources: Annotated[Resources, Depends(get_resources)],
) -> OneTimeTokenStore:
    return OneTimeTokenStore(resources.redis)


def get_session_store(resources: Annotated[Resources, Depends(get_resources)]) -> SessionStore:
    return resources.sessions


async def get_current_session(
    connection: HTTPConnection,
    sessions: Annotated[SessionStore, Depends(get_session_store)],
) -> Session:
    """The caller's session, or a 401. Also puts tenancy on every later log line."""
    token = session_token_from_headers(connection.headers)
    if token is None:
        raise UnauthenticatedError("This endpoint requires a signed-in session.")

    session = await sessions.get(token)
    if session is None:
        raise UnauthenticatedError("The session has expired. Sign in again.")

    connection.scope.setdefault("state", {})["org_id"] = session.org_id
    bind_tenant(org_id=session.org_id)
    return session


AppSettings = Annotated[Settings, Depends(get_app_settings)]
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
RedisClient = Annotated[Redis, Depends(get_redis)]
ClickHouseClient = Annotated[AsyncClient, Depends(get_clickhouse)]
CurrentSession = Annotated[Session, Depends(get_current_session)]
SessionStoreDep = Annotated[SessionStore, Depends(get_session_store)]
# Optional on purpose: only source maps need it, and they say so by name.
ObjectStoreDep = Annotated[ObjectStore | None, Depends(get_object_store)]
# Not optional, unlike the object store: a route that asks for this one needs to
# send, so the refusal belongs before the handler rather than inside it.
EmailDelivery = Annotated[EmailSender, Depends(get_email_sender)]
# Optional, like the object store, and for one caller: signup must not fail
# because mail could not go. Everything else uses EmailDelivery.
OptionalEmail = Annotated[EmailSender | None, Depends(get_optional_email_sender)]
OneTimeTokens = Annotated[OneTimeTokenStore, Depends(get_one_time_tokens)]
