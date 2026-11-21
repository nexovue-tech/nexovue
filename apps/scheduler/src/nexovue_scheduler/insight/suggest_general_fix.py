"""Rung 2b's job: one general fix suggestion, run by `scheduler-insight` alone (F5 design,
*Rung 2b*, *The flow* step 2; plan T2b.6).

The job carries ids only. It acts only while its row is `proposing`, so a row the sweep or
a newer state ended is never overwritten.

1. The row: gone, or no longer `proposing`: stop, nothing spent.
2. The connection, the project and the consent, read again, compared as F3's `word` does:
   AI off, opted out, or consent no longer covering these values ends the row with nothing
   sent. The key opens only for this address.
3. The request: F5's fixed instructions, F3's `model_input` for the version the click
   named, and the picked language, as **one trusted part** (FR34). No `Untrusted` part.
4. One transaction under the row's lock: F5's share of the month reserved through F3's
   statement, then the run row, committed before the call.
5. One call, through F3's guarded transport.
6. The answer: a refusal is read before any parse; then G1 to G9. G1 and G2, a 5xx or a 429
   get one more attempt, as a new attempt reading the fixed `retry_code` (D51). G3 to G9
   refuse at once, storing only the rule (FR36): the refused text is never stored.
7. One transaction: the run finished, the tokens settled, the row ended or kept for retry;
   a run F3's sweep had already called `died` moves nothing and writes nothing.
8. Anything else ends the row `failed`, `nexovue_error`, before it is raised, and what is
   raised holds no body, since the worker logs it.

The model's words are never logged.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from nexovue_platform.ai.connection import ConnectionRefused, check_connection, provider_host
from nexovue_platform.ai.contract import (
    AIRequest,
    AIResponse,
    HttpCall,
    Trusted,
    canonical_json,
    estimate_tokens,
)
from nexovue_platform.ai.formats import wire_format
from nexovue_platform.ai.limits import fix_ceiling, input_estimate, reservation, too_long
from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.deployment import Deployment, ai_available
from nexovue_platform.fixes.general_prompt import (
    INSTRUCTIONS,
    MAX_OUTPUT_TOKENS,
    PROMPT_VERSION,
    RESPONSE_SCHEMA,
    SCHEMA_NAME,
    request_document,
)
from nexovue_platform.fixes.general_rules import (
    GeneralAbstained,
    GeneralRefused,
    GeneralSuggestion,
    check_general_text,
)
from nexovue_platform.fixes.records import (
    FAILED,
    GENERAL_FIELDS_SENT,
    GENERAL_LANGUAGES,
    KIND_GENERAL,
    PROPOSED,
    PROPOSING,
    REFUSED,
    SUGGEST_GENERAL_FIX_TASK,
    SUGGEST_MAX_ATTEMPTS,
    GeneralLanguage,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.insight import fix_store, store, transport
from nexovue_scheduler.insight.word import consent_covers, open_key
from nexovue_scheduler.jobs import registry
from nexovue_scheduler.jobs.registry import Retry, SoftTimeLimit, task
from nexovue_scheduler.probes.addresses import PrivateEndpoints
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# How long a retried attempt waits (as F3's wording, a guess).
RETRY_WAIT_SECONDS: Final = 60.0
# What a retried job's `last_error` says: Nexovue's words, never a provider's.
RETRY_SAYS: Final[dict[str, str]] = {
    "unavailable": "the provider did not answer; one more attempt",
    "limited": "the provider asked to wait; one more attempt",
    "invalid": "the answer did not pass nexovue's rules; one more attempt",
}
_TRANSPORT_RETRIED: Final = frozenset({"timed_out", "unreachable"})
# What the log says when a call ends after F3's sweep had called its run `died`.
LATE_FINISH: Final = "a general suggestion's call finished after the sweep had ended its run"
# A provider's own word for why it did not answer, as the row's reason.
_PROVIDER_REASONS: Final[dict[str, str]] = {
    "refused": "provider_refused",
    "too_long": "provider_too_long",
}


class GeneralFixFailed(Exception):  # noqa: N818 — what the worker logs: a closed word
    """A fault in this task, after `failed` was written. Its text is one closed word."""


@dataclass(frozen=True, slots=True)
class Ending:
    """What one call came to: its run row's ending, where it leaves the row, and whether a
    retry is asked for (then `end.state` is None)."""

    run: store.RunEnd
    end: fix_store.FixEnd
    retry_says: str | None = None
    countdown: float = RETRY_WAIT_SECONDS


async def suggest(
    engine: AsyncEngine,
    *,
    fix_id: str,
    org_id: str,
    requested_by: str | None,
    keys: ConfiguredKeys,
    endpoints: PrivateEndpoints,
    deployment: Deployment,
    clock: Callable[[], datetime],
    send: transport.Send = transport.send,
    attempt: int = 1,
    max_attempts: int = SUGGEST_MAX_ATTEMPTS,
) -> str:
    """One attempt at one general suggestion. Returns what it did, for the log and tests."""
    try:
        return await _suggest(
            engine,
            fix_id=fix_id,
            org_id=org_id,
            requested_by=requested_by,
            keys=keys,
            endpoints=endpoints,
            deployment=deployment,
            clock=clock,
            send=send,
            attempt=attempt,
            max_attempts=max_attempts,
        )
    except Retry:
        raise
    except (SoftTimeLimit, Exception) as fault:
        logger.error(
            "a general suggestion failed in nexovue's own code",
            fix_id=fix_id,
            org_id=org_id,
            error=type(fault).__name__,
        )
        fix = await fix_store.load_fix(engine, org_id=org_id, fix_id=fix_id)
        if fix is not None:
            await fix_store.end_without_call(
                engine, fix, fix_store.FixEnd(FAILED, "nexovue_error"), at=clock()
            )
        if isinstance(fault, SoftTimeLimit):
            raise
        raise GeneralFixFailed("error") from None


async def _suggest(
    engine: AsyncEngine,
    *,
    fix_id: str,
    org_id: str,
    requested_by: str | None,
    keys: ConfiguredKeys,
    endpoints: PrivateEndpoints,
    deployment: Deployment,
    clock: Callable[[], datetime],
    send: transport.Send,
    attempt: int,
    max_attempts: int,
) -> str:
    fix = await fix_store.load_fix(engine, org_id=org_id, fix_id=fix_id)
    if fix is None or fix.state != PROPOSING or fix.kind != KIND_GENERAL:
        return "stale"

    async def leave(reason: str) -> str:
        await fix_store.end_without_call(engine, fix, fix_store.FixEnd(FAILED, reason), at=clock())
        return f"{FAILED}:{reason}"

    connection = await store.load_connection(engine, org_id)
    off = await _why_not_send(engine, connection, fix, deployment)
    if off is not None:
        return await leave(off)
    assert connection is not None
    model_input = await fix_store.load_model_input(engine, fix)
    if model_input is None or fix.language not in GENERAL_LANGUAGES:
        return await leave("nexovue_error")
    language = fix.language
    opened, key = open_key(org_id, connection, keys)
    if not opened:
        return await leave("provider_unavailable")
    fmt = wire_format(connection.wire_format)
    call = fmt.build(
        general_request(model_input, language, fix.retry_code),
        model=connection.model,
        base_url=connection.base_url,
        key=key,
    )
    input_tokens = input_estimate(call.body)
    if too_long(input_tokens):
        return await leave("provider_too_long")
    reserve = reservation(input_tokens, MAX_OUTPUT_TOKENS)
    now = clock()
    admission = await fix_store.admit(
        engine,
        fix,
        reserve=reserve,
        ceiling=fix_ceiling(connection.monthly_token_limit or 0),
        run=store.RunStart(
            org_id=org_id,
            requested_by=requested_by,
            purpose="fix",
            wire_format=connection.wire_format,
            model=connection.model,
            region_label=connection.region_label,
            provider_host=provider_host(connection.base_url),
            prompt_version=PROMPT_VERSION,
            request_sha256=call.sha256(),
            fields_sent=GENERAL_FIELDS_SENT,
            attempt=attempt,
            project_id=fix.project_id,
            subject_id=fix.finding_id,
            reserved_tokens=reserve,
            started_at=now,
        ),
        now=now,
    )
    if admission.stale:
        return "stale"
    if admission.refused is not None:
        logger.info(
            "a general suggestion was refused before it spent",
            fix_id=fix_id,
            org_id=org_id,
            reason=admission.refused,
        )
        return f"{FAILED}:{admission.refused}"
    assert admission.run_id is not None and admission.month is not None

    answer = send(call, timeout_seconds=connection.call_seconds, private_endpoints=endpoints)
    ending = ending_of(
        answer,
        fmt.parse,
        call,
        model_input,
        language=language,
        finished_at=clock(),
        may_retry=attempt < max_attempts,
    )
    finished = await fix_store.finish(
        engine,
        fix,
        run_id=admission.run_id,
        month=admission.month,
        reserved=reserve,
        run_end=ending.run,
        end=ending.end,
    )
    logger.info(
        "a general suggestion's call ended",
        fix_id=fix_id,
        org_id=org_id,
        run_id=admission.run_id,
        outcome=ending.run.outcome,
        input_tokens=ending.run.input_tokens,
        output_tokens=ending.run.output_tokens,
        attempt=attempt,
        written=finished == "written",
    )
    if finished == "already_ended":
        # Only a child that outlived its hard limit gets here: the sweep's charge stands, the
        # answer is dropped, and rung 2b's own sweep ends the row.
        logger.warning(LATE_FINISH, fix_id=fix_id, org_id=org_id, run_id=admission.run_id)
        return "stale"
    if ending.retry_says is not None and finished == "written":
        raise Retry(ending.countdown, ending.retry_says)
    end = ending.end
    return f"{end.state}" if end.reason is None else f"{end.state}:{end.reason}"


def general_request(
    model_input: Mapping[str, Any], language: GeneralLanguage, retry_code: str | None
) -> AIRequest:
    """Rung 2b's request: F5's instructions and one trusted part holding exactly the
    version's `model_input` and the language (FR34, AC68), and a retry's fixed sentence."""
    document = request_document(model_input, language, retry_code)
    return AIRequest(
        instructions=INSTRUCTIONS,
        parts=(Trusted(canonical_json(document).decode()),),
        response_schema=RESPONSE_SCHEMA,
        schema_name=SCHEMA_NAME,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        purpose="fix",
    )


async def _why_not_send(
    engine: AsyncEngine,
    connection: store.StoredConnection | None,
    fix: fix_store.StoredFix,
    deployment: Deployment,
) -> str | None:
    """What must still hold when a call is about to spend, as F3's `word` reads it, as the
    row's reason; None when it may be sent."""
    if connection is None or not connection.enabled or not ai_available(deployment):
        return "ai_off"
    if connection.monthly_token_limit is None:
        return "ai_off"
    if not consent_covers(connection):
        return "consent_ended_in_flight"
    try:
        check_connection(
            wire_format=connection.wire_format,
            base_url=connection.base_url,
            model=connection.model,
            region=connection.region_label,
        )
    except ConnectionRefused:
        return "consent_ended_in_flight"
    if not await store.project_takes_wording(engine, org_id=fix.org_id, project_id=fix.project_id):
        return "project_opted_out"
    return None


