"""One beat process schedules; the others wait to take over.

More than one beat runs on purpose. A single scheduler is a single point of
failure, and when it is the thing that decides when checks run, losing it means
every customer's monitoring stops with nothing to say so. Two scheduling at once
would double every check, so only the holder of one lock schedules anything.

**The lock is a Postgres advisory lock, held by a connection** (ADR-020). The
holder keeps one connection open for as long as it leads; if the process dies,
its connection drops and Postgres releases the lock with it. There is no expiry
to tune and no refresh to get wrong — which is what the Redis lock's two scripts
existed for. A session-level lock needs a direct connection: a pooler in
transaction mode would hand the lock to whichever connection the pool chose.
"""

from __future__ import annotations

import contextlib
from typing import Final

import asyncpg
from nexovue_platform.telemetry.logging import get_logger

logger = get_logger(__name__)

# The advisory lock beat leadership is, among any others this database may hold.
BEAT_LEADER_LOCK: Final = 0x6E78_6265_6174  # "nxbeat"


class LeaderLock:
    """Whether this process leads, decided by holding one advisory lock."""

    def __init__(self, dsn: str, *, lock_id: int = BEAT_LEADER_LOCK) -> None:
        self._dsn = dsn
        self._lock_id = lock_id
        self._connection: asyncpg.Connection | None = None
        self.held = False

    async def keep(self) -> bool:
        """Take the lock if it is free, or confirm it is still ours."""
        try:
            if self._connection is None or self._connection.is_closed():
                self.held = False
                self._connection = await asyncpg.connect(self._dsn)
            if self.held:
                # The lock lives as long as this connection; a connection that
                # answers is a lock still held.
                await self._connection.fetchval("SELECT 1")
            else:
                self.held = bool(
                    await self._connection.fetchval(
                        "SELECT pg_try_advisory_lock($1)", self._lock_id
                    )
                )
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError):
            logger.warning("beat cannot reach Postgres to hold leadership")
            await self._drop_connection()
        return self.held

    async def release(self) -> None:
        """Hand leadership on now rather than when the connection is noticed gone."""
        if self._connection is not None and self.held:
            with contextlib.suppress(OSError, asyncpg.PostgresError, asyncpg.InterfaceError):
                await self._connection.fetchval("SELECT pg_advisory_unlock($1)", self._lock_id)
        await self._drop_connection()

    async def _drop_connection(self) -> None:
        self.held = False
        if self._connection is not None:
            try:
                await self._connection.close(timeout=2)
            except Exception:
                self._connection.terminate()
            self._connection = None
