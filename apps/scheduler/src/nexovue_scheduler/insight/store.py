"""What the AI role reads and writes in Postgres, as its own account may (design, *The Postgres
account*; `insight_account.INSIGHT_GRANTS`).

Every statement binds `org_id`, except the sweep's two, which read every organization's
stuck rows and act on each by its own `org_id` (`sweep.py`). A run row is written, and
committed, **before** the call, so a child killed mid-call still leaves a record (design,
step 3); only its ending, tokens and finish are changed afterwards, the columns this account
may update (AC72).

**Reservations** (plan T5.3; design, *Limits*): a wording run takes its input estimate plus
the most it may write from the month's limit, in the same transaction as its run row, under a
row lock on the finding's first explanation that also holds the run cap (A58: this account
has no advisory locks). A finished run moves what it used to `used_tokens` and returns the
rest; a run that dies keeps it all, moved to used by the sweep. A connection test takes none
(A51, AC53).

**A run is settled once.** Its finish and the sweep each act only on a run still `started`,
and its reservation moves only in the transaction that ends it, so a finish arriving after
the sweep called the run `died` changes nothing: no tokens, no state, no words.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal

from nexovue_platform.ai.limits import RUN_CAP_WINDOW, RUNS_PER_FINDING, month_of
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

_CONNECTION: Final = text(
    """
    SELECT id, wire_format, base_url, model, region_label, key_id, sealed_key, call_seconds,
           enabled, monthly_token_limit, consented_base_url, consented_fingerprint,
           consented_schema
    FROM ai_connections WHERE org_id = :org_id
    """
)
_START_RUN: Final = text(
    """
    INSERT INTO ai_runs (
        org_id, project_id, requested_by, purpose, subject_id, explanation_id, generation,
        wire_format, model, region_label, provider_host, prompt_version, request_sha256,
        fields_sent, attempt, reserved_tokens, started_at
    ) VALUES (
        :org_id, :project_id, :requested_by, :purpose, :subject_id, :explanation_id,
        :generation, :wire_format, :model, :region_label, :provider_host, :prompt_version,
        :request_sha256, CAST(:fields_sent AS jsonb), :attempt, :reserved_tokens,
        COALESCE(CAST(:started_at AS timestamptz), now())
    )
    RETURNING id
    """
)
_FINISH_RUN: Final = text(
    """
    UPDATE ai_runs
    SET outcome = :outcome, input_tokens = :input_tokens, output_tokens = :output_tokens,
        tokens_estimated = :tokens_estimated, finished_at = :finished_at
    WHERE org_id = :org_id AND id = :run_id AND outcome = 'started'
    """
)
# Tokens spent, counted to the month whether or not a limit is set (AC53's null-limit case).
_METER: Final = text(
    """
    INSERT INTO ai_usage_monthly (org_id, month, used_tokens)
    VALUES (:org_id, :month, :tokens)
    ON CONFLICT (org_id, month)
    DO UPDATE SET used_tokens = ai_usage_monthly.used_tokens + EXCLUDED.used_tokens
    """
)
# A test's result lands only on the values it tested: a change saved meanwhile keeps its own
# (untested) state (design, *The Postgres account*).
_RECORD_CHECK: Final = text(
    """
    UPDATE ai_connections
    SET checked_at = :at, checked_ok = :ok, checked_fingerprint = :fingerprint
    WHERE org_id = :org_id AND id = :connection_id
      AND wire_format = :wire_format AND base_url = :base_url AND model = :model
      AND region_label IS NOT DISTINCT FROM CAST(:region_label AS text)
      AND key_id IS NOT DISTINCT FROM CAST(:key_id AS uuid)
    """
)


# ------------------------------------------------------------- a wording run

# What `word` may read of one version: `model_input`, never the evidence or the names (AC45).
_EXPLANATION: Final = text(
    """
    SELECT e.id, e.org_id, e.project_id, e.finding_id, e.version, e.model_input,
           e.wording_state, e.wording_generation, e.wording_changed_at,
           EXISTS (
               SELECT 1 FROM finding_explanations AS newer
               WHERE newer.org_id = e.org_id AND newer.finding_id = e.finding_id
                 AND newer.version > e.version
           ) AS superseded
    FROM finding_explanations AS e
    WHERE e.org_id = :org_id AND e.id = :explanation_id
    """
)
_OPTED_OUT: Final = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM ai_project_opt_outs WHERE org_id = :org_id AND project_id = :project_id
    )
    """
)
_PROJECT_LIVE: Final = text(
    "SELECT deleted_at IS NULL FROM projects WHERE org_id = :org_id AND id = :project_id"
)
# The cap's lock: one row per finding, never a table lock (the account holds no table UPDATE).
_LOCK_FINDING: Final = text(
    """
    SELECT id FROM finding_explanations
    WHERE org_id = :org_id AND finding_id = :finding_id AND version = 1
    FOR UPDATE
    """
)
# A run of this generation still under way: a job run twice waits on the first (AC56).
_RUN_UNDER_WAY: Final = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM ai_runs
        WHERE org_id = :org_id AND explanation_id = :explanation_id
          AND generation = :generation AND outcome = 'started'
    )
    """
)
_STATE_NOW: Final = text(
    """
    SELECT wording_state, wording_generation FROM finding_explanations
    WHERE org_id = :org_id AND id = :explanation_id
    """
)
# Decided runs: first attempts only, so an invalid answer and its retry are one (AC58).
_DECIDED_RUNS: Final = text(
    """
    SELECT count(*) FROM ai_runs
    WHERE org_id = :org_id AND subject_id = :finding_id AND purpose = 'explain'
      AND attempt = 1 AND started_at > :since
    """
)
_MONTH_ROW: Final = text(
    """
    INSERT INTO ai_usage_monthly (org_id, month) VALUES (:org_id, :month)
    ON CONFLICT (org_id, month) DO NOTHING
    """
)
# Atomic: parallel runs cannot all fit in what is left (design, *Limits*).
_RESERVE: Final = text(
    """
    UPDATE ai_usage_monthly SET reserved_tokens = reserved_tokens + :reserve
    WHERE org_id = :org_id AND month = :month
      AND used_tokens + reserved_tokens + :reserve <= :ceiling
    RETURNING reserved_tokens
    """
)
# What a run used is counted even past its reservation; usage may pass the limit by that one
# overshoot, and the next run is refused (BR13). Only after `_FINISH_RUN` or `_RUN_DIED` ended
# the run in the same transaction, so no run's reservation is subtracted twice.
_SETTLE: Final = text(
    """
    UPDATE ai_usage_monthly
    SET used_tokens = used_tokens + :used,
        reserved_tokens = GREATEST(reserved_tokens - :reserved, 0)
    WHERE org_id = :org_id AND month = :month
    """
)
# Each write over `pending` names the generation it was asked for: a newer click, a mute or an
# opt-out since is never overwritten, and the first result of a job run twice stands (AC56).
_SET_STATE: Final = text(
    """
    UPDATE finding_explanations
    SET wording_state = :state, wording_reason = :reason, wording_changed_at = :at
    WHERE org_id = :org_id AND id = :explanation_id
      AND wording_state = 'pending' AND wording_generation = :generation
    """
)
_SET_WORDS: Final = text(
    """
    UPDATE finding_explanations
    SET wording = CAST(:wording AS jsonb), wording_state = :state, wording_reason = NULL,
        wording_from_version = version, wording_changed_at = :at
    WHERE org_id = :org_id AND id = :explanation_id
      AND wording_state = 'pending' AND wording_generation = :generation
    """
)

# ------------------------------------------------------------------ the sweep

# Across every organization: runs still `started` past any hard limit, so their child was
# killed (design, *Failure handling*). Each row carries its own `org_id`, which every write
# that follows binds.
_DIED_RUNS: Final = text(
    """
    SELECT id, org_id, explanation_id, generation, reserved_tokens, started_at
    FROM ai_runs
    WHERE outcome = 'started' AND started_at < :before
    ORDER BY started_at
    LIMIT :batch
    FOR UPDATE SKIP LOCKED
    """
)
_RUN_DIED: Final = text(
    """
    UPDATE ai_runs
    SET outcome = 'died', finished_at = :at, input_tokens = :reserved, output_tokens = 0,
        tokens_estimated = true
    WHERE org_id = :org_id AND id = :run_id AND outcome = 'started'
    """
)
# Not while a later attempt of the same generation is under way: it may still write.
_EXPLANATION_DIED: Final = text(
    """
    UPDATE finding_explanations AS e
    SET wording_state = 'failed', wording_reason = 'died', wording_changed_at = :at
    WHERE e.org_id = :org_id AND e.id = :explanation_id
      AND e.wording_state = 'pending' AND e.wording_generation = :generation
      AND NOT EXISTS (
          SELECT 1 FROM ai_runs AS later
          WHERE later.org_id = e.org_id AND later.explanation_id = e.id
            AND later.generation = e.wording_generation AND later.outcome = 'started'
            AND later.id <> :run_id
      )
    """
)
# Across every organization: waiting past the job's life with no run under way, so the job
# expired or was spent and nothing will write (`_SWEEP_SPENT` deletes it and changes nothing).
_EXPIRED: Final = text(
    """
    UPDATE finding_explanations AS e
    SET wording_state = 'failed', wording_reason = 'expired', wording_changed_at = :at
    WHERE e.id IN (
        SELECT stuck.id FROM finding_explanations AS stuck
        WHERE stuck.wording_state = 'pending' AND stuck.wording_changed_at < :before
          AND NOT EXISTS (
              SELECT 1 FROM ai_runs AS live
              WHERE live.org_id = stuck.org_id AND live.explanation_id = stuck.id
                AND live.generation = stuck.wording_generation AND live.outcome = 'started'
          )
        LIMIT :batch
        FOR UPDATE SKIP LOCKED
    )
    RETURNING e.org_id, e.id
    """
)


@dataclass(frozen=True, slots=True)
class StoredConnection:
    id: str
    wire_format: str
    base_url: str
    model: str
    region_label: str | None
    key_id: str | None
    sealed_key: bytes | None
    call_seconds: int
    enabled: bool = False
    monthly_token_limit: int | None = None
    consented_base_url: str | None = None
    consented_fingerprint: str | None = None
    consented_schema: int | None = None


@dataclass(frozen=True, slots=True)
class RunStart:
    org_id: str
    requested_by: str | None
    purpose: str
    wire_format: str
    model: str
    region_label: str | None
    provider_host: str
    prompt_version: str
    request_sha256: str
    fields_sent: tuple[str, ...] = ()
    attempt: int = 1
    project_id: str | None = None
    # The finding, for the run cap and the run list.
    subject_id: str | None = None
    explanation_id: str | None = None
    generation: int | None = None
    reserved_tokens: int = 0
    # The job's own clock, so the run cap and the sweep read one time; the database's when
    # None (the connection test).
    started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RunEnd:
    outcome: str
    input_tokens: int | None
    output_tokens: int | None
    tokens_estimated: bool
    finished_at: datetime

    @property
    def tokens(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0)


async def load_connection(engine: AsyncEngine, org_id: str) -> StoredConnection | None:
    async with engine.connect() as connection:
        row = (await connection.execute(_CONNECTION, {"org_id": org_id})).one_or_none()
    if row is None:
        return None
    return StoredConnection(
        id=str(row.id),
        wire_format=row.wire_format,
        base_url=row.base_url,
        model=row.model,
        region_label=row.region_label,
        key_id=None if row.key_id is None else str(row.key_id),
        sealed_key=None if row.sealed_key is None else bytes(row.sealed_key),
        call_seconds=row.call_seconds,
        enabled=row.enabled,
        monthly_token_limit=row.monthly_token_limit,
        consented_base_url=row.consented_base_url,
        consented_fingerprint=row.consented_fingerprint,
        consented_schema=row.consented_schema,
    )


def _run_parameters(run: RunStart) -> dict[str, Any]:
    return {
        "org_id": run.org_id,
        "project_id": run.project_id,
        "requested_by": run.requested_by,
        "purpose": run.purpose,
        "subject_id": run.subject_id,
        "explanation_id": run.explanation_id,
        "generation": run.generation,
        "wire_format": run.wire_format,
        "model": run.model,
        "region_label": run.region_label,
        "provider_host": run.provider_host,
        "prompt_version": run.prompt_version,
        "request_sha256": run.request_sha256,
        "fields_sent": json.dumps(list(run.fields_sent)),
        "attempt": run.attempt,
        "reserved_tokens": run.reserved_tokens,
        "started_at": run.started_at,
    }


async def insert_run(connection: AsyncConnection, run: RunStart) -> str:
    """The run's row, in the caller's transaction (F5's admission takes it beside its
    reservation, as `admit` does). A person deleted while the job waited is refused by the
    foreign key, and the row is then written with no one, in a savepoint so the
    transaction goes on."""
    parameters = _run_parameters(run)
    try:
        async with connection.begin_nested():
            run_id: uuid.UUID = (await connection.execute(_START_RUN, parameters)).scalar_one()
    except IntegrityError:
        if run.requested_by is None:
            raise
        run_id = (
            await connection.execute(_START_RUN, parameters | {"requested_by": None})
        ).scalar_one()
    return str(run_id)


async def start_run(engine: AsyncEngine, run: RunStart) -> str:
    """The run's row, committed before any byte is sent."""
    async with engine.begin() as connection:
        return await insert_run(connection, run)


