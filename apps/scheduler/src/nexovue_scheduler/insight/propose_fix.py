"""Rung 3's job: one suggestion from the files a person sent, run by `scheduler-insight`
alone (F5 design, *The flow*, step 5; plan T4.3, T4.4, T4.5, T4.13).

The job carries ids only, and acts only while its row is `proposing`, so a row the sweep,
a cancel or a withdrawal ended is never overwritten.

1. The row: gone, or no longer `proposing`: stop, nothing spent.
2. What must still hold, read again: AI on, the project not opted out, F3's consent, and
   an owner's source consent for this repository covering the row's folder and naming the
   connection as it is now (FR8). This room cannot read code locations; the API compared
   those at Send. The key opens only for this address.
3. The request: F5's instructions; one trusted part of numbers, F3's `model_input` and a
   retry's fixed sentence; each file, and the person's hint, in its own fenced
   `Untrusted` part (U2). No path, message or GitHub text reaches the provider.
4. One transaction under the row's lock: F5's share of the month reserved through F3's
   statement, then the run row, committed before the call.
5. One call, through F3's guarded transport.
6. The answer, judged by `fixes.proposal`: K1 to K10 on the pack's whole files, the
   display facts computed by Nexovue (D53). A misquote, an edit on a never-changed file,
   an unusable answer, a 5xx or a 429 get one more attempt, as a new attempt reading the
   fixed `retry_code` (D51). Any other rule withholds at once, storing the rule and pack
   references alone. `needs_files` (rounds 1 and 2) stores the names and hands the row
   back to the pack job through `fix_request_more_files` (D75).
7. One transaction: the run settled, the pack deleted first, then the row ended; a run
   F3's sweep had already called `died` moves nothing and writes nothing.
8. Anything else ends the row `failed`, `nexovue_error`, before it is raised, and what is
   raised holds no body, since the worker logs it.

The model's words, the files and the hint are never logged.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from nexovue_platform.ai.connection import provider_host
from nexovue_platform.ai.contract import AIRequest, HttpCall, Trusted, canonical_json
from nexovue_platform.ai.formats import wire_format
from nexovue_platform.ai.limits import fix_ceiling, input_estimate, reservation, too_long
from nexovue_platform.ai.untrusted import Untrusted
from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.deployment import Deployment
from nexovue_platform.evidence.aliases import ALIAS_TOKEN, AliasBook
from nexovue_platform.fixes.checks import PackFile
from nexovue_platform.fixes.consent import ProviderTerms, folder_covered, terms_match
from nexovue_platform.fixes.policy import OwnerLists
from nexovue_platform.fixes.proposal import (
    Abstained,
    NeedsFiles,
    Proposed,
    RetryWith,
    Verdict,
    Withheld,
    judge,
)
from nexovue_platform.fixes.records import (
    CONSENT_ENDED_IN_FLIGHT,
    FAILED,
    HINT_MAX_CHARACTERS,
    INVALID_OUTPUT,
    KIND_SOURCE,
    MAX_SENT_BYTES,
    PROPOSE_FIX_TASK,
    PROPOSED,
    PROPOSING,
    REFUSED,
    SOURCE_FIELDS_SENT,
    SOURCE_FIELDS_SENT_WITHOUT_FACTS,
    SUGGEST_MAX_ATTEMPTS,
)
from nexovue_platform.fixes.source_prompt import (
    HINT_LABEL,
    MAX_OUTPUT_TOKENS,
    PROMPT_VERSION,
    SCHEMA_NAME,
    SentFile,
    file_label,
    instructions,
    may_ask,
    response_schema,
    sent_files,
    trusted_document,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.insight import fix_store, store, transport
from nexovue_scheduler.insight import source_fix_store as source_store
from nexovue_scheduler.insight.source_fix_store import SourceEnd, SourceFix
from nexovue_scheduler.insight.suggest_general_fix import (
    RETRY_SAYS,
    RETRY_WAIT_SECONDS,
    CallFailed,
    read_answer,
    why_not_send,
)
from nexovue_scheduler.insight.word import open_key
from nexovue_scheduler.jobs import registry
from nexovue_scheduler.jobs.registry import Retry, SoftTimeLimit, task
from nexovue_scheduler.probes.addresses import PrivateEndpoints
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# What the log says when a call ends after F3's sweep had called its run `died`.
LATE_FINISH: Final = "a suggestion's call finished after the sweep had ended its run"


class ProposeFixFailed(Exception):  # noqa: N818 — what the worker logs: a closed word
    """A fault in this task, after `failed` was written. Its text is one closed word."""


@dataclass(frozen=True, slots=True)
class Ending:
    """What one call came to: its run row's ending, where it leaves the row, and whether a
    retry is asked for (then `end.state` is None and `end.asked` is None)."""

    run: store.RunEnd
    end: SourceEnd
    retry_says: str | None = None
    countdown: float = RETRY_WAIT_SECONDS


async def propose(
    engine: AsyncEngine,
    *,
    fix_id: str,
    org_id: str,
    sent_by: str | None,
    keys: ConfiguredKeys,
    endpoints: PrivateEndpoints,
    deployment: Deployment,
    clock: Callable[[], datetime],
    send: transport.Send = transport.send,
    attempt: int = 1,
    max_attempts: int = SUGGEST_MAX_ATTEMPTS,
) -> str:
    """One attempt at one suggestion. Returns what it did, for the log and tests."""
    try:
        return await _propose(
            engine,
            fix_id=fix_id,
            org_id=org_id,
            sent_by=sent_by,
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
            "a suggestion failed in nexovue's own code",
            fix_id=fix_id,
            org_id=org_id,
            error=type(fault).__name__,
        )
        fix = await source_store.load_fix(engine, org_id=org_id, fix_id=fix_id)
        if fix is not None and fix.state == PROPOSING:
            await source_store.end_without_call(
                engine, fix, SourceEnd(FAILED, "nexovue_error"), at=clock()
            )
        if isinstance(fault, SoftTimeLimit):
            raise
        raise ProposeFixFailed("error") from None


async def _propose(
    engine: AsyncEngine,
    *,
    fix_id: str,
    org_id: str,
    sent_by: str | None,
    keys: ConfiguredKeys,
    endpoints: PrivateEndpoints,
    deployment: Deployment,
    clock: Callable[[], datetime],
    send: transport.Send,
    attempt: int,
    max_attempts: int,
) -> str:
    fix = await source_store.load_fix(engine, org_id=org_id, fix_id=fix_id)
    if fix is None or fix.state != PROPOSING or fix.kind != KIND_SOURCE:
        return "stale"

    async def leave(reason: str) -> str:
        await source_store.end_without_call(engine, fix, SourceEnd(FAILED, reason), at=clock())
        return f"{FAILED}:{reason}"

    connection = await store.load_connection(engine, org_id)
    off = await why_not_send(engine, connection, fix, deployment)
    if off is not None:
        return await leave(off)
    assert connection is not None
    owner = await _covering_consent(engine, fix, connection)
    if owner is None:
        return await leave(CONSENT_ENDED_IN_FLIGHT)
    pack = await source_store.load_pack(engine, fix)
    if pack is None or not pack.files:
        return await leave("nexovue_error")
    model_input = None
    if fix.explanation_version is not None:
        model_input = await fix_store.model_input_of(
            engine,
            org_id=fix.org_id,
            finding_id=fix.finding_id,
            version=fix.explanation_version,
        )
        if model_input is None:
            return await leave("nexovue_error")
    opened, key = open_key(org_id, connection, keys)
    if not opened:
        return await leave("provider_unavailable")
    sent = sent_files(pack.files)
    fmt = wire_format(connection.wire_format)

    def built(files: list[SentFile]) -> HttpCall:
        return fmt.build(
            source_request(
                model_input=model_input,
                sent=files,
                frames=pack.frames,
                hint=pack.hint,
                round_number=fix.round,
                retry_code=fix.retry_code,
            ),
            model=connection.model,
            base_url=connection.base_url,
            key=key,
        )

    call = built(sent)
    input_tokens = input_estimate(call.body)
    # The code is bounded by the pack's byte cap, which the person confirmed; F3's input
    # limit bounds everything else (live test E4, 2026-11-27).
    if too_long(input_estimate(built([]).body)):
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
            requested_by=sent_by,
            purpose="fix",
            wire_format=connection.wire_format,
            model=connection.model,
            region_label=connection.region_label,
            provider_host=provider_host(connection.base_url),
            prompt_version=PROMPT_VERSION,
            request_sha256=call.sha256(),
            fields_sent=SOURCE_FIELDS_SENT
            if model_input is not None
            else SOURCE_FIELDS_SENT_WITHOUT_FACTS,
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
        return f"{FAILED}:{admission.refused}"
    assert admission.run_id is not None and admission.month is not None

    answer = send(call, timeout_seconds=connection.call_seconds, private_endpoints=endpoints)
    read = read_answer(
        answer, fmt.parse, call, finished_at=clock(), may_retry=attempt < max_attempts
    )
    if isinstance(read, CallFailed):
        ending = _failed(read)
    else:
        verdict = judge(
            read.text,
            pack=_pack_files(pack.files),
            sent=sent,
            folder=fix.folder,
            owner=owner,
            allowed_evidence=allowed_evidence(model_input, sent),
            aliases=input_aliases(model_input),
            may_ask=may_ask(fix.round),
        )
        ending = ending_of(verdict, read.run_end, may_retry=attempt < max_attempts)
    finished = await source_store.finish(
        engine,
        fix,
        run_id=admission.run_id,
        month=admission.month,
        reserved=reserve,
        run_end=ending.run,
        end=ending.end,
    )
    logger.info(
        "a suggestion's call ended",
        fix_id=fix_id,
        org_id=org_id,
        run_id=admission.run_id,
        outcome=ending.run.outcome,
        input_tokens=ending.run.input_tokens,
        output_tokens=ending.run.output_tokens,
        attempt=attempt,
        round=fix.round,
        written=finished == "written",
    )
    if finished == "already_ended":
        logger.warning(LATE_FINISH, fix_id=fix_id, org_id=org_id, run_id=admission.run_id)
        return "stale"
    if ending.retry_says is not None and finished == "written":
        raise Retry(ending.countdown, ending.retry_says)
    end = ending.end
    if end.asked is not None:
        return "needs_files" if finished == "written" else "stale"
    return f"{end.state}" if end.reason is None else f"{end.state}:{end.reason}"


async def _covering_consent(
    engine: AsyncEngine, fix: SourceFix, connection: store.StoredConnection
) -> OwnerLists | None:
    """FR8: a live consent for this repository whose scope covers the row's folder, naming
    the connection's address, format, key and model as they are now. Its owner's lists come
    back for K2; None means nothing may be sent."""
    current = ProviderTerms(
        base_url=connection.base_url,
        wire_format=connection.wire_format,
        key_id=connection.key_id,
        model=connection.model,
    )
    for consent in await source_store.live_consents(engine, fix):
        agreed = ProviderTerms(
            base_url=consent.base_url,
            wire_format=consent.wire_format,
            key_id=consent.key_id,
            model=consent.model,
        )
        if folder_covered(consent.folder_scope, fix.folder) and terms_match(agreed, current):
            return OwnerLists(extra_excluded_paths=consent.extra_excluded_paths)
    return None


def source_request(
    *,
    model_input: Mapping[str, Any] | None,
    sent: list[SentFile],
    frames: list[dict[str, Any]],
    hint: str | None,
    round_number: int,
    retry_code: str | None,
) -> AIRequest:
    """Rung 3's request (FR15a, AC42): one trusted part of numbers and F3's facts, then each
    file and the hint in a fence of its own, drawn fresh for this call."""
    asking = may_ask(round_number)
    document = trusted_document(
        model_input=model_input,
        files=sent,
        frames=frames,
        round_number=round_number,
        retry_code=retry_code,
    )
    parts: list[Trusted | Untrusted] = [Trusted(canonical_json(document).decode())]
    parts += [
        Untrusted(label=file_label(each.ref), text=each.text, max_characters=MAX_SENT_BYTES)
        for each in sent
    ]
    if hint:
        parts.append(Untrusted(label=HINT_LABEL, text=hint, max_characters=HINT_MAX_CHARACTERS))
    return AIRequest(
        instructions=instructions(may_ask=asking),
        parts=tuple(parts),
        response_schema=response_schema(may_ask=asking),
        schema_name=SCHEMA_NAME,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        purpose="fix",
    )


def _pack_files(files: list[dict[str, Any]]) -> dict[str, PackFile]:
    return {
        str(each["ref"]): PackFile(
            ref=str(each["ref"]), path=str(each["path"]), content=str(each.get("content") or "")
        )
        for each in files
        if isinstance(each, dict) and isinstance(each.get("ref"), str)
    }


def allowed_evidence(model_input: Mapping[str, Any] | None, sent: list[SentFile]) -> set[str]:
    """U3: the ids an edit may cite. F3's facts' ids when an explanation was sent, else the
    files' numbers (`checks.parse_answer`)."""
    if model_input is None:
        return {each.ref for each in sent}
    facts = model_input.get("facts") or []
    return {str(fact["id"]) for fact in facts if isinstance(fact, Mapping) and "id" in fact}


