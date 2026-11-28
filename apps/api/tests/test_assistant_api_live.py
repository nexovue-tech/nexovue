"""F4 plan T2.6: chat's API against real Postgres (AC1, AC2, AC3, AC5, AC6, AC26, AC37, AC54,
AC74, AC79, AC86, AC88, AC91).

Driven through the service functions the routes call, inside a transaction that is rolled
back: each test owns its rows, and no job it enqueues is ever committed for the stack's
workers. The routes' gates are held by `test_authorization_coverage.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from assistant_live import (
    AVAILABLE,
    KEYS,
    NOW,
    chat_on,
    db,
    reported,
    require_postgres,
    tenant,
)
from nexovue_platform.assistant_records import (
    ANSWER_QUESTION_TASK,
    ASSISTANT_QUEUE,
    CHAT_DISCLOSURE_VERSION,
    TICKET_PURPOSE,
)
from nexovue_platform.checks.sealing import SealedBox, keyring_from
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.ai.service import consent_fingerprint_of
from nexovue_api.assistant import service, tickets
from nexovue_api.assistant.schemas import AskContext, ChatConsent, SaveChatSettings
from nexovue_api.auth.dependencies import Identity
from nexovue_api.auth.sessions import Impersonation
from nexovue_api.data.postgres.models import (
    AIProjectOptOut,
    AssistantMessage,
    AssistantQuestionCount,
    AssistantSettings,
    AssistantStep,
    Job,
    User,
)
from nexovue_api.errors.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from nexovue_api.signup.service import ProvisionedTenant

require_postgres("chat's API suite")
__all__ = ["db"]


def _identity(made: ProvisionedTenant, user: User | None = None) -> Identity:
    return Identity(user=user or made.user, organization=made.organization)


async def _ask(
    db: AsyncSession,
    made: ProvisionedTenant,
    *,
    conversation_id: uuid.UUID | None = None,
    now: datetime = NOW,
    identity: Identity | None = None,
    question: str = "What is failing right now?",
) -> Any:
    return await service.ask(
        db,
        identity=identity or _identity(made),
        project_id=made.project.id,
        conversation_id=conversation_id,
        question=question,
        context=AskContext(time_zone="Europe/London"),
        ai=AVAILABLE,
        keys=KEYS,
        now=now,
    )


async def _ready(db: AsyncSession, **chat: Any) -> ProvisionedTenant:
    made = await tenant(db)
    await chat_on(db, made, **chat)
    return made


async def test_a_question_is_stored_with_its_ticket_clocks_and_job_in_one_go(
    db: AsyncSession,
) -> None:
    made = await _ready(db)

    view = await _ask(db, made)

    message = await db.get(AssistantMessage, view.id)
    assert message is not None
    assert (message.state, message.phase, message.data_level) == (
        "queued",
        "waiting",
        "facts_and_text",
    )
    assert message.deadline_at > message.queue_expires_at > NOW
    assert message.hard_ends_at > message.deadline_at
    # The sealed ticket opens, for this message only, to the ticket its hash names.
    box = SealedBox(keyring_from(KEYS.current, KEYS.previous), purpose=TICKET_PURPOSE)
    assert message.sealed_ticket is not None
    ticket = box.open(message.sealed_ticket, owner=str(message.id))
    assert ticket.startswith(tickets.TICKET_PREFIX)
    assert message.ticket_hash == tickets.fingerprint(ticket)
    job = await db.scalar(
        select(Job).where(Job.org_id == made.organization.id, Job.task == ANSWER_QUESTION_TASK)
    )
    assert job is not None and job.queue == ASSISTANT_QUEUE
    assert job.kwargs == {"message_id": str(message.id), "org_id": str(made.organization.id)}
    assert job.max_attempts == 1


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("no_connection", "ai_off"),
        ("chat_off", "chat_off"),
        ("level", "no_consent"),
        ("opt_out", "opted_out"),
    ],
)
async def test_each_refusal_has_its_own_code_and_stores_nothing(
    db: AsyncSession, change: str, code: str
) -> None:
    """AC1, AC2, AC79: never a 429, which would lose the reason in a toast."""
    made = await tenant(db)
    if change != "no_connection":
        await chat_on(db, made)
    if change == "chat_off":
        await db.execute(
            update(AssistantSettings)
            .values(enabled=False)
            .where(AssistantSettings.org_id == made.organization.id)
        )
    if change == "level":
        await db.execute(
            update(AssistantSettings)
            .values(data_level="facts")
            .where(AssistantSettings.org_id == made.organization.id)
        )
    if change == "opt_out":
        db.add(AIProjectOptOut(org_id=made.organization.id, project_id=made.project.id))
        await db.flush()

    with pytest.raises(ConflictError) as refused:
        await _ask(db, made)

    assert refused.value.extensions["code"] == code
    stored = await db.scalar(
        select(func.count())
        .select_from(AssistantMessage)
        .where(AssistantMessage.org_id == made.organization.id)
    )
    assert stored == 0


async def test_one_question_in_flight_per_person(db: AsyncSession) -> None:
    made = await _ready(db)
    await _ask(db, made)

    with pytest.raises(ConflictError) as refused:
        await _ask(db, made)

    assert refused.value.extensions["code"] == "still_answering"


async def test_a_stale_question_is_marked_and_frees_the_person(db: AsyncSession) -> None:
    """AC54: a dead worker cannot block anyone; the read says `unavailable` at once."""
    made = await _ready(db)
    first = await _ask(db, made)
    later = NOW + timedelta(hours=1)

    polled = await service.message(
        db,
        identity=_identity(made),
        conversation_id=first.conversation_id,
        message_id=first.id,
        now=later,
    )
    assert polled.state == "unavailable"
    second = await _ask(db, made, now=later)

    stale = await db.get(AssistantMessage, first.id, populate_existing=True)
    assert stale is not None and stale.state == "unavailable" and stale.ticket_hash is None
    assert second.state == "queued"


async def test_the_daily_limit_is_counted_apart_from_conversations(db: AsyncSession) -> None:
    """AC37: deleting a conversation does not give a question back."""
    made = await _ready(db, daily_limit=1)
    first = await _ask(db, made)
    await service.stop(
        db,
        identity=_identity(made),
        conversation_id=first.conversation_id,
        message_id=first.id,
        now=NOW,
    )
    await service.delete_conversation(
        db, identity=_identity(made), conversation_id=first.conversation_id
    )

    with pytest.raises(ConflictError) as refused:
        await _ask(db, made)

    assert refused.value.extensions["code"] == "daily_limit"
    counted = await db.scalar(
        select(AssistantQuestionCount.count).where(AssistantQuestionCount.user_id == made.user.id)
    )
    assert counted == 1


async def test_stop_ends_the_question_and_its_ticket_but_moves_no_money(db: AsyncSession) -> None:
    """AC28's API half: `keep`, so the worker or the sweep settles."""
    made = await _ready(db)
    asked = await _ask(db, made)
    await db.execute(
        update(AssistantMessage)
        .where(AssistantMessage.id == asked.id)
        .values(state="running", reserved_tokens=60_000)
    )

    stopped = await service.stop(
        db,
        identity=_identity(made),
        conversation_id=asked.conversation_id,
        message_id=asked.id,
        now=NOW,
    )

    assert stopped.state == "stopped"
    row = await db.get(AssistantMessage, asked.id, populate_existing=True)
    assert row is not None and (row.ticket_hash, row.reserved_tokens) == (None, 60_000)
    with pytest.raises(ConflictError) as refused:
        await service.delete_conversation(
            db, identity=_identity(made), conversation_id=asked.conversation_id
        )
    # Spec *Error cases*, "Deleted conversation", word for word.
    assert refused.value.detail == "Stop this question first."


