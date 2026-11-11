"""What the AI role reads and writes in Postgres, as its own account may (design, *The Postgres
account*; `insight_account.INSIGHT_GRANTS`).

Every statement binds `org_id`. A run row is written, and committed, **before** the call, so
a child killed mid-call still leaves a record (design, step 3); only its ending, tokens and
finish are changed afterwards, the columns this account may update (AC72). Reservations
against the monthly limit are plan T5.3's; a connection test takes none (A51, AC53).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

_CONNECTION: Final = text(
    """
    SELECT id, wire_format, base_url, model, region_label, key_id, sealed_key, call_seconds
    FROM ai_connections WHERE org_id = :org_id
    """
)
_START_RUN: Final = text(
    """
    INSERT INTO ai_runs (
        org_id, requested_by, purpose, wire_format, model, region_label, provider_host,
        prompt_version, request_sha256, fields_sent, attempt
    ) VALUES (
        :org_id, :requested_by, :purpose, :wire_format, :model, :region_label, :provider_host,
        :prompt_version, :request_sha256, CAST(:fields_sent AS jsonb), :attempt
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


def month_of(moment: datetime) -> date:
    """The usage row a moment counts toward: the first of its month, UTC (design)."""
    return date(moment.year, moment.month, 1)


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
    )


async def start_run(engine: AsyncEngine, run: RunStart) -> str:
    """The run's row, committed before any byte is sent. A person deleted while the job
    waited is refused by the foreign key, and the row is then written with no one."""
    parameters: dict[str, Any] = {
        "org_id": run.org_id,
        "requested_by": run.requested_by,
        "purpose": run.purpose,
        "wire_format": run.wire_format,
        "model": run.model,
        "region_label": run.region_label,
        "provider_host": run.provider_host,
        "prompt_version": run.prompt_version,
        "request_sha256": run.request_sha256,
        "fields_sent": json.dumps(list(run.fields_sent)),
        "attempt": run.attempt,
    }
    try:
        async with engine.begin() as connection:
            run_id: uuid.UUID = (await connection.execute(_START_RUN, parameters)).scalar_one()
    except IntegrityError:
        if run.requested_by is None:
            raise
        async with engine.begin() as connection:
            run_id = (
                await connection.execute(_START_RUN, parameters | {"requested_by": None})
            ).scalar_one()
    return str(run_id)


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
