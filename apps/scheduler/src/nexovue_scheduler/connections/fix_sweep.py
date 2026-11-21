"""`fixes.sweep_fixes`: rung 3's sweep, on `scheduler-connections` (F5 design, *The sweep*;
plan T3.13).

Beat adds it to `connections` every minute while connections are on. Its own task and
queue, never `jobs/expiry.py`'s loop, so a failing pass here can stop neither retention nor
an alert (AC33). It lives apart from rung 2b's sweep (`fixes/sweep.py`, on `light`), so a
detection or alert worker never loads it (AC6b). Each pass is its own transaction, across
every organization, each row written by its own `org_id`:

1. **Stuck:** a source row still `gathering` past the pack job's life and limit is
   `failed`, `timed_out`; a `gathered` list nobody sent within 15 minutes is `failed`,
   `lapsed` (FR13a, AC46). Candidates and the pack go with the move (AC14).
2. **Ended short, once:** a refused or failed source row's stored code is deleted through
   the one statement (BR9's first row), and `result_recorded_at` is set in the same
   transaction, so a second pass writes nothing twice.
3. **AI off, or 30 days:** stored code is deleted when the organization switched AI off
   or removed its connection, and when a written suggestion is 30 days old (BR9).
4. **Short-lived rows** past `expires_at`: packs, check runs, GitHub link attempts.
5. **The Fix App's hourly refresh** of its repositories and grant, as a job each.

Step 4 adds `proposing` source rows and recording a proposal; Step 7 the pull request rows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from nexovue_platform.connections import REPOSITORY_REFRESH
from nexovue_platform.fixes.records import (
    GATHERED_LAPSES_AFTER,
    GATHERING_STUCK_AFTER,
    INTERACTIVE_QUEUE,
    REFRESH_FIX_APP_TASK,
    SWEEP_FIXES_TASK,
)
from nexovue_platform.fixes.statements import CLEAR_STORED_CODE
from nexovue_platform.jobs import ENQUEUE_UNLESS_WAITING, JobRequest
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine

logger = get_logger(__name__)

BATCH: Final = 200
# BR9: written code lasts at most this long (a guess).
CODE_KEPT_FOR: Final = timedelta(days=30)
REFRESH_EXPIRES_SECONDS: Final = 60

# Across every organization: rows nothing will write any more. Each carries its `org_id`.
_ENDED_WAITING: Final = text(
    """
    UPDATE finding_fixes AS fix
    SET state = 'failed', reason = :reason, state_changed_at = :at, finished_at = :at,
        path_candidates = NULL, requested_paths = NULL
    WHERE fix.id IN (
        SELECT waiting.id FROM finding_fixes AS waiting
        WHERE waiting.kind = 'source' AND waiting.state = :state
          AND waiting.state_changed_at < :before
          AND (CAST(:only_org AS uuid) IS NULL OR waiting.org_id = CAST(:only_org AS uuid))
        ORDER BY waiting.state_changed_at
        LIMIT :batch
        FOR UPDATE SKIP LOCKED
    )
    RETURNING fix.org_id, fix.id
    """
)
_DROP_PACK: Final = text(
    "DELETE FROM finding_fix_source_packs WHERE org_id = :org_id AND fix_id = :fix_id"
)
# Across every organization: source rows that ended short, whose code is not yet cleared.
_ENDED_SHORT: Final = text(
    """
    SELECT id, org_id FROM finding_fixes
    WHERE kind = 'source' AND state IN ('refused', 'failed') AND result_recorded_at IS NULL
      AND (CAST(:only_org AS uuid) IS NULL OR org_id = CAST(:only_org AS uuid))
    ORDER BY state_changed_at
    LIMIT :batch
    FOR UPDATE SKIP LOCKED
    """
)
_RECORDED: Final = text(
    """
    UPDATE finding_fixes SET result_recorded_at = :at
    WHERE org_id = :org_id AND id = :fix_id AND result_recorded_at IS NULL
    """
)
# Across every organization: code kept where AI was switched off by a person or removed,
# and code past its 30 days (BR9).
_CODE_DUE: Final = text(
    """
    SELECT fix.id, fix.org_id,
           CASE WHEN ai.id IS NULL OR (NOT ai.enabled AND ai.off_reason = 'person')
                THEN 'ai_off' ELSE 'aged_out' END AS reason
    FROM finding_fixes AS fix
    LEFT JOIN ai_connections AS ai ON ai.org_id = fix.org_id
    WHERE fix.kind = 'source' AND fix.code_deleted_at IS NULL
      AND fix.state NOT IN ('gathering', 'gathered', 'proposing')
      AND (fix.files_read IS NOT NULL OR fix.edits IS NOT NULL)
      AND (ai.id IS NULL OR (NOT ai.enabled AND ai.off_reason = 'person')
           OR fix.finished_at < :aged_before)
      AND (CAST(:only_org AS uuid) IS NULL OR fix.org_id = CAST(:only_org AS uuid))
    ORDER BY fix.state_changed_at
    LIMIT :batch
    """
)
_EXPIRED_PACKS: Final = text(
    "DELETE FROM finding_fix_source_packs WHERE expires_at < :at AND "
    "(CAST(:only_org AS uuid) IS NULL OR org_id = CAST(:only_org AS uuid)) RETURNING org_id"
)
_EXPIRED_CHECKS: Final = text(
    "DELETE FROM finding_fix_checks WHERE expires_at < :at AND "
    "(CAST(:only_org AS uuid) IS NULL OR org_id = CAST(:only_org AS uuid)) RETURNING org_id"
)
_EXPIRED_ATTEMPTS: Final = text(
    "DELETE FROM github_link_attempts WHERE expires_at < :at AND "
    "(CAST(:only_org AS uuid) IS NULL OR org_id = CAST(:only_org AS uuid)) RETURNING org_id"
)
_APPS_DUE: Final = text(
    """
    SELECT id, org_id FROM connections
    WHERE kind = 'github_fix' AND state <> 'disconnected' AND github_app_id IS NOT NULL
      AND sealed_credential IS NOT NULL
      AND (repositories_read_at IS NULL OR repositories_read_at < :refresh_before)
      AND (CAST(:only_org AS uuid) IS NULL OR org_id = CAST(:only_org AS uuid))
    ORDER BY id
    LIMIT :batch
    """
)


@dataclass(frozen=True, slots=True)
class SweptFixes:
    ended: int
    cleared: int
    expired: int
    refreshes: int


async def sweep(
    engine: AsyncEngine, *, clock: Callable[[], datetime], only_org: str | None = None
) -> SweptFixes:
    """Every organization's due rows; `only_org` narrows each pass to one, for a test,
    so a test's sweep never reaches another test's rows."""
    now = clock()
    ended = 0
    for state, after, reason in (
        ("gathering", GATHERING_STUCK_AFTER, "timed_out"),
        ("gathered", GATHERED_LAPSES_AFTER, "lapsed"),
    ):
        async with engine.begin() as connection:
            rows = (
                await connection.execute(
                    _ENDED_WAITING,
                    {
                        "state": state,
                        "before": now - after,
                        "reason": reason,
                        "at": now,
                        "batch": BATCH,
                        "only_org": only_org,
                    },
                )
            ).all()
            for row in rows:
                await connection.execute(_DROP_PACK, {"org_id": row.org_id, "fix_id": row.id})
        ended += len(rows)
    cleared = await _clear_ended_short(engine, now=now, only_org=only_org)
    cleared += await _clear_due(engine, now=now, only_org=only_org)
    async with engine.begin() as connection:
        expired = 0
        for statement in (_EXPIRED_PACKS, _EXPIRED_CHECKS, _EXPIRED_ATTEMPTS):
            expired += len(
                (await connection.execute(statement, {"at": now, "only_org": only_org})).all()
            )
    refreshes = await _queue_refreshes(engine, now=now, only_org=only_org)
    return SweptFixes(ended=ended, cleared=cleared, expired=expired, refreshes=refreshes)


async def _clear_ended_short(engine: AsyncEngine, *, now: datetime, only_org: str | None) -> int:
    async with engine.begin() as connection:
        rows = (
            await connection.execute(_ENDED_SHORT, {"batch": BATCH, "only_org": only_org})
        ).all()
        for row in rows:
            ids = {"org_id": row.org_id, "fix_id": row.id}
            await connection.execute(text(CLEAR_STORED_CODE), {**ids, "reason": "ended_short"})
            await connection.execute(_RECORDED, {**ids, "at": now})
    return len(rows)


async def _clear_due(engine: AsyncEngine, *, now: datetime, only_org: str | None) -> int:
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                _CODE_DUE,
                {"aged_before": now - CODE_KEPT_FOR, "batch": BATCH, "only_org": only_org},
            )
        ).all()
        for row in rows:
            await connection.execute(
                text(CLEAR_STORED_CODE),
                {"org_id": row.org_id, "fix_id": row.id, "reason": row.reason},
            )
    return len(rows)


async def _queue_refreshes(engine: AsyncEngine, *, now: datetime, only_org: str | None) -> int:
    async with engine.begin() as connection:
        due = (
            await connection.execute(
                _APPS_DUE,
                {
                    "refresh_before": now - REPOSITORY_REFRESH,
                    "batch": BATCH,
                    "only_org": only_org,
                },
            )
        ).all()
        for row in due:
            request = JobRequest(
                task=REFRESH_FIX_APP_TASK,
                queue=INTERACTIVE_QUEUE,
                kwargs={"org_id": str(row.org_id), "connection_id": str(row.id)},
                org_id=str(row.org_id),
                expires_seconds=REFRESH_EXPIRES_SECONDS,
            )
            await connection.execute(text(ENQUEUE_UNLESS_WAITING), request.parameters())
    return len(due)


@task(SWEEP_FIXES_TASK)
def sweep_fixes_task() -> None:
    swept = with_engine(lambda engine: sweep(engine, clock=lambda: datetime.now(UTC)))
    if swept.ended or swept.cleared or swept.expired or swept.refreshes:
        logger.info(
            "the suggested fix sweep moved rows",
            ended=swept.ended,
            cleared=swept.cleared,
            expired=swept.expired,
            refreshes=swept.refreshes,
        )