def input_aliases(model_input: Mapping[str, Any] | None) -> AliasBook | None:
    """The stand-ins the input carried, each standing for itself: this room may not read
    the real names (F3 AC45), so a `why` may name only an alias it was given, and the API
    restores the real names at read."""
    if model_input is None:
        return None
    seen = set(ALIAS_TOKEN.findall(canonical_json(model_input).decode()))
    return AliasBook({"names": {alias: alias for alias in seen}})


def _failed(read: CallFailed) -> Ending:
    if read.retry_says is not None:
        return Ending(read.run, SourceEnd(None), read.retry_says, read.countdown)
    return Ending(read.run, SourceEnd(FAILED, read.reason))


def ending_of(
    verdict: Verdict, run_end: Callable[[str], store.RunEnd], *, may_retry: bool
) -> Ending:
    """Where a judged answer leaves the row (design, *Every sentence*)."""
    if isinstance(verdict, Proposed):
        end = SourceEnd(PROPOSED, edits=verdict.edits, evidence_ids=verdict.evidence_ids)
        return Ending(run_end("done"), end)
    if isinstance(verdict, NeedsFiles):
        asked = tuple(each.stored() for each in verdict.asked)
        return Ending(run_end("done"), SourceEnd(None, asked=asked))
    if isinstance(verdict, Abstained):
        return Ending(run_end("abstained"), SourceEnd(REFUSED, verdict.reason))
    if isinstance(verdict, Withheld):
        end = SourceEnd(REFUSED, verdict.reason, withheld_refs=verdict.refs)
        return Ending(run_end("invalid"), end)
    assert isinstance(verdict, RetryWith)
    if may_retry:
        keep = SourceEnd(None, retry_code=verdict.code)
        return Ending(run_end("invalid"), keep, RETRY_SAYS["invalid"], RETRY_WAIT_SECONDS)
    state = FAILED if verdict.final == INVALID_OUTPUT else REFUSED
    return Ending(run_end("invalid"), SourceEnd(state, verdict.final))


@task(PROPOSE_FIX_TASK)
def propose_fix_task(*, fix_id: str, org_id: str, sent_by: str | None = None) -> None:
    settings = get_settings()
    did = with_engine(
        lambda engine: propose(
            engine,
            fix_id=fix_id,
            org_id=org_id,
            sent_by=sent_by,
            keys=settings.ai_secret_keys,
            endpoints=settings.ai_endpoints,
            deployment=settings.nexovue_deployment,
            clock=lambda: datetime.now(UTC),
            attempt=registry.attempt(),
        )
    )
    logger.info("a suggestion job ended", fix_id=fix_id, org_id=org_id, did=did)
