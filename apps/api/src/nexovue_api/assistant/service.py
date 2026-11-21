"""Chat's API side (F4 design, *Routes* and *What `POST` must do*; plan T2.6).

The API never calls a provider. It stores a question with its ticket and clocks, and adds
its job, in **one transaction**: check the standing and the question; add to the person's
count for the day, refusing past the limit; mark the person's own stale question
`unavailable`; insert the message under the one-in-flight index; enqueue with a wake. The
reservation is the worker's, so a question that never runs reserves nothing.

Every refusal has its own problem `code`, outside 429, since a 429 becomes a toast that
loses the reset time (design, *Validation and refusals*). A conversation that belongs to
another person reads 404, not 403: its existence is private too.

**The API's final writes go through `assistant_finish_message` with `keep`,** so it may
write a state (Stop, a stale row) but never moves money; the worker or the sweep settles.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from nexovue_platform.ai.chat import provider_label
from nexovue_platform.ai.connection import provider_host
from nexovue_platform.ai.real_calls import formats_with_a_real_call
from nexovue_platform.assistant_records import (
    CHAT_DISCLOSURE_VERSION,
    HISTORY_CHARACTERS,
    HISTORY_TURNS,
    IN_FLIGHT,
    answer_question_request,
    clocks,
)
from nexovue_platform.assistant_records import KEPT_SINCE_PREFIX as KEPT_SINCE
from nexovue_platform.assistant_tools import KNOWLEDGE_TOOL, SEVERITY_FILTER_IDS, TOOLS_BY_NAME
from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.jobs import ENQUEUE, WAKE
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.ai import queries as ai_queries
from nexovue_api.ai.service import Availability as AIAvailability
from nexovue_api.ai.service import consent_fingerprint_of
from nexovue_api.assistant import tickets
from nexovue_api.assistant.schemas import (
    AskContext,
    Availability,
    ChatConsent,
    ChatSettings,
    ConversationSummary,
    ConversationView,
    MessageView,
    PersonCount,
    ReadRow,
    SaveChatSettings,
    StepLink,
    StepView,
)
from nexovue_api.assistant.standing import consent_matches, settings_of, standing_of
from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity, Reader
from nexovue_api.auth.permissions import Action, may
from nexovue_api.data.postgres.models import (
    AssistantConversation,
    AssistantMessage,
    AssistantMonthlyCount,
    AssistantQuestionCount,
    AssistantReadLog,
    AssistantSettings,
    AssistantStep,
    Finding,
    Project,
    User,
)
from nexovue_api.errors.exceptions import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
)
from nexovue_api.findings.queries import derived_retention_days
from nexovue_api.projects import queries as project_queries
from nexovue_api.projects.service import require_readable_project

# The exact sentences of the spec's *Error cases*, each with its own code.
REFUSALS: Final[dict[str, str]] = {
    "ai_off": "AI is off here. Ask an admin to turn it on.",
    "chat_off": "Chat is not on here. Ask an admin to turn this on.",
    "no_consent": "Chat is waiting for an owner to agree to what it sends.",
    "opted_out": "AI is off for this project.",
    "daily_limit": "You have asked today's questions. More tomorrow.",
    "still_answering": "Chat is still answering your last question. Stop it, or wait.",
    "finding_elsewhere": "That finding is not in this project.",
}
NOT_FOUND: Final = "No such conversation."
SUPPORT_VIEW: Final = "Support view cannot read conversations."
CONSENT_MOVED: Final = "What chat would send changed after you read it. Read it again, then agree."
NO_CONNECTION: Final = "Save an AI connection first."
# The spec's *Error cases* sentence for deleting a conversation whose question still runs.
STILL_RUNNING: Final = "Stop this question first."
NOT_FINISHED: Final = "Only a finished answer can be marked wrong."
TITLE_CHARACTERS: Final = 80
PAGE_MESSAGES: Final = 50
AUDIT_TARGET: Final = "assistant_settings"
SETTINGS_CHANGED: Final = "assistant.settings_changed"
DISCLOSURE_AGREED: Final = "assistant.disclosure_agreed"

_FINISH: Final = text(
    "SELECT assistant_finish_message(:id, :state, NULL, NULL, NULL, NULL, 'keep')"
)
_MESSAGE_COLUMNS_DONE: Final = ("answered", "partial")


@dataclass(frozen=True, slots=True)
class Asker:
    org_id: uuid.UUID
    user_id: uuid.UUID
    role: str


def asker_of(identity: Identity) -> Asker:
    if identity.impersonation is not None:
        raise PermissionDeniedError(SUPPORT_VIEW)
    return Asker(identity.org_id, identity.user.id, identity.user.role)


def _today(now: datetime) -> date:
    return now.astimezone(UTC).date()


def _first_of_month(now: datetime) -> date:
    return date(now.year, now.month, 1)


def _pairs(rows: Any) -> dict[Any, Any]:
    """Two-column rows as a mapping. Not `dict(result)`: a result has `keys()`, so `dict`
    would read it as a mapping of column names."""
    return dict(iter(rows))


# ------------------------------------------------------------------ availability


async def availability(
    session: AsyncSession,
    *,
    identity: Identity,
    project_id: uuid.UUID,
    ai: AIAvailability,
    now: datetime,
) -> Availability:
    reader = Reader(org_id=identity.org_id, identity=identity)
    await require_readable_project(session, reader=reader, project_id=project_id)
    standing = await standing_of(
        session, org_id=identity.org_id, project_id=project_id, availability=ai
    )
    may_manage = may(identity.user.role, Action.MANAGE_AI)
    waiting: list[str] = []
    if standing.state == "no_consent":
        owners = await session.scalars(
            select(func.coalesce(User.name, User.email)).where(
                User.org_id == identity.org_id, User.role == "owner", User.removed_at.is_(None)
            )
        )
        waiting = sorted(owners)
    if not standing.ready or standing.connection is None or standing.settings is None:
        return Availability(state=standing.state, waiting_for=waiting, may_manage=may_manage)
    # Nothing to read yet: say so before anyone spends tokens. The ingest meter answers,
    # so a project whose telemetry has since expired still reads as one that reported.
    if project_id not in await project_queries.first_reported_days(session, identity.org_id):
        return Availability(state="no_data", may_manage=may_manage)
    asked = await _asked_today(session, identity.org_id, identity.user.id, now)
    tomorrow = datetime.combine(_today(now) + timedelta(days=1), datetime.min.time(), UTC)
    return Availability(
        state="ready",
        provider_label=provider_label(
            standing.connection.wire_format, standing.connection.base_url
        ),
        tested=standing.connection.wire_format in formats_with_a_real_call(),
        questions_left_today=max(0, standing.settings.daily_question_limit - asked),
        resets_at=tomorrow,
        may_manage=may_manage,
    )


async def _asked_today(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, now: datetime
) -> int:
    asked = await session.scalar(
        select(AssistantQuestionCount.count).where(
            AssistantQuestionCount.org_id == org_id,
            AssistantQuestionCount.user_id == user_id,
            AssistantQuestionCount.day == _today(now),
        )
    )
    return int(asked or 0)


# ------------------------------------------------------------------ asking


async def ask(
    session: AsyncSession,
    *,
    identity: Identity,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    question: str,
    context: AskContext,
    ai: AIAvailability,
    keys: ConfiguredKeys,
    now: datetime,
) -> MessageView:
    """One question, stored with its ticket and job in the caller's transaction."""
    asker = asker_of(identity)
    reader = Reader(org_id=asker.org_id, identity=identity)
    project = await require_readable_project(session, reader=reader, project_id=project_id)
    standing = await standing_of(
        session, org_id=asker.org_id, project_id=project.id, availability=ai
    )
    if not standing.ready or standing.connection is None or standing.settings is None:
        raise ConflictError(REFUSALS[standing.state], code=standing.state)
    if context.finding_id is not None and not await _finding_in(
        session, asker, project.id, context.finding_id
    ):
        raise InvalidRequestError(REFUSALS["finding_elsewhere"], code="finding_elsewhere")
    conversation = await _conversation_for(
        session, asker, project.id, conversation_id, question, now
    )
    await _count_today(session, asker, standing.settings.daily_question_limit, now)
    await _mark_stale(session, asker, now)
    call_seconds = standing.connection.call_seconds
    queue_expires_at, deadline_at, hard_ends_at = clocks(now, call_seconds)
    message_id = uuid.uuid4()
    ticket = tickets.mint()
    message = AssistantMessage(
        id=message_id,
        org_id=asker.org_id,
        project_id=project.id,
        conversation_id=conversation.id,
        user_id=asker.user_id,
        question=question,
        context=context.model_dump(mode="json", by_alias=True, exclude_none=True),
        history=await _history(session, asker.org_id, conversation.id),
        data_level=standing.settings.data_level,
        state="queued",
        phase="waiting",
        ticket_hash=tickets.fingerprint(ticket),
        sealed_ticket=tickets.seal(ticket, message_id=message_id, keys=keys),
        call_seconds=call_seconds,
        queue_expires_at=queue_expires_at,
        deadline_at=deadline_at,
        hard_ends_at=hard_ends_at,
        created_at=now,
    )
    try:
        async with session.begin_nested():
            session.add(message)
            await session.flush()
    except IntegrityError as conflict:
        raise ConflictError(REFUSALS["still_answering"], code="still_answering") from conflict
    conversation.last_message_at = now
    job = answer_question_request(
        message_id=str(message_id), org_id=str(asker.org_id), call_seconds=call_seconds
    )
    await session.execute(text(ENQUEUE), job.parameters())
    await session.execute(text(WAKE), job.wake_parameters())
    return _view(message, [])


