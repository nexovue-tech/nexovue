"""One small Postgres engine per worker child, on one event loop of its own.

Shared by every task module a child runs — probe state writes, heartbeat and
file sweeps, alert delivery — so a process holds one pool however many kinds of
task it takes. Built on first use, in the child. A fresh engine per task opened
a new connection for every state write, and those arrive once per run of every
check.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from nexovue_platform.jobs import JobRequest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexovue_scheduler.jobs import queue
from nexovue_scheduler.settings import get_settings

_engine: AsyncEngine | None = None
_loop: asyncio.AbstractEventLoop | None = None


def with_engine[Result](work: Callable[[AsyncEngine], Awaitable[Result]]) -> Result:
    """Run `work` against this process's engine, on this process's loop."""
    global _engine, _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
    if _engine is None:
        _engine = create_async_engine(
            str(get_settings().postgres_dsn), pool_size=1, max_overflow=1, pool_pre_ping=True
        )
    return _loop.run_until_complete(work(_engine))


def enqueue(request: JobRequest) -> None:
    """Add a job from inside a task — the follow-up a task hands on."""

    async def add(engine: AsyncEngine) -> None:
        async with engine.begin() as connection:
            await queue.enqueue(connection, request)

    with_engine(add)