async def finish_check(
    engine: AsyncEngine,
    *,
    org_id: str,
    connection: StoredConnection,
    fingerprint: str,
    passed: bool,
    at: datetime,
    run: tuple[str, RunEnd] | None,
) -> None:
    """The run's ending and its tokens on the month, when a call was made, and the test's
    result on the connection, in one transaction."""
    async with engine.begin() as db:
        if run is not None:
            run_id, end = run
            await db.execute(
                _FINISH_RUN,
                {
                    "org_id": org_id,
                    "run_id": run_id,
                    "outcome": end.outcome,
                    "input_tokens": end.input_tokens,
                    "output_tokens": end.output_tokens,
                    "tokens_estimated": end.tokens_estimated,
                    "finished_at": end.finished_at,
                },
            )
            if end.tokens:
                await db.execute(
                    _METER,
                    {"org_id": org_id, "month": month_of(end.finished_at), "tokens": end.tokens},
                )
        await db.execute(
            _RECORD_CHECK,
            {
                "org_id": org_id,
                "connection_id": connection.id,
                "wire_format": connection.wire_format,
                "base_url": connection.base_url,
                "model": connection.model,
                "region_label": connection.region_label,
                "key_id": connection.key_id,
                "at": at,
                "ok": passed,
                "fingerprint": fingerprint,
            },
        )