async def _finding_in(
    session: AsyncSession, asker: Asker, project_id: uuid.UUID, finding_id: uuid.UUID
) -> bool:
    found = await session.scalar(
        select(Finding.id).where(
            Finding.org_id == asker.org_id,
            Finding.project_id == project_id,
            Finding.id == finding_id,
        )
    )
    return found is not None


async def _conversation_for(
    session: AsyncSession,
    asker: Asker,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    question: str,
    now: datetime,
) -> AssistantConversation:
    if conversation_id is not None:
        conversation = await _own_conversation(session, asker, conversation_id)
        if conversation.project_id != project_id:
            raise NotFoundError(NOT_FOUND)
        return conversation
    conversation = AssistantConversation(
        id=uuid.uuid4(),
        org_id=asker.org_id,
        user_id=asker.user_id,
        project_id=project_id,
        title=question.strip()[:TITLE_CHARACTERS],
        created_at=now,
        last_message_at=now,
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def _count_today(session: AsyncSession, asker: Asker, limit: int, now: datetime) -> None:
    """Atomic: no race and no deleted row can change the day's count (D56)."""
    statement = (
        insert(AssistantQuestionCount)
        .values(org_id=asker.org_id, user_id=asker.user_id, day=_today(now), count=1)
        .on_conflict_do_update(
            index_elements=["org_id", "user_id", "day"],
            set_={"count": AssistantQuestionCount.count + 1},
            where=AssistantQuestionCount.count < limit,
        )
        .returning(AssistantQuestionCount.count)
    )
    if (await session.execute(statement)).first() is None:
        raise ConflictError(REFUSALS["daily_limit"], code="daily_limit")


async def _mark_stale(session: AsyncSession, asker: Asker, now: datetime) -> None:
    """The person's own question past its clock, so a dead worker cannot block them."""
    stale = await session.scalars(
        select(AssistantMessage.id).where(
            AssistantMessage.org_id == asker.org_id,
            AssistantMessage.user_id == asker.user_id,
            AssistantMessage.state.in_(IN_FLIGHT),
            ((AssistantMessage.state == "queued") & (AssistantMessage.queue_expires_at <= now))
            | (AssistantMessage.deadline_at <= now),
        )
    )
    for message_id in stale:
        await session.execute(_FINISH, {"id": message_id, "state": "unavailable"})


async def _history(
    session: AsyncSession, org_id: uuid.UUID, conversation_id: uuid.UUID
) -> list[dict[str, Any]]:
    """A capped snapshot of earlier answered turns; nulled when the question ends."""
    earlier = (
        await session.scalars(
            select(AssistantMessage)
            .where(
                AssistantMessage.org_id == org_id,
                AssistantMessage.conversation_id == conversation_id,
                AssistantMessage.state.in_(_MESSAGE_COLUMNS_DONE),
            )
            .order_by(AssistantMessage.created_at.desc())
            .limit(HISTORY_TURNS)
        )
    ).all()
    turns: list[dict[str, Any]] = []
    room = HISTORY_CHARACTERS
    for message in reversed(earlier):
        words = " ".join(
            str(sentence.get("text", ""))
            for sentence in (message.answer or {}).get("sentences", [])
            if isinstance(sentence, dict)
        )
        turn = {"question": message.question[: room // 2], "answer": words[: room // 2]}
        room -= len(turn["question"]) + len(turn["answer"])
        if room <= 0:
            break
        turns.append(turn)
    return turns


# ------------------------------------------------------------------ reading


async def _own_conversation(
    session: AsyncSession, asker: Asker, conversation_id: uuid.UUID
) -> AssistantConversation:
    found: AssistantConversation | None = await session.scalar(
        select(AssistantConversation).where(
            AssistantConversation.org_id == asker.org_id,
            AssistantConversation.user_id == asker.user_id,
            AssistantConversation.id == conversation_id,
        )
    )
    if found is None:
        raise NotFoundError(NOT_FOUND)
    return found


async def conversations(
    session: AsyncSession, *, identity: Identity, project_id: uuid.UUID
) -> list[ConversationSummary]:
    asker = asker_of(identity)
    rows = (
        await session.scalars(
            select(AssistantConversation)
            .where(
                AssistantConversation.org_id == asker.org_id,
                AssistantConversation.user_id == asker.user_id,
                AssistantConversation.project_id == project_id,
            )
            .order_by(AssistantConversation.last_message_at.desc())
            .limit(PAGE_MESSAGES)
        )
    ).all()
    in_flight = _pairs(
        (
            await session.execute(
                select(AssistantMessage.conversation_id, AssistantMessage.id).where(
                    AssistantMessage.org_id == asker.org_id,
                    AssistantMessage.user_id == asker.user_id,
                    AssistantMessage.state.in_(IN_FLIGHT),
                )
            )
        ).tuples()
    )
    return [
        ConversationSummary(
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            last_message_at=row.last_message_at,
            in_flight_message_id=in_flight.get(row.id),
        )
        for row in rows
    ]


async def conversation(
    session: AsyncSession,
    *,
    identity: Identity,
    conversation_id: uuid.UUID,
    before: datetime | None,
    now: datetime,
) -> ConversationView:
    asker = asker_of(identity)
    found = await _own_conversation(session, asker, conversation_id)
    statement = select(AssistantMessage).where(
        AssistantMessage.org_id == asker.org_id, AssistantMessage.conversation_id == found.id
    )
    if before is not None:
        statement = statement.where(AssistantMessage.created_at < before)
    messages = (
        await session.scalars(
            statement.order_by(AssistantMessage.created_at.desc()).limit(PAGE_MESSAGES + 1)
        )
    ).all()
    page = list(reversed(messages[:PAGE_MESSAGES]))
    steps = await _steps_of(session, asker.org_id, [message.id for message in page])
    kept = await _cases_kept_days(session, asker.org_id, steps)
    return ConversationView(
        id=found.id,
        project_id=found.project_id,
        title=found.title,
        messages=[
            _view(message, steps.get(message.id, []), now=now, cases_kept_days=kept)
            for message in page
        ],
        has_older=len(messages) > PAGE_MESSAGES,
    )


async def message(
    session: AsyncSession,
    *,
    identity: Identity,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    now: datetime,
) -> MessageView:
    """What the panel polls while a question is in flight: one row, never the conversation."""
    asker = asker_of(identity)
    found = await _own_message(session, asker, conversation_id, message_id)
    steps = await _steps_of(session, asker.org_id, [found.id])
    kept = await _cases_kept_days(session, asker.org_id, steps)
    return _view(found, steps.get(found.id, []), now=now, cases_kept_days=kept)


async def _cases_kept_days(
    session: AsyncSession, org_id: uuid.UUID, steps: Mapping[uuid.UUID, Sequence[AssistantStep]]
) -> int | None:
    """How long a resolved case is kept after it ends, for FR42's sentence on an empty
    knowledge search; read only when a knowledge step is shown."""
    if not any(step.tool == KNOWLEDGE_TOOL for found in steps.values() for step in found):
        return None
    return await derived_retention_days(session, org_id=org_id)


async def _own_message(
    session: AsyncSession, asker: Asker, conversation_id: uuid.UUID, message_id: uuid.UUID
) -> AssistantMessage:
    found: AssistantMessage | None = await session.scalar(
        select(AssistantMessage).where(
            AssistantMessage.org_id == asker.org_id,
            AssistantMessage.user_id == asker.user_id,
            AssistantMessage.conversation_id == conversation_id,
            AssistantMessage.id == message_id,
        )
    )
    if found is None:
        raise NotFoundError(NOT_FOUND)
    return found


async def _steps_of(
    session: AsyncSession, org_id: uuid.UUID, message_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[AssistantStep]]:
    if not message_ids:
        return {}
    rows = await session.scalars(
        select(AssistantStep)
        .where(AssistantStep.org_id == org_id, AssistantStep.message_id.in_(message_ids))
        .order_by(AssistantStep.position)
    )
    grouped: dict[uuid.UUID, list[AssistantStep]] = defaultdict(list)
    for row in rows:
        grouped[row.message_id].append(row)
    return grouped


def _state_now(message: AssistantMessage, now: datetime | None) -> str:
    """A question past its clock reads `unavailable` before the sweep writes it."""
    if now is None or message.state not in IN_FLIGHT:
        return message.state
    if message.state == "queued" and now >= message.queue_expires_at:
        return "unavailable"
    if now >= message.hard_ends_at:
        return "unavailable"
    return message.state


def _view(
    message: AssistantMessage,
    steps: Sequence[AssistantStep],
    *,
    now: datetime | None = None,
    cases_kept_days: int | None = None,
) -> MessageView:
    return MessageView(
        id=message.id,
        conversation_id=message.conversation_id,
        project_id=message.project_id,
        question=message.question,
        context=message.context,
        state=_state_now(message, now),
        phase=message.phase if message.state in IN_FLIGHT else None,
        failure_reason=message.failure_reason,
        rejection_reason=message.rejection_reason,
        abstain_reason=message.abstain_reason,
        answer=message.answer,
        wrong_reason=message.wrong_reason,
        created_at=message.created_at,
        finished_at=message.finished_at,
        steps=[
            _step_view(step, project_id=message.project_id, cases_kept_days=cases_kept_days)
            for step in steps
        ],
    )


def _step_view(
    step: AssistantStep, *, project_id: uuid.UUID, cases_kept_days: int | None = None
) -> StepView:
    tool = TOOLS_BY_NAME.get(step.tool)
    kept_since, failure = _kept_since(step.failure)
    return StepView(
        key=f"s{step.position}",
        position=step.position,
        tool=step.tool,
        words=tool.step_words if tool is not None else "A step that was refused",
        display_arguments=step.display_arguments,
        # The worker stores a phrase that failed its checks as null: read, never shown (FR14).
        search_hidden="q" in step.arguments and step.arguments["q"] is None,
        from_time=step.from_time,
        to_time=step.to_time,
        row_count=step.row_count,
        was_cut=step.was_cut,
        state=step.state,
        failure=failure,
        kept_since=kept_since,
        cases_kept_days=cases_kept_days if step.tool == KNOWLEDGE_TOOL else None,
        link=None if tool is None or step.state == "refused" else _link(step, project_id),
    )


def _kept_since(failure: str | None) -> tuple[date | None, str | None]:
    """The worker's kept-since note (`kept_since:YYYY-MM-DD`) as a date; any other failure
    as it is. A note that is not a date is shown as no date, never as text."""
    if failure is None or not failure.startswith(KEPT_SINCE):
        return None, failure
    try:
        return date.fromisoformat(failure.removeprefix(KEPT_SINCE)), None
    except ValueError:
        return None, None


def _link(step: AssistantStep, project_id: uuid.UUID) -> StepLink | None:
    """Where a step leads (design, *Where each step leads*); built by nexovue only, from the
    catalog and the step's own validated arguments. None when a route parameter is missing,
    since a link without it would open a different screen."""
    tool = TOOLS_BY_NAME[step.tool]
    params: dict[str, str] = {}
    for param, argument in tool.link.params.items():
        value = step.arguments.get(argument)
        if not isinstance(value, str) or not value:
            return None
        params[param] = value
    query: dict[str, str] = {"project": str(project_id)}
    if tool.link.carries_range and step.from_time is not None and step.to_time is not None:
        query["from"] = step.from_time.isoformat()
        query["to"] = step.to_time.isoformat()
    for argument, name in tool.link.carries.items():
        value = step.arguments.get(argument)
        # A phrase that failed its checks is stored as null, so it is never carried (AC62).
        if isinstance(value, str) and value:
            query[name] = value
    severity = step.arguments.get("min_severity")
    if tool.link.carries_severity and isinstance(severity, int) and severity in SEVERITY_FILTER_IDS:
        query["severity"] = SEVERITY_FILTER_IDS[severity]
    return StepLink(path=tool.link.path, params=params, query=query, caveat=tool.link.caveat)


# ------------------------------------------------------------------ acting on a question


async def stop(
    session: AsyncSession,
    *,
    identity: Identity,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    now: datetime,
) -> MessageView:
    asker = asker_of(identity)
    found = await _own_message(session, asker, conversation_id, message_id)
    if found.state in IN_FLIGHT:
        await session.execute(_FINISH, {"id": found.id, "state": "stopped"})
        await session.refresh(found)
    steps = await _steps_of(session, asker.org_id, [found.id])
    return _view(found, steps.get(found.id, []), now=now)


async def mark_wrong(
    session: AsyncSession,
    *,
    identity: Identity,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    reason: str | None,
    now: datetime,
) -> None:
    """ "This was wrong", or Undo. Admins see only a count; no AI learns from it (D47)."""
    asker = asker_of(identity)
    found = await _own_message(session, asker, conversation_id, message_id)
    if found.state not in _MESSAGE_COLUMNS_DONE and found.state != "abstained":
        raise ConflictError(NOT_FINISHED)
    if found.wrong_reason is not None:
        await _count(session, asker.org_id, f"wrong_{found.wrong_reason}", -1, now)
    found.wrong_reason = reason
    if reason is not None:
        await _count(session, asker.org_id, f"wrong_{reason}", 1, now)


async def _count(
    session: AsyncSession, org_id: uuid.UUID, kind: str, change: int, now: datetime
) -> None:
    statement = (
        insert(AssistantMonthlyCount)
        .values(org_id=org_id, month=_first_of_month(now), kind=kind, count=max(change, 0))
        .on_conflict_do_update(
            index_elements=["org_id", "month", "kind"],
            set_={"count": func.greatest(AssistantMonthlyCount.count + change, 0)},
        )
    )
    await session.execute(statement)


async def delete_conversation(
    session: AsyncSession, *, identity: Identity, conversation_id: uuid.UUID
) -> int:
    """Deletes it and its questions; 409 while one is in flight, since a cascade would lose
    what it holds. The day's count survives (D56)."""
    asker = asker_of(identity)
    found = await _own_conversation(session, asker, conversation_id)
    held = await session.scalar(
        select(func.count()).where(
            AssistantMessage.conversation_id == found.id,
            (AssistantMessage.state.in_(IN_FLIGHT)) | (AssistantMessage.reserved_tokens > 0),
        )
    )
    if held:
        raise ConflictError(STILL_RUNNING, code="still_answering")
    count = await session.scalar(
        select(func.count()).where(AssistantMessage.conversation_id == found.id)
    )
    await session.execute(
        delete(AssistantConversation).where(
            AssistantConversation.org_id == asker.org_id, AssistantConversation.id == found.id
        )
    )
    return int(count or 0)


# ------------------------------------------------------------------ settings and consent


async def chat_settings(
    session: AsyncSession, *, org_id: uuid.UUID, ai: AIAvailability, now: datetime
) -> ChatSettings:
    row = await settings_of(session, org_id=org_id) or AssistantSettings(
        org_id=org_id, enabled=False, daily_question_limit=50, data_level="facts", mask_emails=True
    )
    connection = await ai_queries.connection_of(session, org_id=org_id)
    consenter = (
        None
        if row.consented_by is None
        else await session.scalar(
            select(func.coalesce(User.name, User.email)).where(
                User.org_id == org_id, User.id == row.consented_by
            )
        )
    )
    month = _first_of_month(now)
    counts = _pairs(
        (
            await session.execute(
                select(AssistantMonthlyCount.kind, AssistantMonthlyCount.count).where(
                    AssistantMonthlyCount.org_id == org_id, AssistantMonthlyCount.month == month
                )
            )
        ).tuples()
    )
    people = (
        await session.execute(
            select(func.coalesce(User.name, User.email), func.sum(AssistantQuestionCount.count))
            .join(User, User.id == AssistantQuestionCount.user_id)
            .where(AssistantQuestionCount.org_id == org_id, AssistantQuestionCount.day >= month)
            .group_by(User.id, User.name, User.email)
            .order_by(func.sum(AssistantQuestionCount.count).desc())
        )
    ).tuples()
    return ChatSettings(
        enabled=row.enabled,
        daily_question_limit=row.daily_question_limit,
        data_level=row.data_level,
        mask_emails=row.mask_emails,
        consented_at=row.consented_at,
        consented_by_name=consenter,
        consent_current=connection is not None and consent_matches(row, connection),
        consent_fingerprint=None if connection is None else consent_fingerprint_of(connection),
        disclosure_version=CHAT_DISCLOSURE_VERSION,
        monthly_counts=counts,
        people_this_month=[PersonCount(name=name, questions=int(total)) for name, total in people],
    )


def _record(
    session: AsyncSession, identity: Identity, action: str, detail: Mapping[str, Any]
) -> None:
    audit.record_ai_event(
        session,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        action=action,
        target_id=identity.org_id,
        detail=dict(detail),
        target_kind=AUDIT_TARGET,
    )


async def save_settings(
    session: AsyncSession,
    *,
    identity: Identity,
    request: SaveChatSettings,
    ai: AIAvailability,
    now: datetime,
) -> ChatSettings:
    row = await settings_of(session, org_id=identity.org_id)
    if row is None:
        row = AssistantSettings(org_id=identity.org_id)
        session.add(row)
    changed = [
        name
        for name in ("enabled", "daily_question_limit", "data_level", "mask_emails")
        if getattr(row, name, None) != getattr(request, name)
    ]
    row.enabled = request.enabled
    row.daily_question_limit = request.daily_question_limit
    row.data_level = request.data_level
    row.mask_emails = request.mask_emails
    row.updated_at = now
    if changed:
        _record(session, identity, SETTINGS_CHANGED, {"fields": changed})
    await session.flush()
    return await chat_settings(session, org_id=identity.org_id, ai=ai, now=now)


async def consent(
    session: AsyncSession,
    *,
    identity: Identity,
    request: ChatConsent,
    ai: AIAvailability,
    now: datetime,
) -> ChatSettings:
    """An owner agrees to what they read: refused if it moved since (FR25, AC3)."""
    connection = await ai_queries.connection_of(session, org_id=identity.org_id)
    if connection is None:
        raise ConflictError(NO_CONNECTION)
    row = await settings_of(session, org_id=identity.org_id)
    if row is None:
        row = AssistantSettings(org_id=identity.org_id, data_level="facts")
        session.add(row)
        await session.flush()
    if (
        request.fingerprint != consent_fingerprint_of(connection)
        or request.disclosure_version != CHAT_DISCLOSURE_VERSION
        or request.data_level != row.data_level
    ):
        raise ConflictError(CONSENT_MOVED, code="consent_moved")
    row.consented_by = identity.user.id
    row.consented_at = now
    row.consented_fingerprint = request.fingerprint
    row.consented_disclosure_version = CHAT_DISCLOSURE_VERSION
    row.consented_data_level = request.data_level
    _record(
        session,
        identity,
        DISCLOSURE_AGREED,
        {"host": provider_host(connection.base_url), "data_level": request.data_level},
    )
    await session.flush()
    return await chat_settings(session, org_id=identity.org_id, ai=ai, now=now)


async def reads(session: AsyncSession, *, org_id: uuid.UUID, limit: int = 200) -> list[ReadRow]:
    """What was sent: person, time, project, tool, range, rows, provider. No filter values."""
    rows = await session.execute(
        select(AssistantReadLog, func.coalesce(User.name, User.email), Project.name)
        .outerjoin(User, User.id == AssistantReadLog.user_id)
        .outerjoin(Project, Project.id == AssistantReadLog.project_id)
        .where(AssistantReadLog.org_id == org_id)
        .order_by(AssistantReadLog.at.desc())
        .limit(limit)
    )
    return [
        ReadRow(
            at=read.at,
            person=person,
            project=project,
            tool=read.tool,
            from_time=read.from_time,
            to_time=read.to_time,
            row_count=read.row_count,
            provider_label=read.provider_label,
        )
        for read, person, project in rows.tuples()
    ]
