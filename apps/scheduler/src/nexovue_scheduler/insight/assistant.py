"""F4: chat's loop, run by `scheduler-insight` alone (design, section 3; plan T2.9).

`answer_question(message_id, org_id)` takes a queued question, reserves its share of the
month, then up to `MAX_STEPS` times asks the provider for a tool call or an answer. Each
tool call is checked against the catalog and read **through the API, as the asking person,
with the question's ticket** (`api_client.py`); results are projected, masked, labelled,
fenced and cut, and never stored. An answer is checked by `assistant_answer.py` before
anything is written. Every end goes through exactly one `finish`.

**One clock** (design, *One clock*): the API set `deadline_at`; a call starts only if the
time left covers two of the connection's `call_seconds`, one kept for a final call with no
tools, because a soft limit cannot interrupt a call blocked in libcurl. **Money:** a call
starts only if its estimate (bytes ÷ 3 plus the output limit) fits what the question still
holds; actual tokens move through `assistant_record_spend`, an estimate when a call may have
reached the provider and returned nothing.

Before every provider call it rechecks that AI and chat are on, the project not opted out
and the consent standing; the API rechecks the same on every read the ticket makes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Final, Protocol, cast

from nexovue_platform.ai.chat import ChatFormat, ChatProvider, ChatRequest, ChatResponse
from nexovue_platform.ai.chat_formats import chat_format
from nexovue_platform.ai.connection import (
    AI_CONNECTION_PURPOSE,
    ConnectionRefused,
    check_connection,
    check_key,
    key_owner,
)
from nexovue_platform.ai.contract import estimate_tokens
from nexovue_platform.ai.embed import EmbeddingTarget
from nexovue_platform.assistant_masks import mask
from nexovue_platform.assistant_records import (
    ANSWER_QUESTION_TASK,
    KEPT_SINCE_PREFIX,
    MAX_OUTPUT_TOKENS,
    MAX_STEPS,
    QUESTION_RESERVATION_TOKENS,
    TICKET_PURPOSE,
    FailureReason,
    Phase,
    Remainder,
    StepState,
)
from nexovue_platform.assistant_tools import (
    KNOWLEDGE_TOOL,
    TOOLS_BY_NAME,
    ChatTool,
    DataLevel,
    tools_at,
)
from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, SealError, keyring_from
from nexovue_platform.knowledge import SEARCH_NOTE_PREFIX, STEP_QUERY
from nexovue_platform.telemetry.logging import get_logger

from nexovue_scheduler.insight import transport
from nexovue_scheduler.insight.api_client import ApiClient, CurlApiClient
from nexovue_scheduler.insight.assistant_answer import (
    Abstained,
    AnswerRejected,
    CheckedAnswer,
    StepRead,
    check_answer,
)
from nexovue_scheduler.insight.assistant_prompt import (
    PROMPT_VERSION,
    StepResult,
    Turn,
    build_request,
)
from nexovue_scheduler.insight.assistant_reads import (
    ArgumentsRefused,
    ValidCall,
    check_arguments,
    kept_since,
    project,
    within_retention,
)
from nexovue_scheduler.insight.assistant_store import (
    ChatConnection,
    PostgresQuestionStore,
    Question,
    Scope,
    StepEnd,
)
from nexovue_scheduler.insight.knowledge import (
    EmbedderFor,
    EmbedFailed,
    Ready,
    transport_embedder,
)
from nexovue_scheduler.jobs.registry import SoftTimeLimit, task
from nexovue_scheduler.probes.addresses import PrivateEndpoints
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# Two calls beyond the step limit: one retry of a rejected answer, and the final call.
MAX_CALLS: Final = MAX_STEPS + 2
# A ticket refused: every refusal of `tickets.valid_for` is one of these. A 404 is not one:
# an item tool asked for a trace, finding or group the project does not hold (Step 3).
_REFUSED_STATUSES: Final = frozenset({401, 403})
_NOT_FOUND: Final = 404
PROJECT_QUERY: Final = "project_id"
# The knowledge search's phrase, as its catalog entry sends it: what the worker embeds.
SEARCH_QUERY: Final = TOOLS_BY_NAME[KNOWLEDGE_TOOL].arguments["q"].query


class ProviderFailed(Exception):  # noqa: N818 — what the transport said, as a closed word
    def __init__(self, reason: FailureReason, *, reached: bool) -> None:
        super().__init__(reason)
        self.reason: FailureReason = reason
        self.reached = reached


class QuestionStore(Protocol):
    """`PostgresQuestionStore`'s shape, so the loop's tests run with no database."""

    async def start(self, *, org_id: str, message_id: str) -> Question | None: ...
    async def scope(self, question: Question) -> Scope | None: ...
    async def connection(self, question: Question) -> ChatConnection | None: ...
    async def still_allowed(self, question: Question) -> FailureReason | None: ...
    async def reserve(
        self, question: Question, *, tokens: int, limit: int, now: datetime
    ) -> bool: ...
    async def is_running(self, question: Question) -> bool: ...
    async def set_phase(self, question: Question, phase: Phase) -> bool: ...
    async def start_run(
        self, question: Question, connection: ChatConnection, *, prompt_version: str
    ) -> str: ...
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
    ) -> None: ...
    async def record_spend(self, question: Question, *, tokens: int, estimated: bool) -> None: ...
    async def embedding(self, question: Question, *, keys: ConfiguredKeys) -> Ready | None: ...
    async def start_embed_run(
        self, question: Question, target: EmbeddingTarget, *, request_sha256: str
    ) -> str: ...
    async def set_query_embedding(
        self, question: Question, step_id: str, *, vector: tuple[float, ...], model: str
    ) -> bool: ...
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
    ) -> str | None: ...
    async def finish_step(
        self, question: Question, step_id: str, end: StepEnd, *, at: datetime
    ) -> None: ...
    async def log_read(
        self,
        question: Question,
        *,
        tool: str,
        from_time: datetime | None,
        to_time: datetime | None,
        row_count: int | None,
        provider: str,
    ) -> None: ...
    async def count(self, question: Question, *, kind: str, now: datetime) -> None: ...
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
    ) -> bool: ...


ProviderFor = Callable[[ChatConnection, str | None], ChatProvider]


@dataclass(frozen=True, slots=True)
class TransportProvider:
    """The real provider: F3's guarded transport, with a chat format's bytes."""

    fmt: ChatFormat
    connection: ChatConnection
    key: str | None
    endpoints: PrivateEndpoints

    def complete(self, request: ChatRequest) -> ChatResponse:
        call = self.fmt.build(
            request,
            model=self.connection.model,
            base_url=self.connection.base_url,
            key=self.key,
        )
        answer = transport.send(
            call,
            timeout_seconds=self.connection.call_seconds,
            private_endpoints=self.endpoints,
        )
        if answer.failure is not None and answer.status is None:
            reason: FailureReason = "invalid" if answer.failure == "too_large" else answer.failure
            raise ProviderFailed(reason, reached=answer.reached)
        if answer.failure == "too_large":
            return ChatResponse("invalid").with_counts(request_body=call.body)
        return self.fmt.parse(answer.status or 0, answer.body).with_counts(request_body=call.body)


def transport_provider(endpoints: PrivateEndpoints) -> ProviderFor:
    def build(connection: ChatConnection, key: str | None) -> ChatProvider:
        return TransportProvider(chat_format(connection.wire_format), connection, key, endpoints)

    return build


@dataclass(frozen=True, slots=True)
class LoopParts:
    store: QuestionStore
    client: ApiClient
    provider_for: ProviderFor
    keys: ConfiguredKeys
    clock: Callable[[], datetime]
    # Step 6: how a search phrase is embedded; None searches by keyword alone.
    embedder_for: EmbedderFor | None = None


@dataclass
class _Run:
    """One question's working state, held in memory only: tool results are never stored."""

    question: Question
    scope: Scope
    connection: ChatConnection
    provider: ChatProvider
    ticket: str
    level: DataLevel
    tools: tuple[ChatTool, ...]
    held: int
    position: int = 0
    results: list[StepResult] = field(default_factory=list)
    reads: dict[str, StepRead] = field(default_factory=dict)
    result_texts: list[str] = field(default_factory=list)
    told: list[str] = field(default_factory=list)
    sent_back: bool = False


