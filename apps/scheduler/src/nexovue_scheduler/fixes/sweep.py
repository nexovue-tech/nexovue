"""Rung 2b's sweep: general suggestions left in flight, and results not yet recorded (F5
design, *Rung 2b*, step 4; plan T2b.9).

Beat adds it to `light` every minute. It touches no source, no GitHub and no key, so it
runs where `light` runs and stays apart from Step 3's `sweep_fixes`
(`connections/fix_sweep.py`), so rung 2b never needs `scheduler-connections`; each pass reads
general rows only. Two passes, across every organization, each row written by its own
`org_id`:

- **Stuck.** A row still `proposing` past its job's life, the longest attempt and the
  lease's grace: no attempt can still write, so `failed`, `timed_out`.
- **Record, once.** An ended row whose result is not yet recorded: `proposed` writes the
  timeline's `fix_proposed` (ids and counts, FR38); a rule's refusal writes the audit entry
  `fix.withheld` with the rule alone; every end then sets `result_recorded_at`, in the same
  transaction, so a second run writes nothing twice. No email, alert, notification or
  outbox row is ever written for a general row (AC73).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_platform.ai_records import CALL_SECONDS_MAX
from nexovue_platform.cases import (
    RECORD_CASE_AUDIT_SQL,
    RECORD_FINDING_EVENT_SQL,
    case_audit_parameters,
    finding_event_parameters,
)
from nexovue_platform.detection import FINDING_AUDIT_TARGET
from nexovue_platform.fixes.records import (
    FIX_WITHHELD,
    GENERAL_WITHHELD,
    PROPOSED,
    SUGGEST_EXPIRES_SECONDS,
    SUGGEST_HARD_LIMIT_EXTRA_SECONDS,
    SWEEP_GENERAL_FIXES_TASK,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.jobs.queue import LEASE_GRACE_SECONDS
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine

logger = get_logger(__name__)

# Past the queue's life, F3's longest call with the hard limit's extra, and the lease's
# grace, no attempt of the job can still write (design: job expiry plus hard limit).
STUCK_AFTER: Final = timedelta(
    seconds=SUGGEST_EXPIRES_SECONDS
    + CALL_SECONDS_MAX
    + SUGGEST_HARD_LIMIT_EXTRA_SECONDS
    + LEASE_GRACE_SECONDS
)
BATCH: Final = 200
_WITHHELD_RULES: Final = {reason: reason.rsplit("_", 1)[1] for reason in GENERAL_WITHHELD}

# Across every organization: rows nothing will write any more. Each carries its `org_id`.
_STUCK: Final = text(
    """
    UPDATE finding_fixes AS fix
    SET state = 'failed', reason = 'timed_out', state_changed_at = :at, finished_at = :at
    WHERE fix.id IN (
        SELECT stuck.id FROM finding_fixes AS stuck
        WHERE stuck.kind = 'general' AND stuck.state = 'proposing'
          AND stuck.state_changed_at < :before
        ORDER BY stuck.state_changed_at
        LIMIT :batch
        FOR UPDATE SKIP LOCKED
    )
    RETURNING fix.org_id, fix.id
    """
)
# Across every organization: ended rows whose result is not yet recorded.
_UNRECORDED: Final = text(
    """
    SELECT id, org_id, finding_id, kind, state, reason,
           jsonb_array_length(COALESCE(general_suggestion -> 'steps', '[]'::jsonb)) AS steps
    FROM finding_fixes
    WHERE kind = 'general' AND result_recorded_at IS NULL
      AND state IN ('proposed', 'refused', 'failed')
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


@dataclass(frozen=True, slots=True)
class Swept:
    timed_out: int
    recorded: int


async def sweep(engine: AsyncEngine, *, clock: Callable[[], datetime]) -> Swept:
    now = clock()
    async with engine.begin() as connection:
        stuck = (
            await connection.execute(
                _STUCK, {"before": now - STUCK_AFTER, "at": now, "batch": BATCH}
            )
        ).all()
    async with engine.begin() as connection:
        rows = (await connection.execute(_UNRECORDED, {"batch": BATCH})).all()
        for row in rows:
            await _record(connection, row, at=now)
    return Swept(timed_out=len(stuck), recorded=len(rows))


async def _record(connection: AsyncConnection, row: Any, *, at: datetime) -> None:
    """One row's result, written once, in the transaction that marks it recorded."""
    if row.state == PROPOSED:
        await connection.execute(
            text(RECORD_FINDING_EVENT_SQL),
            finding_event_parameters(
                "fix_proposed",
                org_id=row.org_id,
                finding_id=row.finding_id,
                actor_kind="system",
                at=at,
                fix_id=row.id,
                fix_kind=row.kind,
                step_count=row.steps,
            ),
        )
    elif row.reason in _WITHHELD_RULES:
        await connection.execute(
            text(RECORD_CASE_AUDIT_SQL),
            case_audit_parameters(
                org_id=row.org_id,
                actor_user_id=None,
                action=FIX_WITHHELD,
                target=f"{FINDING_AUDIT_TARGET}:{row.finding_id}",
                detail={"fix_id": str(row.id), "rule": _WITHHELD_RULES[row.reason]},
            ),
        )
    await connection.execute(_RECORDED, {"org_id": row.org_id, "fix_id": row.id, "at": at})


@task(SWEEP_GENERAL_FIXES_TASK)
def sweep_general_fixes_task() -> None:
    swept = with_engine(lambda engine: sweep(engine, clock=lambda: datetime.now(UTC)))
    if swept.timed_out or swept.recorded:
        logger.info(
            "the general suggestion sweep ended or recorded rows",
            timed_out=swept.timed_out,
            recorded=swept.recorded,
        )