# ------------------------------------------------------------- a wording run


@dataclass(frozen=True, slots=True)
class StoredVersion:
    """One version as `word` may read it."""

    id: str
    org_id: str
    project_id: str
    finding_id: str
    version: int
    model_input: dict[str, Any] | None
    wording_state: str
    wording_generation: int
    wording_changed_at: datetime | None
    superseded: bool


async def load_version(
    engine: AsyncEngine, *, org_id: str, explanation_id: str
) -> StoredVersion | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                _EXPLANATION, {"org_id": org_id, "explanation_id": explanation_id}
            )
        ).one_or_none()
    if row is None:
        return None
    return StoredVersion(
        id=str(row.id),
        org_id=str(row.org_id),
        project_id=str(row.project_id),
        finding_id=str(row.finding_id),
        version=row.version,
        model_input=row.model_input,
        wording_state=row.wording_state,
        wording_generation=row.wording_generation,
        wording_changed_at=row.wording_changed_at,
        superseded=bool(row.superseded),
    )


async def project_takes_wording(engine: AsyncEngine, *, org_id: str, project_id: str) -> bool:
    """Not deleted and not opted out (FR28, AC39), read again just before a run spends."""
    parameters = {"org_id": org_id, "project_id": project_id}
    async with engine.connect() as connection:
        live = await connection.scalar(_PROJECT_LIVE, parameters)
        opted_out = await connection.scalar(_OPTED_OUT, parameters)
    return bool(live) and not opted_out