class _Ended(Exception):  # noqa: N818 — control flow: the question has had its one finish
    pass


def _open(sealed: bytes | None, keys: ConfiguredKeys, *, purpose: str, owner: str) -> str | None:
    if sealed is None:
        return None
    box = SealedBox(keyring_from(keys.current, keys.previous), purpose=purpose)
    return box.open(sealed, owner=owner)


def _history(question: Question) -> list[Turn]:
    return [
        Turn(question=str(turn.get("question", "")), answer=str(turn.get("answer", "")))
        for turn in question.history
        if isinstance(turn, dict)
    ]


async def answer(org_id: str, message_id: str, parts: LoopParts) -> str | None:
    """Answer one question; the state it ended in, or None when it was not ours to run."""
    store = parts.store
    question = await store.start(org_id=org_id, message_id=message_id)
    if question is None:
        logger.info("a question was no longer waiting", message_id=message_id, org_id=org_id)
        return None
    logger.info("a question started", message_id=message_id, org_id=org_id)
    try:
        return await _answer(question, parts)
    except _Ended as ended:
        return str(ended)
    except SoftTimeLimit:
        await _end(parts, question, "failed", failure="soft_limit", remainder="charge")
        return "failed"
    except BaseException:
        # A call may have been billed: overstating is the safe side (F3 BR13).
        await _end(parts, question, "failed", failure="died", remainder="charge")
        raise