async def test_another_person_s_conversation_is_not_found(db: AsyncSession) -> None:
    """AC6: its existence is private too, from an admin as well."""
    made = await _ready(db)
    asked = await _ask(db, made)
    other = User(
        org_id=made.organization.id, email=f"other-{uuid.uuid4()}@example.test", role="admin"
    )
    db.add(other)
    await db.flush()
    stranger = _identity(made, other)

    with pytest.raises(NotFoundError):
        await service.message(
            db,
            identity=stranger,
            conversation_id=asked.conversation_id,
            message_id=asked.id,
            now=NOW,
        )
    with pytest.raises(NotFoundError):
        await service.stop(
            db,
            identity=stranger,
            conversation_id=asked.conversation_id,
            message_id=asked.id,
            now=NOW,
        )
    with pytest.raises(NotFoundError):
        await _ask(db, made, conversation_id=asked.conversation_id, identity=stranger)
    assert await service.conversations(db, identity=stranger, project_id=made.project.id) == []


async def test_a_support_view_reads_no_conversation(db: AsyncSession) -> None:
    made = await _ready(db)
    viewing = Identity(
        user=made.user,
        organization=made.organization,
        impersonation=Impersonation(
            grant_id=str(uuid.uuid4()),
            impersonation_id=str(uuid.uuid4()),
            operator_label="support",
            reason="a customer asked",
            until=NOW + timedelta(hours=1),
        ),
    )

    with pytest.raises(PermissionDeniedError):
        await service.conversations(db, identity=viewing, project_id=made.project.id)


async def test_availability_names_the_label_and_questions_left_never_the_address(
    db: AsyncSession,
) -> None:
    """AC74."""
    made = await _ready(db, daily_limit=3)
    await reported(db, made)
    await _ask(db, made)

    ready = await service.availability(
        db, identity=_identity(made), project_id=made.project.id, ai=AVAILABLE, now=NOW
    )

    assert (ready.state, ready.provider_label, ready.questions_left_today) == (
        "ready",
        "OpenAI-compatible",
        2,
    )
    assert "provider.example" not in ready.model_dump_json()


async def test_a_project_that_never_reported_says_so_before_anyone_asks(
    db: AsyncSession,
) -> None:
    """Spec *Error cases*, "No telemetry in this project yet": before anyone spends tokens."""
    made = await _ready(db)

    before = await service.availability(
        db, identity=_identity(made), project_id=made.project.id, ai=AVAILABLE, now=NOW
    )
    await reported(db, made)
    after = await service.availability(
        db, identity=_identity(made), project_id=made.project.id, ai=AVAILABLE, now=NOW
    )

    assert (before.state, before.provider_label) == ("no_data", None)
    assert after.state == "ready"