@dataclass(frozen=True, slots=True)
class Admission:
    """What the limits said, in their order: a run row and its reservation, or why not."""

    run_id: str | None = None
    month: date | None = None
    refused: str | None = None
    # True when the row moved on (a click, a mute, an opt-out) before the lock was held.
    stale: bool = False


async def admit(
    engine: AsyncEngine,
    *,
    version: StoredVersion,
    generation: int,
    first_attempt: bool,
    reserve: int,
    ceiling: int,
    too_long: bool,
    run: RunStart,
    now: datetime,
) -> Admission:
    """The cap, the input limit and the month's limit, then the run row, in one transaction
    under the finding's row lock, so parallel jobs cannot pass the cap or the limit (AC53,
    AC58). A refusal writes its state over `pending` in that same transaction and spends
    nothing. The row is committed before the call (design, step 3)."""
    parameters = {"org_id": version.org_id, "explanation_id": version.id}
    async with engine.begin() as connection:
        await connection.execute(
            _LOCK_FINDING, {"org_id": version.org_id, "finding_id": version.finding_id}
        )
        current = (await connection.execute(_STATE_NOW, parameters)).one_or_none()
        if current is None or current.wording_state != "pending":
            return Admission(stale=True)
        if current.wording_generation != generation:
            return Admission(stale=True)
        if await connection.scalar(_RUN_UNDER_WAY, parameters | {"generation": generation}):
            return Admission(stale=True)
        refused = await _limit_refusal(
            connection,
            version=version,
            first_attempt=first_attempt,
            reserve=reserve,
            ceiling=ceiling,
            too_long=too_long,
            now=now,
        )
        if refused is not None:
            state, reason = refused
            await connection.execute(
                _SET_STATE,
                parameters
                | {"state": state, "reason": reason, "at": now, "generation": generation},
            )
            return Admission(refused=reason or state)
        run_id = await insert_run(connection, run)
    return Admission(run_id=run_id, month=month_of(now))