async def _end(
    parts: LoopParts,
    question: Question,
    state: str,
    *,
    answer: dict[str, Any] | None = None,
    failure: FailureReason | None = None,
    rejection: str | None = None,
    abstain: str | None = None,
    remainder: Remainder = "return",
) -> str:
    ended = await parts.store.finish(
        question,
        state=state,
        answer=answer,
        failure=failure,
        rejection=rejection,
        abstain=abstain,
        remainder=remainder,
    )
    now = parts.clock()
    if ended:
        await parts.store.count(question, kind=state, now=now)
        if rejection is not None:
            await parts.store.count(question, kind=f"rejected_{rejection}", now=now)
    logger.info(
        "a question ended",
        message_id=question.id,
        org_id=question.org_id,
        outcome=state if ended else "already_over",
        reason=failure or rejection or abstain,
    )
    return state


async def _answer(question: Question, parts: LoopParts) -> str:
    store = parts.store
    connection = await store.connection(question)
    if connection is None:
        return await _end(parts, question, "refused", failure="ai_off")
    if refused := await store.still_allowed(question):
        return await _end(parts, question, "refused", failure=refused)
    scope = await store.scope(question)
    if scope is None:
        return await _end(parts, question, "refused", failure="ticket_refused")
    try:
        check_connection(
            wire_format=connection.wire_format,
            base_url=connection.base_url,
            model=connection.model,
            region=connection.region_label,
        )
        ticket = _open(
            question.sealed_ticket, parts.keys, purpose=TICKET_PURPOSE, owner=question.id
        )
        key = _open(
            connection.sealed_key,
            parts.keys,
            purpose=AI_CONNECTION_PURPOSE,
            owner=key_owner(question.org_id, connection.id, connection.base_url),
        )
        if key is not None:
            check_key(key)
    except ConnectionRefused:
        return await _end(parts, question, "failed", failure="not_allowed")
    except (SealError, ValueError):
        return await _end(parts, question, "failed", failure="key_unreadable")
    if ticket is None:
        return await _end(parts, question, "failed", failure="key_unreadable")
    if not await store.reserve(
        question,
        tokens=QUESTION_RESERVATION_TOKENS,
        limit=connection.monthly_token_limit,
        now=parts.clock(),
    ):
        # Nothing was reserved, so nothing returns: a refusal, or Stop came first.
        if await store.is_running(question):
            return await _end(parts, question, "over_limit")
        return await _end(parts, question, "stopped")
    level: DataLevel = "facts_and_text" if question.data_level == "facts_and_text" else "facts"
    run = _Run(
        question=question,
        scope=scope,
        connection=connection,
        provider=parts.provider_for(connection, key),
        ticket=ticket,
        level=level,
        tools=tools_at(level),
        held=QUESTION_RESERVATION_TOKENS,
    )
    for _ in range(MAX_CALLS):
        outcome = await _one_call(run, parts)
        if outcome is not None:
            return outcome
    return await _partial(run, parts)


def _seconds_left(run: _Run, parts: LoopParts) -> float:
    return (run.question.deadline_at - parts.clock()).total_seconds()


async def _partial(run: _Run, parts: LoopParts) -> str:
    """Out of steps, time or budget with no answer: the steps stand on their own."""
    return await _end(parts, run.question, "partial")


