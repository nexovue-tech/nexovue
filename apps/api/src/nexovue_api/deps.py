"""Request-scoped dependencies.

The database session is the important one: one session per request, and the
dependency — not the endpoint — owns the commit and the rollback. An endpoint
that has to remember to commit will eventually forget, and an endpoint that
raises after a partial write must never leave that write behind.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.sessions import Session, SessionStore, session_token_from_headers
from nexovue_api.errors.exceptions import UnauthenticatedError
from nexovue_api.lifespan import Resources
from nexovue_api.telemetry.context import bind_tenant


def get_resources(request: Request) -> Resources:
    """The pools the lifespan opened."""
    resources: Resources = request.app.state.resources
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


def get_redis(resources: Annotated[Resources, Depends(get_resources)]) -> Redis:
    return resources.redis


def get_clickhouse(resources: Annotated[Resources, Depends(get_resources)]) -> AsyncClient:
    return resources.clickhouse


def get_session_store(resources: Annotated[Resources, Depends(get_resources)]) -> SessionStore:
    return resources.sessions


async def get_current_session(
    request: Request,
    sessions: Annotated[SessionStore, Depends(get_session_store)],
) -> Session:
    """The caller's session, or a 401. Also puts tenancy on every later log line."""
    token = session_token_from_headers(request.headers)
    if token is None:
        raise UnauthenticatedError("This endpoint requires a signed-in session.")

    session = await sessions.get(token)
    if session is None:
        raise UnauthenticatedError("The session has expired. Sign in again.")

    request.scope.setdefault("state", {})["org_id"] = session.org_id
    bind_tenant(org_id=session.org_id)
    return session


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
RedisClient = Annotated[Redis, Depends(get_redis)]
ClickHouseClient = Annotated[AsyncClient, Depends(get_clickhouse)]
CurrentSession = Annotated[Session, Depends(get_current_session)]
