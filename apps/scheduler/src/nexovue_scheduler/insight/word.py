"""The wording job: one version's checked facts put into plainer words (F3 design, *The
wording decision and run*; plan T5.4), run by `scheduler-insight` alone.

The job carries ids and a generation only (AC60). It reads `model_input`, never the
evidence or the names (AC45), and acts only while the row is `pending` at that generation,
so a newer click, a mute or an opt-out since is never overwritten, and an older job stops
without spending (AC81).

1. The version: gone, superseded, no longer pending, or another generation: stop.
2. The connection, the project and the consent, read again: AI off, opted out, deleted, an
   address failing FR23, or consent no longer covering these values: `off`, nothing spent.
   Only absences: `abstained`, no model called (FR33). The key opens only for this address.
3. One transaction under the finding's row lock: the run cap on a first attempt, the input
   limit, the month's reservation, then the run row, committed before the call.
4. One call, through the guarded transport.
5. The answer: a refusal is read before any parse (a 200 may be one); then FR37's checks.
6. Invalid output, a 5xx or a 429 asks for one more attempt while one is left, as a new
   attempt with its own limits, reservation and run row (FR38); a 429's `Retry-After` is its
   wait, up to the job's life.
7. One transaction, only while the run is still `started`: the run finished, the tokens
   moved from reserved to used, and the words over `pending` of this generation. A run the
   sweep already called `died` keeps the sweep's charge and writes nothing.
8. Anything else writes `failed`, reason `error`, before it is raised, and what is raised
   holds no body, since the worker logs it (AC54, AC59).

The model's words are never logged, and no exception here carries a provider's bytes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_platform.ai.connection import (
    AI_CONNECTION_PURPOSE,
    ConnectionRefused,
    check_connection,
    check_key,
    consent_fingerprint,
    key_owner,
    provider_host,
)
from nexovue_platform.ai.contract import AIResponse, HttpCall, Outcome, estimate_tokens
from nexovue_platform.ai.explain_prompt import PROMPT_VERSION, explain_request, fields_sent
from nexovue_platform.ai.formats import wire_format
from nexovue_platform.ai.limits import ceiling, input_estimate, reservation, too_long
from nexovue_platform.ai.validate import Checked, check_answer
from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, SealError, keyring_from
from nexovue_platform.deployment import Deployment, ai_available
from nexovue_platform.evidence import (
    WORD_EXPIRES_SECONDS,
    WORD_EXPLANATION_TASK,
    WORD_MAX_ATTEMPTS,
)
from nexovue_platform.evidence.model_facts import SCHEMA_VERSION
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.insight import store, transport
from nexovue_scheduler.jobs import registry
from nexovue_scheduler.jobs.registry import Retry, SoftTimeLimit, task
from nexovue_scheduler.probes.addresses import PrivateEndpoints
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# How long a 5xx, an unreadable answer, or a 429 with no readable `Retry-After` waits before
# its one more attempt (design, *Failure handling*; a guess).
RETRY_WAIT_SECONDS: Final = 60.0
# What a retried job's `last_error` says: nexovue's words, never a provider's (AC59).
RETRY_SAYS: Final[dict[str, str]] = {
    "unavailable": "the provider did not answer; one more attempt",
    "limited": "the provider asked to wait; one more attempt",
    "invalid": "the answer did not pass nexovue's checks; one more attempt",
}
# Outcomes that get one more attempt (FR38): the provider may answer next time.
_RETRIED: Final = frozenset(RETRY_SAYS)
_TRANSPORT_RETRIED: Final = frozenset({"timed_out", "unreachable"})


class WordingFailed(Exception):  # noqa: N818 — what the worker logs: a closed word, no body
    """A fault in this task, after `failed` was written. Its text is one closed word."""


@dataclass(frozen=True, slots=True)
class Ending:
    """What a call came to: the run row's ending, the state it leaves, and whether it may be
    tried again. `state` None with `retry` means the row stays `pending` for the next."""

    run: store.RunEnd
    state: str
    reason: str | None
    words: dict[str, Any] | None = None
    retry: bool = False
    countdown: float = RETRY_WAIT_SECONDS


async def word(
    engine: AsyncEngine,
    *,
    explanation_id: str,
    org_id: str,
    generation: int,
    requested_by: str | None,
    keys: ConfiguredKeys,
    endpoints: PrivateEndpoints,
    deployment: Deployment,
    clock: Callable[[], datetime],
    send: transport.Send = transport.send,
    attempt: int = 1,
    max_attempts: int = WORD_MAX_ATTEMPTS,
) -> str:
    """One attempt at wording one version. Returns what it did, for the log and tests."""
    try:
        return await _word(
            engine,
            explanation_id=explanation_id,
            org_id=org_id,
            generation=generation,
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
            "a wording run failed in nexovue's own code",
            explanation_id=explanation_id,
            org_id=org_id,
            error=type(fault).__name__,
        )
        await store.set_state(
            engine,
            org_id=org_id,
            explanation_id=explanation_id,
            generation=generation,
            state="failed",
            reason="error",
            at=clock(),
        )
        if isinstance(fault, SoftTimeLimit):
            raise
        raise WordingFailed("error") from None


async def _word(
    engine: AsyncEngine,
    *,
    explanation_id: str,
    org_id: str,
    generation: int,
    requested_by: str | None,
    keys: ConfiguredKeys,
    endpoints: PrivateEndpoints,
    deployment: Deployment,
    clock: Callable[[], datetime],
    send: transport.Send,
    attempt: int,
    max_attempts: int,
) -> str:
    version = await store.load_version(engine, org_id=org_id, explanation_id=explanation_id)
    if (
        version is None
        or version.superseded
        or version.wording_state != "pending"
        or version.wording_generation != generation
    ):
        return "stale"
    model_input = version.model_input or {}

    async def leave(state: str, reason: str | None = None) -> str:
        await store.set_state(
            engine,
            org_id=org_id,
            explanation_id=explanation_id,
            generation=generation,
            state=state,
            reason=reason,
            at=clock(),
        )
        return state if reason is None else f"{state}:{reason}"

    connection = await store.load_connection(engine, org_id)
    if not await _may_send(engine, connection, version, deployment):
        return await leave("off")
    assert connection is not None
    if not _has_a_fact(model_input):
        return await leave("abstained")
    opened, key = open_key(org_id, connection, keys)
    if not opened:
        return await leave("failed", "bad_key")
    fmt = wire_format(connection.wire_format)
    call = fmt.build(
        explain_request(model_input),
        model=connection.model,
        base_url=connection.base_url,
        key=key,
    )
    input_tokens = input_estimate(call.body)
    reserve = reservation(input_tokens)
    now = clock()
    admission = await store.admit(
        engine,
        version=version,
        generation=generation,
        first_attempt=attempt == 1,
        reserve=reserve,
        ceiling=ceiling(connection.monthly_token_limit or 0, _severity(model_input)),
        too_long=too_long(input_tokens),
        run=store.RunStart(
            org_id=org_id,
            requested_by=requested_by,
            purpose="explain",
            wire_format=connection.wire_format,
            model=connection.model,
            region_label=connection.region_label,
            provider_host=provider_host(connection.base_url),
            prompt_version=PROMPT_VERSION,
            request_sha256=call.sha256(),
            fields_sent=tuple(fields_sent(model_input)),
            attempt=attempt,
            project_id=version.project_id,
            subject_id=version.finding_id,
            explanation_id=explanation_id,
            generation=generation,
            reserved_tokens=reserve,
            started_at=now,
        ),
        now=now,
    )
    if admission.stale:
        return "stale"
    if admission.refused is not None:
        logger.info(
            "a wording run was refused before it spent",
            explanation_id=explanation_id,
            org_id=org_id,
            reason=admission.refused,
        )
        return f"refused:{admission.refused}"
    assert admission.run_id is not None and admission.month is not None

    answer = send(call, timeout_seconds=connection.call_seconds, private_endpoints=endpoints)
    ending = _ending(
        answer,
        fmt.parse,
        call,
        model_input,
        model=connection.model,
        finished_at=clock(),
        may_retry=attempt < max_attempts,
        life_left=_life_left(version, clock()),
    )
    finished = await store.finish_wording(
        engine,
        org_id=org_id,
        explanation_id=explanation_id,
        generation=generation,
        run_id=admission.run_id,
        month=admission.month,
        reserved=reserve,
        end=ending.run,
        words=ending.words,
        state=None if ending.retry else ending.state,
        reason=ending.reason,
    )
    logger.info(
        "a wording run ended",
        explanation_id=explanation_id,
        org_id=org_id,
        run_id=admission.run_id,
        outcome=ending.run.outcome,
        input_tokens=ending.run.input_tokens,
        output_tokens=ending.run.output_tokens,
        attempt=attempt,
        written=finished == "written",
    )
    if finished == "already_ended":
        # Only a child that outlived its hard limit gets here: the sweep's charge stands, and
        # there is nothing to retry.
        logger.warning(
            "a wording run finished after the sweep had ended it",
            explanation_id=explanation_id,
            org_id=org_id,
            run_id=admission.run_id,
        )
        return "stale"
    if ending.retry:
        raise Retry(ending.countdown, RETRY_SAYS[ending.run.outcome])
    return ending.state if ending.reason is None else f"{ending.state}:{ending.reason}"


async def _may_send(
    engine: AsyncEngine,
    connection: store.StoredConnection | None,
    version: store.StoredVersion,
    deployment: Deployment,
) -> bool:
    """Everything that must still hold when a run is about to spend (design, step 2)."""
    if connection is None or not connection.enabled or not ai_available(deployment):
        return False
    if connection.monthly_token_limit is None or not consent_covers(connection):
        return False
    try:
        check_connection(
            wire_format=connection.wire_format,
            base_url=connection.base_url,
            model=connection.model,
            region=connection.region_label,
        )
    except ConnectionRefused:
        return False
    return await store.project_takes_wording(
        engine, org_id=version.org_id, project_id=version.project_id
    )


def consent_covers(connection: store.StoredConnection) -> bool:
    """The owner's consent still names these values and this facts schema (FR26, BR12).
    F5's general suggestion compares consent through this too (design, *Rung 2b*)."""
    return (
        connection.consented_base_url == connection.base_url
        and connection.consented_schema == SCHEMA_VERSION
        and connection.consented_fingerprint
        == consent_fingerprint(
            wire_format=connection.wire_format,
            base_url=connection.base_url,
            region=connection.region_label,
            key_id=connection.key_id,
        )
    )


