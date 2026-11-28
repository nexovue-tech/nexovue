"""The expiry sweep: deletes what Redis used to forget on its own (ADR-020), what
detection keeps only for a while (`detection/retention.py`), the evidence job's hour
(`evidence/retention.py`), AI runs and monthly usage past 13 months
(`nexovue_platform.ai_records`), chat's questions past their project's derived retention
and its records past 13 months (`nexovue_platform.assistant_records`), runbooks and chunks of a
deleted project or organization (`nexovue_platform.knowledge`), what reliability
targets keep: a deleted target past restoring, and figures past 400 days
(`slos/retention.py`), and F28's deploys and releases (`releases/retention.py`). A deleted
dashboard past its 30 days goes with its versions (`purge_deleted_dashboards`).

Every read of the first group already ignores a lapsed row, so for them this
sweep is about disk, never correctness — a missed run leaves rows that answer
nothing. Detection's are different: a closed finding past its project's derived
retention still names services, so a missed run keeps telemetry text a day
longer than the plan says (security m4). It deletes in batches so a backlog is
cleared in bites, never one long lock.

**One table failing does not stop the others** (F4 AC35; F28 design 3, *Retention*): its
error is logged with the table's name, what it removed before the failure is kept and
counted, and the sweep goes on to the next. A sweep that stopped at the first failure would
keep every later table's rows, and the spent jobs, past their plan, and nobody would see
why until the next run failed the same way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from nexovue_platform import ai_records, assistant_records, knowledge
from nexovue_platform.dashboards import RESTORE_FOR
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.detection import retention
from nexovue_scheduler.evidence import retention as evidence_retention
from nexovue_scheduler.jobs import queue
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.releases import retention as release_retention
from nexovue_scheduler.slos import retention as slo_retention

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
    *retention.EXPIRED,
    *evidence_retention.EXPIRED,
    *ai_records.EXPIRED,
    *assistant_records.EXPIRED,
    *knowledge.EXPIRED,
    *slo_retention.EXPIRED,
    *release_retention.EXPIRED,
)


async def purge_deleted_dashboards(engine: AsyncEngine, *, now: datetime) -> int:
    """Remove each dashboard deleted more than 30 days before `now`, its versions with it
    (the foreign key cascades), in batches (FR51). One deleted exactly 30 days ago can
    still be restored, so it stays. Audit entries are not touched: they hold no
    definition, and a purge's rewriting is its own path (FR68)."""
    statement = text(
        "DELETE FROM dashboards WHERE (org_id, id) IN "
        "(SELECT org_id, id FROM dashboards WHERE deleted_at < :cutoff LIMIT :batch)"
    )
    total = 0
    while True:
        async with engine.begin() as connection:
            deleted = (
                await connection.execute(statement, {"cutoff": now - RESTORE_FOR, "batch": BATCH})
            ).rowcount
        total += deleted
        if deleted < BATCH:
            return total


async def sweep(engine: AsyncEngine, *, now: datetime | None = None) -> dict[str, int]:
    """Delete every lapsed row; how many went from each table. A deleted target past
    restoring is purged first, so its months are kept before its days can lapse. `now` is
    the clock the two 30-day purges count from."""
    at = now if now is not None else datetime.now(UTC)
    removed: dict[str, int] = {
        "slo_targets": await slo_retention.purge_deleted(engine, now=at),
        "dashboards": await purge_deleted_dashboards(engine, now=at),
    }
    for table, key, lapsed in _EXPIRED:
        removed[table] = await _expire(engine, table, key, lapsed)
    removed["jobs"] = await queue.sweep_spent(engine)
    return removed


async def _expire(engine: AsyncEngine, table: str, key: str, lapsed: str) -> int:
    """One table's lapsed rows, batch by batch. Each batch commits on its own, so a failure
    keeps what went before it, and that count is what this returns."""
    statement = text(
        # Every part is a constant above; nothing here came from a caller.
        f"DELETE FROM {table} WHERE ({key}) IN "
        f"(SELECT {key} FROM {table} WHERE {lapsed} LIMIT :batch)"
    )
    total = 0
    try:
        while True:
            async with engine.begin() as connection:
                deleted = (await connection.execute(statement, {"batch": BATCH})).rowcount
            total += deleted
            if deleted < BATCH:
                return total
    except SQLAlchemyError as error:
        reason = repr(getattr(error, "orig", error))
        logger.error(
            "an expiry table failed; the rest still run", table=table, removed=total, error=reason
        )
        return total


@task(SWEEP_EXPIRED_TASK)
def sweep_expired() -> None:
    removed = {table: count for table, count in with_engine(sweep).items() if count}
    if removed:
        logger.info("expired rows removed", **removed)