async def test_availability_without_consent_names_the_owners_it_waits_for(db: AsyncSession) -> None:
    made = await _ready(db)
    await db.execute(
        update(AssistantSettings)
        .values(consented_fingerprint="moved")
        .where(AssistantSettings.org_id == made.organization.id)
    )

    waiting = await service.availability(
        db, identity=_identity(made), project_id=made.project.id, ai=AVAILABLE, now=NOW
    )

    assert waiting.state == "no_consent"
    assert waiting.waiting_for == [made.user.name or made.user.email]
    # Live test D1, 2026-11-27: whether the model passed a real call does not wait on consent.
    assert waiting.tested is True


async def test_consent_binds_the_fingerprint_disclosure_and_data_level(db: AsyncSession) -> None:
    """AC3: what the owner read must still be what goes."""
    made = await _ready(db)
    connection = await chat_on_connection(db, made)
    await service.save_settings(
        db,
        identity=_identity(made),
        request=SaveChatSettings(
            enabled=True, daily_question_limit=10, data_level="facts", mask_emails=True
        ),
        ai=AVAILABLE,
        now=NOW,
    )
    standing = await service.availability(
        db, identity=_identity(made), project_id=made.project.id, ai=AVAILABLE, now=NOW
    )
    assert standing.state == "no_consent"

    with pytest.raises(ConflictError):
        await service.consent(
            db,
            identity=_identity(made),
            request=ChatConsent(
                fingerprint="old", disclosure_version=CHAT_DISCLOSURE_VERSION, data_level="facts"
            ),
            ai=AVAILABLE,
            now=NOW,
        )
    agreed = await service.consent(
        db,
        identity=_identity(made),
        request=ChatConsent(
            fingerprint=consent_fingerprint_of(connection),
            disclosure_version=CHAT_DISCLOSURE_VERSION,
            data_level="facts",
        ),
        ai=AVAILABLE,
        now=NOW,
    )
    assert agreed.consent_current
    audited = await db.scalar(
        text("SELECT count(*) FROM audit_log WHERE org_id = :o AND action LIKE 'assistant.%'"),
        {"o": made.organization.id},
    )
    assert audited == 2


async def chat_on_connection(db: AsyncSession, made: ProvisionedTenant) -> Any:
    from nexovue_api.ai import queries as ai_queries

    return await ai_queries.connection_of(db, org_id=made.organization.id)


async def test_a_step_links_where_nexovue_says_with_the_range_and_filters(db: AsyncSession) -> None:
    """AC22's Step 2 half: links are built by nexovue from the catalog, never by the model."""
    made = await _ready(db)
    asked = await _ask(db, made)
    start = NOW - timedelta(minutes=30)
    db.add(
        AssistantStep(
            org_id=made.organization.id,
            message_id=asked.id,
            position=1,
            tool="search_logs",
            arguments={"service": "checkout", "min_severity": 17, "q": None},
            from_time=start,
            to_time=NOW,
            state="read",
            row_count=3,
        )
    )
    await db.flush()

    polled = await service.message(
        db,
        identity=_identity(made),
        conversation_id=asked.conversation_id,
        message_id=asked.id,
        now=NOW,
    )

    [step] = polled.steps
    assert step.search_hidden is True
    assert step.link is not None and step.link.path == "/logs"
    assert step.link.query == {
        "project": str(made.project.id),
        "from": start.isoformat(),
        "to": NOW.isoformat(),
        "service": "checkout",
        "severity": "error",
    }


async def test_marking_wrong_counts_and_undo_takes_it_back(db: AsyncSession) -> None:
    """AC91: admins see only a count."""
    made = await _ready(db)
    asked = await _ask(db, made)
    with pytest.raises(ConflictError):
        await service.mark_wrong(
            db,
            identity=_identity(made),
            conversation_id=asked.conversation_id,
            message_id=asked.id,
            reason="other",
            now=NOW,
        )
    await db.execute(
        update(AssistantMessage)
        .where(AssistantMessage.id == asked.id)
        .values(state="answered", ticket_hash=None, sealed_ticket=None)
    )

    await service.mark_wrong(
        db,
        identity=_identity(made),
        conversation_id=asked.conversation_id,
        message_id=asked.id,
        reason="wrong_numbers",
        now=NOW,
    )
    marked = await service.chat_settings(db, org_id=made.organization.id, ai=AVAILABLE, now=NOW)
    await service.mark_wrong(
        db,
        identity=_identity(made),
        conversation_id=asked.conversation_id,
        message_id=asked.id,
        reason=None,
        now=NOW,
    )
    undone = await service.chat_settings(db, org_id=made.organization.id, ai=AVAILABLE, now=NOW)

    assert marked.monthly_counts["wrong_wrong_numbers"] == 1
    assert undone.monthly_counts["wrong_wrong_numbers"] == 0
    assert marked.people_this_month[0].questions == 1
