"""The expiry sweep: deletes what Redis used to forget on its own (ADR-020).

Every read of these tables already ignores a lapsed row, so this sweep is about
disk, never correctness — a missed run leaves rows that answer nothing. It
deletes in batches so a backlog is cleared in bites, never one long lock.
"""

from __future__ import annotations

from typing import Final

from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.jobs import queue
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine

logger = get_logger(__name__)

SWEEP_EXPIRED_TASK: Final = "nexovue_scheduler.jobs.sweep_expired"
BATCH: Final = 1000

# Each table, the columns that name a row, and what makes a row lapsed. Listed
# here, never discovered, so what this deletes from is readable in one place.
_EXPIRED: Final = (
    ("sessions", "token_hash", "expires_at < now()"),
    ("one_time_tokens", "token_hash", "expires_at < now()"),
    ("view_as_tickets", "token_hash", "expires_at < now()"),
    ("totp_enrolments", "user_id", "expires_at < now()"),
    ("totp_challenges", "token_hash", "expires_at < now()"),
    ("totp_spent_codes", "user_id, code_hash", "expires_at < now()"),
    ("job_results", "request_id", "expires_at < now()"),
    # The auth tier's windows are a minute long; an hour is room for any quota.
    ("rate_limit_windows", "key, window_start", "window_start < now() - interval '1 hour'"),
)


async def sweep(engine: AsyncEngine) -> dict[str, int]:
    """Delete every lapsed row; how many went from each table."""
    removed: dict[str, int] = {}
    for table, key, lapsed in _EXPIRED:
        statement = text(
            # Every part is a constant above; nothing here came from a caller.
            f"DELETE FROM {table} WHERE ({key}) IN "
            f"(SELECT {key} FROM {table} WHERE {lapsed} LIMIT :batch)"
        )
        total = 0
        while True:
            async with engine.begin() as connection:
                deleted = (await connection.execute(statement, {"batch": BATCH})).rowcount
            total += deleted
            if deleted < BATCH:
                break
        removed[table] = total
    removed["jobs"] = await queue.sweep_spent(engine)
    return removed


@task(SWEEP_EXPIRED_TASK)
def sweep_expired() -> None:
    removed = {table: count for table, count in with_engine(sweep).items() if count}
    if removed:
        logger.info("expired rows removed", **removed)