async def _limit_refusal(
    connection: AsyncConnection,
    *,
    version: StoredVersion,
    first_attempt: bool,
    reserve: int,
    ceiling: int,
    too_long: bool,
    now: datetime,
) -> tuple[str, str | None] | None:
    """The first limit a run meets, as the state and reason it leaves, or None."""
    if first_attempt:
        decided = await connection.scalar(
            _DECIDED_RUNS,
            {
                "org_id": version.org_id,
                "finding_id": version.finding_id,
                "since": now - RUN_CAP_WINDOW,
            },
        )
        if int(decided or 0) >= RUNS_PER_FINDING:
            return ("failed", "run_cap")
    if too_long:
        return ("failed", "too_long")
    if not await reserve_month(
        connection, org_id=version.org_id, month=month_of(now), reserve=reserve, ceiling=ceiling
    ):
        return ("over_limit", None)
    return None


async def reserve_month(
    connection: AsyncConnection, *, org_id: str, month: date, reserve: int, ceiling: int
) -> bool:
    """Take `reserve` tokens of the month, or nothing when used plus reserved would pass
    `ceiling`. Atomic, in the caller's transaction; F4's chat reserves through it too."""
    await connection.execute(_MONTH_ROW, {"org_id": org_id, "month": month})
    taken = await connection.scalar(
        _RESERVE, {"org_id": org_id, "month": month, "reserve": reserve, "ceiling": ceiling}
    )
    return taken is not None


async def set_state(
    engine: AsyncEngine,
    *,
    org_id: str,
    explanation_id: str,
    generation: int,
    state: str,
    reason: str | None,
    at: datetime,
) -> bool:
    """A state over `pending` of this generation; False when the row had moved on."""
    async with engine.begin() as connection:
        changed = await connection.execute(
            _SET_STATE,
            {
                "org_id": org_id,
                "explanation_id": explanation_id,
                "generation": generation,
                "state": state,
                "reason": reason,
                "at": at,
            },
        )
    return bool(changed.rowcount)


# How a run's ending landed: its words or state taken, ended with the explanation already
# moved on (a click, a mute, or a retry still to come), or too late, after the sweep had
# called the run `died`.
Finished = Literal["written", "not_written", "already_ended"]


