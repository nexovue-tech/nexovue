"""F4: who writes `unavailable`, and who settles a dead question's tokens (design, *Settling*;
plan T2.9). `jobs/expiry.py` only deletes, so this is chat's own sweep, on `insight`.

Every minute (a guess), it finds questions this account can still see (in flight, or holding
a reservation) whose clock has run out: a `queued` row past `queue_expires_at`, or any row
past `hard_ends_at`. Each ends `unavailable` through `assistant_finish_message`, with what it
holds charged as spent, flagged estimated (F3 BR13: overstating is the safe side). A row
already over keeps its state; only its tokens move. A second run finds nothing to move.
"""

from __future__ import annotations

from typing import Final

from nexovue_platform.assistant_records import ASSISTANT_SWEEP_TASK
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine

logger = get_logger(__name__)

SWEEP_BATCH: Final = 200

# Across every organization, as a sweep is; row-level security shows this account only the
# rows it may still settle.
_LAPSED: Final = text(
    """
    SELECT id FROM assistant_messages
    WHERE (state = 'queued' AND queue_expires_at < now())
       OR (hard_ends_at < now() AND (state IN ('queued', 'running') OR reserved_tokens > 0))
    ORDER BY hard_ends_at
    LIMIT :batch
    """
)
_SETTLE: Final = text(
    "SELECT assistant_finish_message(:id, 'unavailable', NULL, 'died', NULL, NULL, 'charge')"
)


async def sweep(engine: AsyncEngine) -> int:
    """Settle one batch; how many questions were looked at."""
    async with engine.connect() as db:
        lapsed = [str(row.id) for row in await db.execute(_LAPSED, {"batch": SWEEP_BATCH})]
    for message_id in lapsed:
        async with engine.begin() as db:
            await db.execute(_SETTLE, {"id": message_id})
    if lapsed:
        logger.info("questions past their clock were settled", count=len(lapsed))
    return len(lapsed)


@task(ASSISTANT_SWEEP_TASK)
def sweep_task() -> None:
    with_engine(sweep)
