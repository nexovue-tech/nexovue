"""What the AI role reads and writes of a general suggestion, as its own account may (F5
design, *The Postgres account*; plan T2b.5, T2b.6; `insight_account.INSIGHT_GRANTS`).

Every statement binds `org_id`. Row security (migration 0077) lets this account see a row in
rung 2b's states and change one only while it is `proposing`, so a write to a row the API or
the sweep has ended matches nothing. It reads `model_input` alone of the explanation, never
the evidence or the names (F3 AC45). The reservation, the run row and its settling are F3's
own (`store.reserve_month`, `store.insert_run`, `store.settle_run`), never a second copy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Protocol

from nexovue_platform.ai.limits import month_of
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.insight import store

_FIX: Final = text(
    """
    SELECT id, org_id, project_id, finding_id, kind, general_language, explanation_version,
           state, retry_code
    FROM finding_fixes
    WHERE org_id = :org_id AND id = :fix_id
    """
)
# The version whose facts the person's click named (FR34): `model_input` alone.
_MODEL_INPUT: Final = text(
    """
    SELECT model_input FROM finding_explanations
    WHERE org_id = :org_id AND finding_id = :finding_id AND version = :version
    """
)
# The admission's lock: the row itself, still `proposing`, so a second job waits on the first.
_LOCK_PROPOSING: Final = text(
    """
    SELECT id FROM finding_fixes
    WHERE org_id = :org_id AND id = :fix_id AND state = 'proposing'
    FOR UPDATE
    """
)
_END: Final = text(
    """
    UPDATE finding_fixes
    SET state = :state, reason = :reason, general_suggestion = CAST(:suggestion AS jsonb),
        evidence_ids = CAST(:evidence_ids AS jsonb), tokens_used = tokens_used + :tokens,
        state_changed_at = :at, finished_at = :at
    WHERE org_id = :org_id AND id = :fix_id AND state = 'proposing'
    """
)
# A G1 or G2 answer, or a provider that may answer next time: the row stays `proposing` for
# the next attempt, which reads the fixed `retry_code` (D51).
_KEEP_FOR_RETRY: Final = text(
    """
    UPDATE finding_fixes
    SET retry_code = CAST(:retry_code AS text), tokens_used = tokens_used + :tokens
    WHERE org_id = :org_id AND id = :fix_id AND state = 'proposing'
    """
)


class FixOfProject(Protocol):
    """What admitting a call and ending a row need of either kind of suggestion."""

    @property
    def id(self) -> str: ...
    @property
    def org_id(self) -> str: ...
    @property
    def project_id(self) -> str: ...


@dataclass(frozen=True, slots=True)
class StoredFix:
    """A suggestion as the AI role may read it: no answer, no person."""

    id: str
    org_id: str
    project_id: str
    finding_id: str
    kind: str
    language: str | None
    explanation_version: int
    state: str
    retry_code: str | None


@dataclass(frozen=True, slots=True)
class FixEnd:
    """Where a call leaves the row: an end state, or `None` to stay `proposing` for a retry."""

    state: str | None
    reason: str | None = None
    suggestion: dict[str, Any] | None = None
    evidence_ids: tuple[str, ...] | None = None
    retry_code: str | None = None


@dataclass(frozen=True, slots=True)
class FixAdmission:
    run_id: str | None = None
    month: date | None = None
    refused: str | None = None
    stale: bool = False


async def load_fix(engine: AsyncEngine, *, org_id: str, fix_id: str) -> StoredFix | None:
    async with engine.connect() as connection:
        row = (await connection.execute(_FIX, {"org_id": org_id, "fix_id": fix_id})).one_or_none()
    if row is None:
        return None
    return StoredFix(
        id=str(row.id),
        org_id=str(row.org_id),
        project_id=str(row.project_id),
        finding_id=str(row.finding_id),
        kind=row.kind,
        language=row.general_language,
        explanation_version=row.explanation_version,
        state=row.state,
        retry_code=row.retry_code,
    )


async def load_model_input(engine: AsyncEngine, fix: StoredFix) -> dict[str, Any] | None:
    return await model_input_of(
        engine, org_id=fix.org_id, finding_id=fix.finding_id, version=fix.explanation_version
    )


async def model_input_of(
    engine: AsyncEngine, *, org_id: str, finding_id: str, version: int
) -> dict[str, Any] | None:
    """`model_input` alone of one explanation version, for either kind of suggestion."""
    async with engine.connect() as connection:
        found = await connection.scalar(
            _MODEL_INPUT, {"org_id": org_id, "finding_id": finding_id, "version": version}
        )
    return found if isinstance(found, dict) else None


async def admit(
    engine: AsyncEngine,
    fix: FixOfProject,
    *,
    reserve: int,
    ceiling: int,
    run: store.RunStart,
    now: datetime,
) -> FixAdmission:
    """The month's reservation against F5's share, then the run row, in one transaction under
    the row's lock, so parallel jobs cannot pass the limit (NFR3). A refusal ends the row
    in that same transaction and spends nothing. The run row is committed before the call."""
    month = month_of(now)
    async with engine.begin() as connection:
        locked = await connection.scalar(_LOCK_PROPOSING, {"org_id": fix.org_id, "fix_id": fix.id})
        if locked is None:
            return FixAdmission(stale=True)
        if not await store.reserve_month(
            connection, org_id=fix.org_id, month=month, reserve=reserve, ceiling=ceiling
        ):
            await connection.execute(
                _END, _end_parameters(fix, FixEnd("failed", "limit_reached"), 0, now)
            )
            return FixAdmission(refused="limit_reached")
        run_id = await store.insert_run(connection, run)
    return FixAdmission(run_id=run_id, month=month)


async def end_without_call(
    engine: AsyncEngine, fix: StoredFix, end: FixEnd, *, at: datetime
) -> bool:
    """An end decided before anything was sent or reserved. False when the row had moved."""
    async with engine.begin() as connection:
        changed = await connection.execute(_END, _end_parameters(fix, end, 0, at))
    return bool(changed.rowcount)


async def finish(
    engine: AsyncEngine,
    fix: StoredFix,
    *,
    run_id: str,
    month: date,
    reserved: int,
    run_end: store.RunEnd,
    end: FixEnd,
) -> store.Finished:
    """A call's end in one transaction: F3's settling of its tokens and run row, then the row
    ended, or kept `proposing` with its retry code. A run F3's sweep had already called
    `died` settles nothing and writes nothing (`store.settle_run`): `already_ended`."""
    async with engine.begin() as connection:
        if not await store.settle_run(
            connection,
            org_id=fix.org_id,
            run_id=run_id,
            month=month,
            reserved=reserved,
            end=run_end,
        ):
            return "already_ended"
        if end.state is None:
            changed = await connection.execute(
                _KEEP_FOR_RETRY,
                {
                    "org_id": fix.org_id,
                    "fix_id": fix.id,
                    "retry_code": end.retry_code,
                    "tokens": run_end.tokens,
                },
            )
        else:
            changed = await connection.execute(
                _END, _end_parameters(fix, end, run_end.tokens, run_end.finished_at)
            )
    return "written" if changed.rowcount else "not_written"


def _end_parameters(fix: FixOfProject, end: FixEnd, tokens: int, at: datetime) -> dict[str, Any]:
    return {
        "org_id": fix.org_id,
        "fix_id": fix.id,
        "state": end.state,
        "reason": end.reason,
        "suggestion": None if end.suggestion is None else json.dumps(end.suggestion),
        "evidence_ids": None if end.evidence_ids is None else json.dumps(list(end.evidence_ids)),
        "tokens": tokens,
        "at": at,
    }