async def _one_call(run: _Run, parts: LoopParts) -> str | None:
    """One provider call and what follows it; the final state, or None to call again."""
    store = parts.store
    question = run.question
    if refused := await store.still_allowed(question):
        return await _end(parts, question, "refused", failure=refused)
    if not await store.is_running(question):
        # Stopped, or marked by the API or the sweep: settle what it holds, keep its state.
        return await _end(parts, question, "stopped")
    call_seconds = run.connection.call_seconds
    final = run.position >= MAX_STEPS or _seconds_left(run, parts) < 2 * call_seconds
    request = _request(run, parts, final=final)
    estimate = _estimate(request)
    # Like the clock, the budget keeps room for the answer: a step must leave enough for a
    # last call at least as large as this one (live test D2, 2026-11-27).
    if not final and 2 * estimate > run.held:
        final = True
        request = _request(run, parts, final=True)
        estimate = _estimate(request)
    if _seconds_left(run, parts) < call_seconds or estimate > run.held:
        return await _partial(run, parts)
    if not await store.set_phase(question, "asking"):
        return await _end(parts, question, "stopped")
    response = await _call(run, parts, request, estimate)
    if response.outcome != "ok":
        return await _end(parts, question, "failed", failure=cast(FailureReason, response.outcome))
    if response.tool_call is not None and not final:
        await _step(run, parts, response.tool_call.name, response.tool_call.arguments)
        return None
    return await _settle_answer(run, parts, response.text, final=final)


def _request(run: _Run, parts: LoopParts, *, final: bool) -> ChatRequest:
    return build_request(
        question=run.question.question,
        context=run.question.context,
        history=_history(run.question),
        results=run.results,
        tools=run.tools,
        now=parts.clock(),
        project_name=run.scope.project_name,
        project_zone=run.scope.project_zone,
        final=final,
        told=run.told,
    )


def _estimate(request: ChatRequest) -> int:
    sent = request.instructions + request.user_text()
    sent += json.dumps([tool.parameters for tool in request.tools], default=str)
    return estimate_tokens(len(sent.encode())) + MAX_OUTPUT_TOKENS


async def _call(run: _Run, parts: LoopParts, request: ChatRequest, estimate: int) -> ChatResponse:
    """One metered call: a run row first, then the call, then its tokens moved."""
    store = parts.store
    question = run.question
    run_id = await store.start_run(question, run.connection, prompt_version=PROMPT_VERSION)
    try:
        response = run.provider.complete(request)
    except ProviderFailed as failed:
        spent = estimate if failed.reached else 0
        await store.finish_run(
            question,
            run_id,
            outcome="unavailable",
            input_tokens=spent or None,
            output_tokens=0 if failed.reached else None,
            estimated=failed.reached,
            at=parts.clock(),
        )
        if spent:
            await store.record_spend(question, tokens=spent, estimated=True)
        raise _Ended(await _end(parts, question, "failed", failure=failed.reason)) from failed
    await store.finish_run(
        question,
        run_id,
        outcome="done" if response.outcome == "ok" else response.outcome,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        estimated=response.tokens_estimated,
        at=parts.clock(),
    )
    await store.record_spend(question, tokens=response.tokens, estimated=response.tokens_estimated)
    run.held = max(0, run.held - response.tokens)
    return response


def _summary(tool: str, end: StepEnd) -> str:
    if end.state == "read":
        cut = ", cut" if end.was_cut else ""
        return f"{tool} read {end.row_count} rows{cut}"
    return f"{tool} {end.state}: {end.failure}"