def ending_of(
    answer: transport.Answer,
    parse: Callable[[int, bytes], AIResponse],
    call: HttpCall,
    model_input: Mapping[str, Any],
    *,
    language: GeneralLanguage,
    finished_at: datetime,
    may_retry: bool,
) -> Ending:
    """What one call came to (design, *Failure handling*), as the run's ending and the row's."""
    if answer.status is None:
        reached = answer.reached
        run = store.RunEnd(
            outcome="unavailable",
            input_tokens=estimate_tokens(len(call.body)) if reached else 0,
            output_tokens=0,
            tokens_estimated=reached,
            finished_at=finished_at,
        )
        if may_retry and answer.failure in _TRANSPORT_RETRIED:
            return Ending(run, fix_store.FixEnd(None), RETRY_SAYS["unavailable"])
        return Ending(run, fix_store.FixEnd(FAILED, "provider_unavailable"))
    response = (
        AIResponse("invalid")
        if answer.failure == "too_large"
        else parse(answer.status, answer.body)
    )
    counted = response.with_counts(request_body=call.body)

    def run_end(outcome: str) -> store.RunEnd:
        return store.RunEnd(
            outcome=outcome,
            input_tokens=counted.input_tokens,
            output_tokens=counted.output_tokens,
            tokens_estimated=counted.tokens_estimated,
            finished_at=finished_at,
        )

    if response.outcome == "ok":
        return _checked_ending(
            check_general_text(response.text or "", language=language, model_input=model_input),
            run_end,
            may_retry=may_retry,
        )
    if response.outcome in ("unavailable", "limited") and may_retry:
        countdown = RETRY_WAIT_SECONDS
        if response.outcome == "limited" and answer.retry_after_seconds is not None:
            countdown = answer.retry_after_seconds
        return Ending(
            run_end(response.outcome),
            fix_store.FixEnd(None),
            RETRY_SAYS[response.outcome],
            countdown,
        )
    reason = _PROVIDER_REASONS.get(response.outcome, "provider_unavailable")
    if response.outcome == "invalid":
        reason = "invalid_output"
    return Ending(run_end(response.outcome), fix_store.FixEnd(FAILED, reason))


