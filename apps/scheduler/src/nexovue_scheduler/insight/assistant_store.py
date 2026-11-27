"""F4: what chat's loop reads and writes in Postgres, as `nexovue_insight` may (design,
*Grants*; migration 0073).

Every statement binds `org_id`. Row-level security shows this account a question only while
it is in flight or holds a reservation, and its writes can only keep it in flight: **every
final write is `assistant_finish_message`**, and every token moved is
`assistant_record_spend`. A statement that finds no row is a normal stop: the person
stopped, or the sweep, deletion or retention came first.

The reservation is F3's: `store.reserve_month` against `limits.chat_ceiling` (design,
*Stopping at 80%*), so wording and chat share one statement and one month row.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from nexovue_platform.ai.chat import provider_label
from nexovue_platform.ai.connection import consent_fingerprint, provider_host
from nexovue_platform.ai.embed import EmbeddingTarget, vector_literal
from nexovue_platform.ai.limits import chat_ceiling, month_of
from nexovue_platform.assistant_records import (
    CHAT_DISCLOSURE_VERSION,
    FailureReason,
    Phase,
    Remainder,
    StepState,
)
from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.evidence.model_facts import SCHEMA_VERSION
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.insight import knowledge
from nexovue_scheduler.insight.knowledge import Ready
from nexovue_scheduler.insight.store import reserve_month

_START: Final = text(
    """
    UPDATE assistant_messages SET state = 'running', phase = 'asking'
    WHERE org_id = :org_id AND id = :id AND state = 'queued' AND now() < deadline_at
    RETURNING id, org_id, project_id, user_id, question, context, history, data_level,
              sealed_ticket, call_seconds, deadline_at
    """
)
_SCOPE: Final = text(
    """
    SELECT p.name, p.timezone, s.mask_emails, r.raw_retention_days, r.derived_retention_days
    FROM projects AS p
    JOIN assistant_settings AS s ON s.org_id = p.org_id
    JOIN project_retention AS r ON r.org_id = p.org_id AND r.project_id = p.id
    WHERE p.org_id = :org_id AND p.id = :project_id AND p.deleted_at IS NULL
    """
)
_CONNECTION: Final = text(
    """
    SELECT id, wire_format, base_url, model, region_label, key_id, sealed_key, call_seconds,
           monthly_token_limit, enabled, consented_base_url, consented_fingerprint,
           consented_schema
    FROM ai_connections WHERE org_id = :org_id
    """
)
_SETTINGS: Final = text(
    """
    SELECT enabled, data_level, consented_fingerprint, consented_disclosure_version,
           consented_data_level
    FROM assistant_settings WHERE org_id = :org_id
    """
)
_OPTED_OUT: Final = text(
    "SELECT 1 FROM ai_project_opt_outs WHERE org_id = :org_id AND project_id = :project_id"
)
_LIVE: Final = text(
    """
    SELECT 1 FROM projects AS p JOIN organizations AS o ON o.id = p.org_id
    WHERE p.org_id = :org_id AND p.id = :project_id
      AND p.deleted_at IS NULL AND o.deleted_at IS NULL
    """
)
_HOLD: Final = text(
    """
    UPDATE assistant_messages SET reserved_tokens = :reserve, reserved_month = :month
    WHERE org_id = :org_id AND id = :id AND state = 'running'
    RETURNING id
    """
)
_STATE: Final = text("SELECT state FROM assistant_messages WHERE org_id = :org_id AND id = :id")
_PHASE: Final = text(
    """
    UPDATE assistant_messages SET phase = :phase
    WHERE org_id = :org_id AND id = :id AND state = 'running'
    RETURNING id
    """
)
_START_RUN: Final = text(
    """
    INSERT INTO ai_runs (
        org_id, project_id, requested_by, purpose, subject_id, wire_format, model,
        region_label, provider_host, prompt_version, fields_sent
    ) VALUES (
        :org_id, :project_id, :requested_by, 'chat', :subject_id, :wire_format, :model,
        :region_label, :provider_host, :prompt_version, CAST(:fields_sent AS jsonb)
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
# Step 6: a search phrase's embedding call, from the question's own reservation like chat's.
_START_EMBED_RUN: Final = text(
    """
    INSERT INTO ai_runs (
        org_id, project_id, requested_by, purpose, subject_id, wire_format, model,
        region_label, provider_host, prompt_version, request_sha256, fields_sent
    ) VALUES (
        :org_id, :project_id, :requested_by, 'embed', :subject_id, :wire_format, :model,
        NULL, :provider_host, :prompt_version, :request_sha256, CAST(:fields_sent AS jsonb)
    )
    RETURNING id
    """
)
# The phrase's own vector and model, on its step while the question runs; the finish nulls
# both (migration 0078). Only a running step of a visible question matches (row security).
_QUERY_EMBEDDING: Final = text(
    """
    UPDATE assistant_steps
    SET query_embedding = CAST(:vector AS vector), query_embedding_model = :model
    WHERE org_id = :org_id AND id = :id AND state = 'running'
    RETURNING id
    """
)
_SPEND: Final = text("SELECT assistant_record_spend(:id, :spent, :estimated)")
_ADD_STEP: Final = text(
    """
    INSERT INTO assistant_steps (
        org_id, message_id, position, tool, arguments, display_arguments, from_time, to_time,
        state, failure
    ) VALUES (
        :org_id, :message_id, :position, :tool, CAST(:arguments AS jsonb), :display,
        :from_time, :to_time, :state, :failure
    )
    RETURNING id
    """
)
_FINISH_STEP: Final = text(
    """
    UPDATE assistant_steps
    SET state = :state, row_count = :row_count, was_cut = :was_cut, failure = :failure,
        finished_at = :finished_at
    WHERE org_id = :org_id AND id = :id AND state = 'running'
    """
)
_READ_LOG: Final = text(
    """
    INSERT INTO assistant_read_log (
        org_id, project_id, user_id, tool, from_time, to_time, row_count, provider_label
    ) VALUES (
        :org_id, :project_id, :user_id, :tool, :from_time, :to_time, :row_count, :provider
    )
    """
)
_COUNT: Final = text(
    """
    INSERT INTO assistant_monthly_counts (org_id, month, kind, count)
    VALUES (:org_id, :month, :kind, 1)
    ON CONFLICT (org_id, month, kind)
    DO UPDATE SET count = assistant_monthly_counts.count + 1
    """
)
_FINISH: Final = text(
    """
    SELECT assistant_finish_message(
        :id, :state, CAST(:answer AS jsonb), :failure, :rejection, :abstain, :remainder
    )
    """
)


@dataclass(frozen=True, slots=True)
class Question:
    org_id: str
    id: str
    project_id: str
    user_id: str
    question: str
    context: dict[str, Any]
    history: list[dict[str, Any]]
    data_level: str
    sealed_ticket: bytes | None
    call_seconds: int
    deadline_at: datetime


@dataclass(frozen=True, slots=True)
class Scope:
    project_name: str
    project_zone: str
    mask_emails: bool
    # How long the project keeps raw telemetry and its rollups, so an aged-out range is
    # decided in code, on each tool's own clock (design, *The aged-out check*).
    raw_retention_days: int
    derived_retention_days: int


@dataclass(frozen=True, slots=True)
class ChatConnection:
    id: str
    wire_format: str
    base_url: str
    model: str
    region_label: str | None
    key_id: str | None
    sealed_key: bytes | None
    call_seconds: int
    monthly_token_limit: int

    @property
    def provider_label(self) -> str:
        return provider_label(self.wire_format, self.base_url)


@dataclass(frozen=True, slots=True)
class StepEnd:
    state: StepState
    row_count: int | None = None
    was_cut: bool = False
    failure: str | None = None


def _jsonable(value: object) -> dict[str, Any] | list[Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, dict | list):
        return value
    return {}


@dataclass(frozen=True, slots=True)
class PostgresQuestionStore:
    engine: AsyncEngine

    async def start(self, *, org_id: str, message_id: str) -> Question | None:
        """Take a queued question; None when Stop, expiry or the sweep came first."""
        async with self.engine.begin() as db:
            row = (await db.execute(_START, {"org_id": org_id, "id": message_id})).one_or_none()
        if row is None:
            return None
        history = _jsonable(row.history)
        context = _jsonable(row.context)
        return Question(
            org_id=str(row.org_id),
            id=str(row.id),
            project_id=str(row.project_id),
            user_id=str(row.user_id),
            question=row.question,
            context=context if isinstance(context, dict) else {},
            history=list(history) if isinstance(history, list) else [],
            data_level=row.data_level,
            sealed_ticket=None if row.sealed_ticket is None else bytes(row.sealed_ticket),
            call_seconds=row.call_seconds,
            deadline_at=row.deadline_at,
        )

    async def scope(self, question: Question) -> Scope | None:
        async with self.engine.connect() as db:
            row = (
                await db.execute(
                    _SCOPE, {"org_id": question.org_id, "project_id": question.project_id}
                )
            ).one_or_none()
        if row is None:
            return None
        return Scope(
            project_name=row.name,
            project_zone=row.timezone,
            mask_emails=row.mask_emails,
            raw_retention_days=row.raw_retention_days,
            derived_retention_days=row.derived_retention_days,
        )

    async def connection(self, question: Question) -> ChatConnection | None:
        """The connection, when AI is still on with an owner's consent to it (F3's rule)."""
        async with self.engine.connect() as db:
            row = (await db.execute(_CONNECTION, {"org_id": question.org_id})).one_or_none()
        if (
            row is None
            or not row.enabled
            or row.monthly_token_limit is None
            or row.consented_base_url != row.base_url
            or row.consented_schema != SCHEMA_VERSION
            or row.consented_fingerprint != _fingerprint(row)
        ):
            return None
        return ChatConnection(
            id=str(row.id),
            wire_format=row.wire_format,
            base_url=row.base_url,
            model=row.model,
            region_label=row.region_label,
            key_id=None if row.key_id is None else str(row.key_id),
            sealed_key=None if row.sealed_key is None else bytes(row.sealed_key),
            call_seconds=row.call_seconds,
            monthly_token_limit=row.monthly_token_limit,
        )

    async def still_allowed(self, question: Question) -> FailureReason | None:
        """Rechecked before every provider call (design, *The loop*; AC60)."""
        connection = await self.connection(question)
        if connection is None:
            return "ai_off"
        bound = {"org_id": question.org_id, "project_id": question.project_id}
        async with self.engine.connect() as db:
            settings = (await db.execute(_SETTINGS, bound)).one_or_none()
            opted_out = (await db.execute(_OPTED_OUT, bound)).first() is not None
            live = (await db.execute(_LIVE, bound)).first() is not None
        if not live:
            return "ticket_refused"
        if settings is None or not settings.enabled:
            return "chat_off"
        if (
            settings.consented_fingerprint != _fingerprint(connection)
            or settings.consented_disclosure_version != CHAT_DISCLOSURE_VERSION
            or settings.consented_data_level != settings.data_level
            or settings.data_level != question.data_level
        ):
            return "consent_changed"
        if opted_out:
            return "opted_out"
        return None

    async def reserve(self, question: Question, *, tokens: int, limit: int, now: datetime) -> bool:
        """Hold `tokens` of the month for the question, in one transaction, or refuse."""
        month = month_of(now)
        bound = {"org_id": question.org_id, "month": month}
        async with self.engine.begin() as db:
            if not await reserve_month(
                db,
                org_id=question.org_id,
                month=month,
                reserve=tokens,
                ceiling=chat_ceiling(limit),
            ):
                return False
            kept = (await db.execute(_HOLD, bound | {"id": question.id, "reserve": tokens})).first()
            if kept is None:
                # Stopped meanwhile: nothing may stay reserved for it.
                await db.rollback()
                return False
        return True

    async def is_running(self, question: Question) -> bool:
        async with self.engine.connect() as db:
            state = (
                await db.execute(_STATE, {"org_id": question.org_id, "id": question.id})
            ).scalar_one_or_none()
        return state == "running"

    async def set_phase(self, question: Question, phase: Phase) -> bool:
        async with self.engine.begin() as db:
            moved = await db.execute(
                _PHASE, {"org_id": question.org_id, "id": question.id, "phase": phase}
            )
            return moved.first() is not None

    async def start_run(
        self, question: Question, connection: ChatConnection, *, prompt_version: str
    ) -> str:
        """A call's row, committed before any byte is sent; chat's hold 0 (design, *Settling*)."""
        async with self.engine.begin() as db:
            run_id: uuid.UUID = (
                await db.execute(
                    _START_RUN,
                    {
                        "org_id": question.org_id,
                        "project_id": question.project_id,
                        "requested_by": question.user_id,
                        "subject_id": question.id,
                        "wire_format": connection.wire_format,
                        "model": connection.model,
                        "region_label": connection.region_label,
                        "provider_host": provider_host(connection.base_url),
                        "prompt_version": prompt_version,
                        "fields_sent": json.dumps([]),
                    },
                )
            ).scalar_one()
        return str(run_id)

    async def finish_run(
        self,
        question: Question,
        run_id: str,
        *,
        outcome: str,
        input_tokens: int | None,
        output_tokens: int | None,
        estimated: bool,
        at: datetime,
    ) -> None:
        async with self.engine.begin() as db:
            await db.execute(
                _FINISH_RUN,
                {
                    "org_id": question.org_id,
                    "run_id": run_id,
                    "outcome": outcome,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "tokens_estimated": estimated,
                    "finished_at": at,
                },
            )

    async def embedding(self, question: Question, *, keys: ConfiguredKeys) -> Ready | None:
        """Where a search phrase may be embedded now, or None: no `vector` on this install,
        or the setting not ready (Step 6). Read again for each phrase (FR25)."""
        if not await knowledge.vector_granted(self.engine):
            return None
        loaded = await knowledge.load(self.engine, org_id=question.org_id, keys=keys)
        return knowledge.ready(loaded, question.org_id)

    async def start_embed_run(
        self, question: Question, target: EmbeddingTarget, *, request_sha256: str
    ) -> str:
        """A phrase's embedding call's row, committed before any byte is sent."""
        async with self.engine.begin() as db:
            run_id: uuid.UUID = (
                await db.execute(
                    _START_EMBED_RUN,
                    {
                        "org_id": question.org_id,
                        "project_id": question.project_id,
                        "requested_by": question.user_id,
                        "subject_id": question.id,
                        "wire_format": target.wire_format,
                        "model": target.model,
                        "provider_host": provider_host(target.base_url),
                        "prompt_version": knowledge.EMBED_VERSION,
                        "request_sha256": request_sha256,
                        "fields_sent": json.dumps([]),
                    },
                )
            ).scalar_one()
        return str(run_id)

    async def set_query_embedding(
        self, question: Question, step_id: str, *, vector: tuple[float, ...], model: str
    ) -> bool:
        async with self.engine.begin() as db:
            stored = (
                await db.execute(
                    _QUERY_EMBEDDING,
                    {
                        "org_id": question.org_id,
                        "id": step_id,
                        "vector": vector_literal(vector),
                        "model": model,
                    },
                )
            ).first()
        return stored is not None

    async def record_spend(self, question: Question, *, tokens: int, estimated: bool) -> None:
        async with self.engine.begin() as db:
            await db.execute(_SPEND, {"id": question.id, "spent": tokens, "estimated": estimated})

    async def add_step(
        self,
        question: Question,
        *,
        position: int,
        tool: str,
        arguments: dict[str, Any],
        display: str | None,
        from_time: datetime | None,
        to_time: datetime | None,
        state: StepState = "running",
        failure: str | None = None,
    ) -> str | None:
        """A step row; None when the question is no longer visible (a normal stop)."""
        async with self.engine.begin() as db:
            step_id = (
                await db.execute(
                    _ADD_STEP,
                    {
                        "org_id": question.org_id,
                        "message_id": question.id,
                        "position": position,
                        "tool": tool,
                        "arguments": json.dumps(arguments),
                        "display": display,
                        "from_time": from_time,
                        "to_time": to_time,
                        "state": state,
                        "failure": failure,
                    },
                )
            ).scalar_one_or_none()
        return None if step_id is None else str(step_id)

    async def finish_step(
        self, question: Question, step_id: str, end: StepEnd, *, at: datetime
    ) -> None:
        async with self.engine.begin() as db:
            await db.execute(
                _FINISH_STEP,
                {
                    "org_id": question.org_id,
                    "id": step_id,
                    "state": end.state,
                    "row_count": end.row_count,
                    "was_cut": end.was_cut,
                    "failure": end.failure,
                    "finished_at": at,
                },
            )

    async def log_read(
        self,
        question: Question,
        *,
        tool: str,
        from_time: datetime | None,
        to_time: datetime | None,
        row_count: int | None,
        provider: str,
    ) -> None:
        """The read record: no filter values, so a question's words cannot get in."""
        async with self.engine.begin() as db:
            await db.execute(
                _READ_LOG,
                {
                    "org_id": question.org_id,
                    "project_id": question.project_id,
                    "user_id": question.user_id,
                    "tool": tool,
                    "from_time": from_time,
                    "to_time": to_time,
                    "row_count": row_count,
                    "provider": provider,
                },
            )

    async def count(self, question: Question, *, kind: str, now: datetime) -> None:
        async with self.engine.begin() as db:
            await db.execute(
                _COUNT, {"org_id": question.org_id, "month": month_of(now), "kind": kind}
            )

    async def finish(
        self,
        question: Question,
        *,
        state: str,
        answer: dict[str, Any] | None = None,
        failure: FailureReason | None = None,
        rejection: str | None = None,
        abstain: str | None = None,
        remainder: Remainder = "return",
    ) -> bool:
        """The one final write. True when this call ended the question; False when it was
        already over (it still settles what the question held)."""
        async with self.engine.begin() as db:
            ended = (
                await db.execute(
                    _FINISH,
                    {
                        "id": question.id,
                        "state": state,
                        "answer": None if answer is None else json.dumps(answer),
                        "failure": failure,
                        "rejection": rejection,
                        "abstain": abstain,
                        "remainder": remainder,
                    },
                )
            ).scalar_one()
        return bool(ended)


def _fingerprint(row: Any) -> str:
    return consent_fingerprint(
        wire_format=row.wire_format,
        base_url=row.base_url,
        region=row.region_label,
        key_id=None if row.key_id is None else str(row.key_id),
    )