async def _step(
    run: _Run, parts: LoopParts, name: str, arguments: Mapping[str, Any] | None
) -> None:
    """A tool call: checked, recorded, read through the API as the person, projected."""
    store = parts.store
    question = run.question
    run.position += 1
    key = f"s{run.position}"
    tool = TOOLS_BY_NAME.get(name)
    if tool is None or tool not in run.tools:
        await store.add_step(
            question,
            position=run.position,
            tool="unknown",
            arguments={},
            display=None,
            from_time=None,
            to_time=None,
            state="refused",
            failure="unknown_tool",
        )
        run.told.append(f"Step {key} was refused: there is no tool by that name here.")
        run.results.append(StepResult(key, "unknown", f"{key}: refused, no such tool", None))
        return
    try:
        call = check_arguments(tool, arguments, level=run.level, now=parts.clock())
    except ArgumentsRefused as refused:
        await store.add_step(
            question,
            position=run.position,
            tool=tool.name,
            arguments={},
            display=None,
            from_time=None,
            to_time=None,
            state="refused",
            failure=refused.kind,
        )
        run.told.append(f"Step {key} ({tool.name}) was refused: {refused}")
        run.results.append(StepResult(key, tool.name, f"{key}: {tool.name} refused", None))
        return
    now = parts.clock()
    kept = kept_since(
        tool,
        raw_days=run.scope.raw_retention_days,
        derived_days=run.scope.derived_retention_days,
        now=now,
    )
    kept_note = None if kept is None else f"{KEPT_SINCE_PREFIX}{kept.date().isoformat()}"
    narrowed = within_retention(call, kept)
    if narrowed is None:
        # Decided in code from the tool's own clock: no read, and no model involved (AC24).
        await _aged_out(run, parts, tool, call, key, kept_note)
        return
    step_id = await store.add_step(
        question,
        position=run.position,
        tool=tool.name,
        arguments=narrowed.stored,
        display=narrowed.display,
        from_time=narrowed.from_time,
        to_time=narrowed.to_time,
    )
    if step_id is None or not await store.set_phase(question, "reading"):
        raise _Ended(await _end(parts, question, "stopped"))
    if narrowed is not call:
        run.told.append(
            f"Step {key} ({tool.name}) read from {narrowed.from_time} only: the data before "
            "it is no longer kept."
        )
    # The question's own project, written by nexovue: the catalog refuses one from the model,
    # and every route reads only the project its ticket was minted for. A route taking no
    # `project_id` compares the ticket's scope with the row it loads.
    query = (
        {**narrowed.query, PROJECT_QUERY: question.project_id}
        if tool.project_in_query
        else dict(narrowed.query)
    )
    if tool.name == KNOWLEDGE_TOOL and await _embed_phrase(
        run, parts, step_id, narrowed.query.get(SEARCH_QUERY, "")
    ):
        # The vector stays in Postgres; the route reads it from the step (design, section 6).
        query[STEP_QUERY] = step_id
    answer = parts.client.get(tool, query, ticket=run.ticket, path=narrowed.path)
    if answer.status in _REFUSED_STATUSES:
        await store.finish_step(
            question, step_id, StepEnd("refused", failure="refused"), at=parts.clock()
        )
        # The person or the scope changed under the question: no further model call.
        raise _Ended(await _end(parts, question, "refused", failure="ticket_refused"))
    end, projected = _read_end(tool, answer.status, answer.body, answer.failure, run.scope)
    if narrowed is not call and end.state == "read":
        end = replace(end, failure=kept_note)
    if tool.name == KNOWLEDGE_TOOL and end.state == "read":
        # How it searched, for the panel's "keyword only" (FR27, AC41).
        end = replace(end, failure=f"{SEARCH_NOTE_PREFIX}{_search_kind(answer.body)}")
    await store.finish_step(question, step_id, end, at=parts.clock())
    await store.log_read(
        question,
        tool=tool.name,
        from_time=narrowed.from_time,
        to_time=narrowed.to_time,
        row_count=end.row_count,
        provider=run.connection.provider_label,
    )
    summary = f"{key}: {_summary(tool.name, end)}"
    if projected is None:
        run.told.append(f"Step {key} ({tool.name}) failed; answer with what you have.")
        run.results.append(StepResult(key, tool.name, summary, None))
        return
    run.reads[key] = StepRead(key, tool, projected.rows)
    run.result_texts.append(projected.text)
    run.results.append(StepResult(key, tool.name, summary, projected.text))


