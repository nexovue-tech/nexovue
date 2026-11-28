"""The install's settled minute, set by one live suite at a time (F28's two suites).

`detection_sweep` is one install-wide row, and the release suites commit their own
value into it, which a job under test reads on its own connection. Run in parallel
(`-n 2`), one suite's value would be read by the other's job. Each test holds a session
advisory lock while it owns the row, and puts back the value it found.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# One key for every suite that writes the row.
_LOCK: Final = text("SELECT pg_advisory_lock(hashtext('nexovue-tests:detection_sweep'))")
_UNLOCK: Final = text("SELECT pg_advisory_unlock(hashtext('nexovue-tests:detection_sweep'))")
_SET: Final = text(
    """
    INSERT INTO detection_sweep (swept_at, settled_minute) VALUES (now(), :minute)
    ON CONFLICT (one_row) DO UPDATE SET settled_minute = EXCLUDED.settled_minute
    """
)


@asynccontextmanager
async def settled_minute(
    engine: AsyncEngine,
) -> AsyncIterator[Callable[[datetime], Awaitable[None]]]:
    """Yields `settle(minute)`, which commits the settled minute, while no other suite
    holding this lock can; the value found is put back afterwards."""
    async with engine.connect() as holder:
        await holder.execute(_LOCK)
        await holder.commit()
        before = (
            await holder.execute(text("SELECT settled_minute FROM detection_sweep"))
        ).one_or_none()
        await holder.commit()

        async def settle(minute: datetime) -> None:
            async with engine.begin() as connection:
                await connection.execute(_SET, {"minute": minute})

        try:
            yield settle
        finally:
            async with engine.begin() as connection:
                if before is None:
                    await connection.execute(text("DELETE FROM detection_sweep"))
                else:
                    await connection.execute(
                        text("UPDATE detection_sweep SET settled_minute = :minute"),
                        {"minute": before.settled_minute},
                    )
            await holder.execute(_UNLOCK)
            await holder.commit()