def _checked_ending(
    verdict: GeneralSuggestion | GeneralAbstained | GeneralRefused,
    run_end: Callable[[str], store.RunEnd],
    *,
    may_retry: bool,
) -> Ending:
    if isinstance(verdict, GeneralSuggestion):
        end = fix_store.FixEnd(
            PROPOSED, suggestion=verdict.stored(), evidence_ids=verdict.evidence_ids
        )
        return Ending(run_end("done"), end)
    if isinstance(verdict, GeneralAbstained):
        return Ending(run_end("abstained"), fix_store.FixEnd(REFUSED, verdict.reason))
    if verdict.retry_code is not None and may_retry:
        keep = fix_store.FixEnd(None, retry_code=verdict.retry_code)
        return Ending(run_end("invalid"), keep, RETRY_SAYS["invalid"])
    state = FAILED if verdict.retry_code is not None else REFUSED
    return Ending(run_end("invalid"), fix_store.FixEnd(state, verdict.reason))


@task(SUGGEST_GENERAL_FIX_TASK)
def suggest_general_fix_task(*, fix_id: str, org_id: str, requested_by: str | None = None) -> None:
    settings = get_settings()
    did = with_engine(
        lambda engine: suggest(
            engine,
            fix_id=fix_id,
            org_id=org_id,
            requested_by=requested_by,
            keys=settings.ai_secret_keys,
            endpoints=settings.ai_endpoints,
            deployment=settings.nexovue_deployment,
            clock=lambda: datetime.now(UTC),
            attempt=registry.attempt(),
        )
    )
    logger.info("a general suggestion job ended", fix_id=fix_id, org_id=org_id, did=did)
