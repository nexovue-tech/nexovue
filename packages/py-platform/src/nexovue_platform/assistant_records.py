"""F4's closed lists, queue, task and clocks: what the API, the AI role and migration 0073 share.

The migration holds frozen copies of these lists; `test_assistant_records.py` holds them
equal, so a change here is a migration of its own (as F3's `ai_records.py`).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final, Literal

from nexovue_platform.jobs import JobRequest

ASSISTANT_QUEUE: Final = "assistant"
ANSWER_QUESTION_TASK: Final = "nexovue_scheduler.insight.assistant.answer_question"
ASSISTANT_SWEEP_TASK: Final = "nexovue_scheduler.insight.assistant_sweep.sweep"

# Raised when what chat's consent says changes (the preview's words, or what goes): every
# owner must agree again. The API and the AI role both compare against it.
CHAT_DISCLOSURE_VERSION: Final = 1

# The purpose a ticket is sealed under for the worker; its owner is the message id.
TICKET_PURPOSE: Final = "assistant-ticket"

MessageState = Literal[
    "queued",
    "running",
    "answered",
    "partial",
    "abstained",
    "withheld",
    "stopped",
    "refused",
    "failed",
    "over_limit",
    "unavailable",
]
IN_FLIGHT: Final[tuple[MessageState, ...]] = ("queued", "running")
FINAL_STATES: Final[tuple[MessageState, ...]] = (
    "answered",
    "partial",
    "abstained",
    "withheld",
    "stopped",
    "refused",
    "failed",
    "over_limit",
    "unavailable",
)
MESSAGE_STATES: Final[tuple[MessageState, ...]] = (*IN_FLIGHT, *FINAL_STATES)

Phase = Literal["waiting", "asking", "reading", "checking"]
PHASES: Final[tuple[Phase, ...]] = ("waiting", "asking", "reading", "checking")

# Why a question ended without an answer, in nexovue's words (closed; never a provider's).
FailureReason = Literal[
    # refused: something it rests on changed
    "ai_off",
    "chat_off",
    "opted_out",
    "consent_changed",
    "ticket_refused",
    # failed: the provider or the transport
    "unavailable",
    "limited",
    "bad_key",
    "not_found",
    "bad_request",
    "refused",
    "too_long",
    "invalid",
    "timed_out",
    "unreachable",
    "address_refused",
    "not_allowed",
    "key_unreadable",
    "no_connection",
    "soft_limit",
    "died",
]
FAILURE_REASONS: Final[tuple[FailureReason, ...]] = (
    "ai_off",
    "chat_off",
    "opted_out",
    "consent_changed",
    "ticket_refused",
    "unavailable",
    "limited",
    "bad_key",
    "not_found",
    "bad_request",
    "refused",
    "too_long",
    "invalid",
    "timed_out",
    "unreachable",
    "address_refused",
    "not_allowed",
    "key_unreadable",
    "no_connection",
    "soft_limit",
    "died",
)
# Why an answer was not shown (design, section 4).
# `copying`: a long exact run of a tool result, which must be quoted by reference instead.
RejectionReason = Literal["shape", "citations", "way_out", "command", "copying"]
REJECTION_REASONS: Final[tuple[RejectionReason, ...]] = (
    "shape",
    "citations",
    "way_out",
    "command",
    "copying",
)
# The model may abstain only with one of these, and nexovue says it in its own words.
AbstainReason = Literal["not_enough_evidence", "no_data", "outside_what_it_reads"]
ABSTAIN_REASONS: Final[tuple[AbstainReason, ...]] = (
    "not_enough_evidence",
    "no_data",
    "outside_what_it_reads",
)
WrongReason = Literal["wrong_numbers", "wrong_cause", "missed_something", "other"]
WRONG_REASONS: Final[tuple[WrongReason, ...]] = (
    "wrong_numbers",
    "wrong_cause",
    "missed_something",
    "other",
)
StepState = Literal["running", "read", "failed", "aged_out", "refused"]
STEP_STATES: Final[tuple[StepState, ...]] = ("running", "read", "failed", "aged_out", "refused")
# What `assistant_finish_message` does with what a question still holds (design, *Settling*).
Remainder = Literal["return", "charge", "keep"]
REMAINDERS: Final[tuple[Remainder, ...]] = ("return", "charge", "keep")

# ------------------------------------------------------------------ limits (all guesses)

MAX_QUESTION_CHARACTERS: Final = 2_000
DEFAULT_DAILY_QUESTION_LIMIT: Final = 50
MAX_DAILY_QUESTION_LIMIT: Final = 1_000
MAX_STEPS: Final = 8
# What one question reserves of the month; chat's share of the limit is F3's
# `limits.CHAT_SHARE` (D50).
QUESTION_RESERVATION_TOKENS: Final = 60_000
MAX_OUTPUT_TOKENS: Final = 1_500
MIN_DEADLINE_SECONDS: Final = 300
DEADLINE_CALLS: Final = 3
MIN_QUEUE_SECONDS: Final = 120
HARD_LIMIT_GRACE_SECONDS: Final = 30
SOFT_LIMIT_MARGIN_SECONDS: Final = 30
# A history snapshot holds at most this many earlier turns, each cut (design, *Cost*).
HISTORY_TURNS: Final = 4
HISTORY_CHARACTERS: Final = 4_000


def deadline_seconds(call_seconds: int) -> int:
    """How long a question may run, from its connection's call limit (FR22)."""
    return max(MIN_DEADLINE_SECONDS, DEADLINE_CALLS * call_seconds)