async def settle_run(
    connection: AsyncConnection,
    *,
    org_id: str,
    run_id: str,
    month: date,
    reserved: int,
    end: RunEnd,
) -> bool:
    """A call's end, in the caller's transaction: its run row finished, then what it used
    moved from reserved to used. F5's general suggestion settles through it too.

    Only while the run is still `started`: the run row's lock, taken first, is the one the
    sweep holds, so one of the two ends the run and the other finds it ended. False when the
    sweep had ended it: nothing moved, its charge stands, and the caller writes nothing."""
    ended = await connection.execute(
        _FINISH_RUN,
        {
            "org_id": org_id,
            "run_id": run_id,
            "outcome": end.outcome,
            "input_tokens": end.input_tokens,
            "output_tokens": end.output_tokens,
            "tokens_estimated": end.tokens_estimated,
            "finished_at": end.finished_at,
        },
    )
    if not ended.rowcount:
        return False
    await connection.execute(
        _SETTLE, {"org_id": org_id, "month": month, "used": end.tokens, "reserved": reserved}
    )
    return True


async def finish_wording(
    engine: AsyncEngine,
    *,
    org_id: str,
    explanation_id: str,
    generation: int,
    run_id: str,
    month: date,
    reserved: int,
    end: RunEnd,
    words: dict[str, Any] | None,
    state: str | None,
    reason: str | None,
) -> Finished:
    """A run's ending in one transaction: its row finished, its tokens moved from reserved to
    used, and, unless it will be tried again (`state` None), the explanation's words or state
    over `pending` of its generation. A run the sweep has ended (`settle_run`) keeps the
    sweep's charge, and its words never land."""
    key = {"org_id": org_id, "explanation_id": explanation_id, "generation": generation}
    async with engine.begin() as connection:
        if not await settle_run(
            connection, org_id=org_id, run_id=run_id, month=month, reserved=reserved, end=end
        ):
            return "already_ended"
        if state is None:
            return "not_written"
        if words is not None:
            changed = await connection.execute(
                _SET_WORDS,
                key | {"wording": json.dumps(words), "state": state, "at": end.finished_at},
            )
        else:
            changed = await connection.execute(
                _SET_STATE, key | {"state": state, "reason": reason, "at": end.finished_at}
            )
    return "written" if changed.rowcount else "not_written"


# ------------------------------------------------------------------ the sweep


@dataclass(frozen=True, slots=True)
class Swept:
    died: int
    expired: int


async def sweep_died(engine: AsyncEngine, *, before: datetime, now: datetime, batch: int) -> int:
    """Runs a killed child left `started`: `died`, their reservation moved to used and flagged
    estimated (overstating is the safe side), and their explanation `failed`, reason `died`,
    where still pending at that generation (AC54)."""
    async with engine.begin() as connection:
        rows = (await connection.execute(_DIED_RUNS, {"before": before, "batch": batch})).all()
        for row in rows:
            await connection.execute(
                _RUN_DIED,
                {
                    "org_id": row.org_id,
                    "run_id": row.id,
                    "at": now,
                    "reserved": row.reserved_tokens,
                },
            )
            if row.reserved_tokens:
                await connection.execute(
                    _SETTLE,
                    {
                        "org_id": row.org_id,
                        "month": month_of(row.started_at),
                        "used": row.reserved_tokens,
                        "reserved": row.reserved_tokens,
                    },
                )
            if row.explanation_id is not None:
                await connection.execute(
                    _EXPLANATION_DIED,
                    {
                        "org_id": row.org_id,
                        "explanation_id": row.explanation_id,
                        "generation": row.generation,
                        "run_id": row.id,
                        "at": now,
                    },
                )
    return len(rows)


async def sweep_expired(engine: AsyncEngine, *, before: datetime, now: datetime, batch: int) -> int:
    """Wording left `pending` past its job's life with no run under way: `failed`, reason
    `expired` (AC81). The container may have been stopped all that time."""
    async with engine.begin() as connection:
        rows = (
            await connection.execute(_EXPIRED, {"before": before, "at": now, "batch": batch})
        ).all()
    return len(rows)