def _has_a_fact(model_input: Mapping[str, Any]) -> bool:
    """FR33: with only absences, no model is called."""
    return any(
        isinstance(fact, Mapping) and fact.get("kind") != "absence"
        for fact in model_input.get("facts") or []
    )


def _severity(model_input: Mapping[str, Any]) -> object:
    finding = model_input.get("finding")
    return finding.get("severity") if isinstance(finding, Mapping) else None


def open_key(
    org_id: str, connection: store.StoredConnection, keys: ConfiguredKeys
) -> tuple[bool, str | None]:
    """Whether the key opened, and the key, opened only for this address: None for a server
    that needs none. It will not open after a rotation it missed (spec, *Error cases*)."""
    if connection.sealed_key is None:
        return True, None
    try:
        box = SealedBox(keyring_from(keys.current, keys.previous), purpose=AI_CONNECTION_PURPOSE)
        key = box.open(
            connection.sealed_key, owner=key_owner(org_id, connection.id, connection.base_url)
        )
        check_key(key)
    except (SealError, ValueError, ConnectionRefused):
        return False, None
    return True, key


def _life_left(version: store.StoredVersion, now: datetime) -> float:
    """Seconds until the job expires: it was added when the row moved to `pending`."""
    if version.wording_changed_at is None:
        return 0.0
    expires = version.wording_changed_at + timedelta(seconds=WORD_EXPIRES_SECONDS)
    return (expires - now).total_seconds()


