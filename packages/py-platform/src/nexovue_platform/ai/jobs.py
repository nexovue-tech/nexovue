"""The connection test's job, by name, so the API adds it and never imports the transport
(spec FR24, AC35): the API never calls a provider (A3).
"""

from __future__ import annotations

from typing import Final, Literal

from nexovue_platform.evidence import INSIGHT_QUEUE
from nexovue_platform.jobs import JobRequest

CHECK_AI_CONNECTION_TASK: Final = "nexovue_scheduler.insight.check"
# What a test leaves in `job_results` (written by migration 0070's function), and how long.
AI_CHECK_RESULT_KIND: Final = "ai_check"
AI_CHECK_RESULT_TTL_SECONDS: Final = 600
# The screen waits this long before saying the AI container did not answer (a guess).
AI_CHECK_ANSWER_SECONDS: Final = 60
# A test's own call limit, under the screen's wait, whatever the connection allows.
CHECK_CALL_SECONDS: Final = 45
CHECK_EXPIRES_SECONDS: Final = 300
CHECK_SOFT_LIMIT_SECONDS: Final = CHECK_CALL_SECONDS + 20
CHECK_HARD_LIMIT_SECONDS: Final = CHECK_CALL_SECONDS + 40

# Why a test did not pass, as the admin reads it (spec, *Error cases*). Never a provider's
# words: closed, so nothing a provider says reaches the page (AC59).
CheckReason = Literal[
    "no_connection",
    "not_allowed",
    "key_unreadable",
    "address_refused",
    "timed_out",
    "unreachable",
    "bad_key",
    "not_found",
    "bad_request",
    "limited",
    "unavailable",
    "refused",
    "too_long",
    "invalid",
]
CHECK_REASONS: Final[tuple[CheckReason, ...]] = (
    "no_connection",
    "not_allowed",
    "key_unreadable",
    "address_refused",
    "timed_out",
    "unreachable",
    "bad_key",
    "not_found",
    "bad_request",
    "limited",
    "unavailable",
    "refused",
    "too_long",
    "invalid",
)


def check_request(*, org_id: str, request_id: str, requested_by: str | None) -> JobRequest:
    """One connection test. Ids only: the task reads the connection itself, bound by org."""
    return JobRequest(
        task=CHECK_AI_CONNECTION_TASK,
        queue=INSIGHT_QUEUE,
        kwargs={"org_id": org_id, "request_id": request_id, "requested_by": requested_by},
        org_id=org_id,
        expires_seconds=CHECK_EXPIRES_SECONDS,
        max_attempts=1,
        soft_limit_seconds=CHECK_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=CHECK_HARD_LIMIT_SECONDS,
    )