def queue_seconds(call_seconds: int) -> int:
    return max(MIN_QUEUE_SECONDS, call_seconds)


def clocks(created_at: datetime, call_seconds: int) -> tuple[datetime, datetime, datetime]:
    """One clock (design, *One clock*): queue expiry, deadline, and the hard end."""
    queue_expires_at = created_at + timedelta(seconds=queue_seconds(call_seconds))
    deadline_at = created_at + timedelta(seconds=deadline_seconds(call_seconds))
    hard_ends_at = deadline_at + timedelta(seconds=call_seconds + HARD_LIMIT_GRACE_SECONDS)
    return queue_expires_at, deadline_at, hard_ends_at


def answer_question_request(*, message_id: str, org_id: str, call_seconds: int) -> JobRequest:
    """The job for one question: ids only (foundation section 1), one attempt (D36). The
    organization rides with the message id so every statement the worker runs binds it."""
    hard = deadline_seconds(call_seconds) + call_seconds + HARD_LIMIT_GRACE_SECONDS
    return JobRequest(
        task=ANSWER_QUESTION_TASK,
        queue=ASSISTANT_QUEUE,
        kwargs={"message_id": message_id, "org_id": org_id},
        org_id=org_id,
        expires_seconds=queue_seconds(call_seconds),
        max_attempts=1,
        soft_limit_seconds=hard - SOFT_LIMIT_MARGIN_SECONDS,
        hard_limit_seconds=hard,
    )


# What chat keeps only for a while, read by the scheduler's expiry sweep (design, *Other
# inventories*; AC35): (table, the columns that name a row, what makes it lapsed). Here, not
# in the AI role, because `scheduler-light` runs the sweep and must load no AI code.
_SETTLED: Final = "state NOT IN ('queued', 'running') AND reserved_tokens = 0"
# A question past its project's derived retention, the findings' rule; a deleted project has
# no `project_retention` row, so the third entry takes its conversations instead.
_PAST_DERIVED_RETENTION: Final = f"""
    {_SETTLED}
    AND created_at < now() - make_interval(days => (
        SELECT retention.derived_retention_days FROM project_retention AS retention
        WHERE retention.project_id = assistant_messages.project_id
          AND retention.org_id = assistant_messages.org_id
    ))"""
_HOLDS_NOTHING: Final = f"""NOT EXISTS (
        SELECT 1 FROM assistant_messages AS message
        WHERE message.conversation_id = assistant_conversations.id
          AND message.org_id = assistant_conversations.org_id
          AND NOT ({_SETTLED})
    )"""
_EMPTY_OR_GONE: Final = f"""
    NOT EXISTS (
        SELECT 1 FROM assistant_messages AS message
        WHERE message.conversation_id = assistant_conversations.id
          AND message.org_id = assistant_conversations.org_id
    )
    OR (
        {_HOLDS_NOTHING}
        AND EXISTS (
            SELECT 1 FROM projects AS project
            JOIN organizations AS organization ON organization.id = project.org_id
            WHERE project.id = assistant_conversations.project_id
              AND project.org_id = assistant_conversations.org_id
              AND (project.deleted_at IS NOT NULL OR organization.deleted_at IS NOT NULL)
        )
    )"""
EXPIRED: Final = (
    ("assistant_messages", "id", _PAST_DERIVED_RETENTION),
    # Its last message gone, or its project or organization deleted, once nothing in it is
    # in flight or holding tokens (chat's sweep settles those first).
    ("assistant_conversations", "id", _EMPTY_OR_GONE),
    ("assistant_read_log", "id", "at < now() - interval '13 months'"),
    ("assistant_question_counts", "org_id, user_id, day", "day < now() - interval '13 months'"),
)