def _ending(
    answer: transport.Answer,
    parse: Callable[[int, bytes], AIResponse],
    call: HttpCall,
    model_input: Mapping[str, Any],
    *,
    model: str,
    finished_at: datetime,
    may_retry: bool,
    life_left: float,
) -> Ending:
    """What one call came to (design, *Failure handling*)."""
    if answer.status is None:
        # Nothing came back. A call that may have reached the provider is counted, overstated.
        reached = answer.reached
        run = store.RunEnd(
            outcome="unavailable",
            input_tokens=estimate_tokens(len(call.body)) if reached else 0,
            output_tokens=0,
            tokens_estimated=reached,
            finished_at=finished_at,
        )
        retry = may_retry and answer.failure in _TRANSPORT_RETRIED
        return Ending(run=run, state="failed", reason="unavailable", retry=retry)
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
        checked = check_answer(response.text or "", model_input)
        if isinstance(checked, Checked):
            if checked.abstained:
                return Ending(run=run_end("abstained"), state="abstained", reason=None)
            # The configured name, never the provider's own word for it (FR19, AC51).
            return Ending(
                run=run_end("done"), state="done", reason=None, words=checked.stored(model)
            )
        outcome: Outcome = "invalid"
    else:
        outcome = response.outcome
    countdown = RETRY_WAIT_SECONDS
    if outcome == "limited" and answer.retry_after_seconds is not None:
        countdown = answer.retry_after_seconds
    retry = may_retry and outcome in _RETRIED and countdown < life_left
    return Ending(
        run=run_end(outcome), state="failed", reason=outcome, retry=retry, countdown=countdown
    )


@task(WORD_EXPLANATION_TASK)
def word_task(
    *, explanation_id: str, org_id: str, generation: int, requested_by: str | None = None
) -> None:
    settings = get_settings()
    did = with_engine(
        lambda engine: word(
            engine,
            explanation_id=explanation_id,
            org_id=org_id,
            generation=generation,
            requested_by=requested_by,
            keys=settings.ai_secret_keys,
            endpoints=settings.ai_endpoints,
            deployment=settings.nexovue_deployment,
            clock=lambda: datetime.now(UTC),
            attempt=registry.attempt(),
        )
    )
    logger.info("a wording job ended", explanation_id=explanation_id, org_id=org_id, did=did)