async def _embed_phrase(run: _Run, parts: LoopParts, step_id: str, phrase: str) -> bool:
    """The search phrase's own vector on its step, metered from the question's reservation;
    False, and keyword search alone, when it cannot be had (design, section 6). A failed call
    never ends the question."""
    store = parts.store
    question = run.question
    if parts.embedder_for is None or not phrase:
        return False
    allowed = await store.embedding(question, keys=parts.keys)
    if allowed is None:
        return False
    embedder = parts.embedder_for(allowed.target, allowed.key, allowed.call_seconds)
    texts = (mask(phrase, mask_emails=run.scope.mask_emails),)
    try:
        body = embedder.body_of(texts)
    except EmbedFailed:
        return False
    estimate = estimate_tokens(len(body))
    if estimate > run.held:
        return False
    run_id = await store.start_embed_run(
        question, allowed.target, request_sha256=hashlib.sha256(body).hexdigest()
    )
    try:
        response = embedder.embed(texts)
    except EmbedFailed as failed:
        await store.finish_run(
            question,
            run_id,
            outcome="unavailable",
            input_tokens=estimate if failed.reached else None,
            output_tokens=0 if failed.reached else None,
            estimated=failed.reached,
            at=parts.clock(),
        )
        if failed.reached:
            await store.record_spend(question, tokens=estimate, estimated=True)
            run.held = max(0, run.held - estimate)
        return False
    usable = response.outcome == "ok" and len(response.vectors) == 1
    await store.finish_run(
        question,
        run_id,
        outcome="done" if usable else ("invalid" if response.outcome == "ok" else response.outcome),
        input_tokens=response.input_tokens,
        output_tokens=0,
        estimated=response.tokens_estimated,
        at=parts.clock(),
    )
    spent = response.input_tokens or 0
    await store.record_spend(question, tokens=spent, estimated=response.tokens_estimated)
    run.held = max(0, run.held - spent)
    if not usable:
        return False
    return await store.set_query_embedding(
        question, step_id, vector=response.vectors[0], model=allowed.target.model
    )


def _search_kind(body: bytes) -> str:
    """The route's own word for how it searched, from a closed list; keyword otherwise."""
    try:
        said = json.loads(body).get("search")
    except (ValueError, AttributeError):
        return "keyword"
    return "hybrid" if said == "hybrid" else "keyword"


async def _aged_out(
    run: _Run, parts: LoopParts, tool: ChatTool, call: ValidCall, key: str, note: str | None
) -> None:
    store = parts.store
    question = run.question
    step_id = await store.add_step(
        question,
        position=run.position,
        tool=tool.name,
        arguments=call.stored,
        display=call.display,
        from_time=call.from_time,
        to_time=call.to_time,
    )
    if step_id is None:
        raise _Ended(await _end(parts, question, "stopped"))
    await store.finish_step(question, step_id, StepEnd("aged_out", failure=note), at=parts.clock())
    kept = (note or "").removeprefix(KEPT_SINCE_PREFIX)
    run.told.append(
        f"Step {key} ({tool.name}) read nothing: the data before {kept} is no longer kept. "
        "Say so; it is not evidence that nothing happened."
    )
    run.results.append(StepResult(key, tool.name, f"{key}: {tool.name} aged out", None))


def _read_end(
    tool: ChatTool, status: int | None, body: bytes, failure: str | None, scope: Scope
) -> tuple[StepEnd, Any]:
    if status != 200:
        word = failure or (
            "limited" if status == 429 else "not_found" if status == _NOT_FOUND else "unavailable"
        )
        return StepEnd("failed", failure=word), None
    try:
        projected = project(tool, body, mask_emails=scope.mask_emails)
    except ValueError:
        return StepEnd("failed", failure="invalid"), None
    return StepEnd("read", row_count=len(projected.rows), was_cut=projected.was_cut), projected


async def _settle_answer(
    run: _Run, parts: LoopParts, text: str | None, *, final: bool
) -> str | None:
    store = parts.store
    question = run.question
    if not await store.set_phase(question, "checking"):
        return await _end(parts, question, "stopped")
    try:
        checked = check_answer(text, run.reads, results=run.result_texts)
    except AnswerRejected as rejected:
        if rejected.reason == "citations" or run.sent_back or final:
            return await _end(parts, question, "withheld", rejection=rejected.reason)
        run.sent_back = True
        run.told.append(f"Your answer was sent back: {rejected} Answer again.")
        return None
    if isinstance(checked, Abstained):
        return await _end(parts, question, "abstained", abstain=checked.reason)
    return await _end(
        parts, question, "partial" if final else "answered", answer=_stored(checked, run)
    )


def _stored(checked: CheckedAnswer, run: _Run) -> dict[str, Any]:
    stored = checked.stored()
    stored["steps_read"] = sorted(run.reads)
    return stored


@task(ANSWER_QUESTION_TASK)
def answer_question(*, message_id: str, org_id: str) -> None:
    settings = get_settings()
    with_engine(
        lambda engine: answer(
            org_id,
            message_id,
            LoopParts(
                store=PostgresQuestionStore(engine),
                client=CurlApiClient(settings.internal_api_url),
                provider_for=transport_provider(settings.ai_endpoints),
                keys=settings.ai_secret_keys,
                clock=lambda: datetime.now(UTC),
                embedder_for=transport_embedder(settings.ai_endpoints),
            ),
        )
    )
