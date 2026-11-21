"""Rung 2b on a finding: whether it is offered, the click, and the read (spec FR31 to FR38;
design, *Rung 2b*, *State by role*; plan T2b.7).

**Its own path, apart from rungs 1 and 2** (founder, 2026-11-21): this module reads the AI
connection and F3's limits, which are AI code, so `GET /findings/{id}/fixes` never imports
it and keeps working with AI off, stopped or failing (AC33).

**Nothing the AI role wrote is believed** (D18): a stored suggestion is checked again,
whole, against G1 to G9 and the `model_input` it was written from, on every read; one that
fails shows nothing and raises no alert (AC71). Aliases are restored as segments through
the one restorer F3 reads with, so restoring can never build a link.

**Who sees what** (FR32, FR37): a person who may ask gets the requester's sentences and the
offer; a viewer and a support view read the viewer's sentences and a proposed suggestion,
which holds no source. `ai_off` is said only to those who can set AI up (FR10). A token
reaches none of this: the routes take a person's identity.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, cast

from nexovue_platform.ai.connection import provider_host
from nexovue_platform.ai.limits import first_of_next_month, fix_ceiling, month_of
from nexovue_platform.deployment import HOSTED
from nexovue_platform.evidence import KINDS, cause_released, display_name, facts_may_be_sent
from nexovue_platform.evidence.aliases import AliasBook, restored_segments
from nexovue_platform.fixes.general_rules import GeneralRefused, GeneralSuggestion, check_general
from nexovue_platform.fixes.records import (
    FAILED,
    GENERAL_LANGUAGES,
    KIND_GENERAL,
    PROPOSED,
    PROPOSING,
    TRIES_PER_FINDING,
    TRIES_WINDOW,
    GeneralLanguage,
    suggest_general_fix_request,
)
from nexovue_platform.fixes.sentences import (
    ELIGIBILITY_SENTENCES,
    GENERAL_ACTION,
    GENERAL_EARLIER_READING,
    GENERAL_HAS_NO_CODE,
    GENERAL_HEADING,
    GENERAL_LABEL,
    GENERAL_LEAD,
    GENERAL_NONE_HEADING,
    GENERAL_OFFER_LEAD,
    GENERAL_PROPOSING_REQUESTER,
    GENERAL_PROPOSING_VIEWER,
    GENERAL_SEND,
    NO_CODE_LOCATION_YET,
    REASON_SENTENCES,
    SECTION_HEADING_PROPOSING,
    SECTION_HEADING_UNAVAILABLE,
    FixSentence,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.ai import queries as ai_queries
from nexovue_api.ai import service as ai_service
from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity
from nexovue_api.auth.permissions import Action, may
from nexovue_api.data.postgres.models import AIConnection, FindingExplanation, FindingFix
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.errors.exceptions import ConflictError, NotFoundError
from nexovue_api.findings import explanation_queries, fix_queries
from nexovue_api.findings.fix_eligibility import (
    Eligibility,
    EligibilityFacts,
    general_eligibility,
)
from nexovue_api.findings.fix_schemas import (
    EarlierGeneral,
    FactReference,
    FindingGeneralFix,
    FixSectionLine,
    GeneralContent,
    GeneralFix,
    GeneralOffer,
    GeneralState,
)
from nexovue_api.findings.queries import find_finding, lock_finding
from nexovue_api.findings.schemas import ExplanationSegment
from nexovue_api.settings import Settings

NOT_FOUND: Final = "No such finding."
NO_SUCH_FIX: Final = "No such suggestion for this finding."
NOT_OFFERED: Final = "A general suggestion is not offered for this finding now."
# A click that lost the one-in-flight index to another (BR8).
ALREADY_RUNNING_CODE: Final = "already_running"
NOT_OFFERED_CODE: Final = "not_offered"
GENERAL_HAS_NO_CODE_CODE: Final = "general_has_no_code"


@dataclass(frozen=True, slots=True)
class Viewer:
    """Who reads: the shape follows the role, and a support view always reads as a viewer."""

    may_request: bool
    may_set_up_ai: bool

    @staticmethod
    def of(identity: Identity) -> Viewer:
        viewed = identity.impersonation is not None
        role = identity.user.role
        return Viewer(
            may_request=not viewed and may(role, Action.REQUEST_FIX),
            may_set_up_ai=not viewed and may(role, Action.MANAGE_AI),
        )

    def sentence(self, of: FixSentence) -> str | None:
        return of.requester if self.may_request else of.viewer


@dataclass(frozen=True, slots=True)
class _State:
    """Everything one read or click decides from, read once."""

    finding: FindingRow
    explanation: FindingExplanation | None
    connection: AIConnection | None
    eligibility: Eligibility
    tries_used: list[datetime]
    tokens_out: bool
    now: datetime


# ------------------------------------------------------------------ the read


async def read_general(
    session: AsyncSession,
    *,
    identity: Identity,
    finding_id: uuid.UUID,
    settings: Settings,
    now: datetime,
) -> FindingGeneralFix:
    """`GET /findings/{id}/fixes/general`. Another organization's finding is a 404."""
    org_id = identity.org_id
    finding = await find_finding(session, org_id=org_id, finding_id=finding_id)
    if finding is None:
        raise NotFoundError(NOT_FOUND)
    state = await _state(session, finding, settings=settings, now=now)
    viewer = Viewer.of(identity)
    rows = await fix_queries.recent_fixes(session, org_id=org_id, finding_id=finding_id)
    latest = rows[0] if rows else None
    latest_version = None if state.explanation is None else state.explanation.version
    return FindingGeneralFix(
        finding_id=finding.id,
        line=_line(state, viewer, latest),
        offer=_offer(state, viewer),
        latest=None
        if latest is None
        else await _view(session, latest, viewer, latest_version=latest_version),
        earlier=[
            EarlierGeneral(id=row.id, state=_known_state(row.state), requested_at=row.requested_at)
            for row in rows[1:]
        ],
    )


async def _state(
    session: AsyncSession, finding: FindingRow, *, settings: Settings, now: datetime
) -> _State:
    org_id = finding.org_id
    explanation = await explanation_queries.version(
        session, org_id=org_id, finding_id=finding.id, number=None
    )
    hosted = settings.nexovue_deployment == HOSTED
    connection = (
        None
        if hosted
        else await ai_service.connection_in_use(
            session, org_id=org_id, availability=ai_service.availability_of(settings)
        )
    )
    opted_out = await ai_queries.opted_out(session, org_id=org_id, project_id=finding.project_id)
    tries = await fix_queries.counted_since(
        session, org_id=org_id, finding_id=finding.id, since=now - TRIES_WINDOW
    )
    tokens_out = await _tokens_out(session, connection, now=now)
    likely_cause = None if explanation is None else explanation.likely_cause
    released_rule = _released_rule(likely_cause, override=settings.show_held_causes)
    model_input = None if explanation is None else explanation.model_input
    facts = EligibilityFacts(
        hosted=hosted,
        ai_on=connection is not None,
        opted_out=opted_out,
        project_scope=finding.service_name is None,
        closed_reason=finding.closed_reason,
        opened_at=finding.started_at,
        error_group_linked=_has_error_group(explanation),
        released_cause_rule=released_rule,
        sendable_facts=model_input is not None
        and facts_may_be_sent(likely_cause, override=settings.show_held_causes),
        cause_or_error_alias=released_rule is not None or _names_an_error(model_input),
        tokens_left=not tokens_out,
        tries_used=len(tries),
        in_flight=await fix_queries.in_flight(session, org_id=org_id, finding_id=finding.id),
    )
    return _State(
        finding=finding,
        explanation=explanation,
        connection=connection,
        eligibility=general_eligibility(facts, now=now),
        tries_used=tries,
        tokens_out=tokens_out,
        now=now,
    )


async def _tokens_out(
    session: AsyncSession, connection: AIConnection | None, *, now: datetime
) -> bool:
    """D32: the organization's used and reserved tokens have reached F5's share."""
    if connection is None or connection.monthly_token_limit is None:
        return False
    usage = await ai_queries.usage_month(session, org_id=connection.org_id, month=month_of(now))
    spent = 0 if usage is None else usage.used_tokens + usage.reserved_tokens
    return spent >= fix_ceiling(connection.monthly_token_limit)


def _released_rule(likely_cause: Mapping[str, Any] | None, *, override: bool) -> str | None:
    rule = None if likely_cause is None else likely_cause.get("rule")
    return rule if isinstance(rule, str) and cause_released(rule, override=override) else None


def _has_error_group(explanation: FindingExplanation | None) -> bool:
    items = [] if explanation is None else explanation.evidence.get("items") or []
    return any(isinstance(item, Mapping) and item.get("kind") == "error_group" for item in items)


def _names_an_error(model_input: Mapping[str, Any] | None) -> bool:
    """D66: an `error_group` fact carrying an `error_N` alias, which exists only when F3's
    identifier pattern passed."""
    facts = [] if model_input is None else model_input.get("facts") or []
    return any(
        isinstance(fact, Mapping)
        and fact.get("kind") == "error_group"
        and isinstance(fact.get("error"), str)
        for fact in facts
    )


def _line(state: _State, viewer: Viewer, latest: FindingFix | None) -> FixSectionLine | None:
    """The section's line: a reason's sentence, or the line the offer stands under."""
    eligibility = state.eligibility
    if eligibility.offered:
        return FixSectionLine(heading=SECTION_HEADING_UNAVAILABLE, sentence=NO_CODE_LOCATION_YET)
    reason = eligibility.reason
    if reason is None or reason == "hosted_ai_not_offered":
        return None
    if reason == "ai_off" and not viewer.may_set_up_ai:
        return None
    if reason == "already_running" and latest is not None and latest.state == PROPOSING:
        return None  # The latest row says it, with its own state.
    sentence = viewer.sentence(ELIGIBILITY_SENTENCES[reason])
    if sentence is None:
        return None
    return FixSectionLine(
        heading=SECTION_HEADING_UNAVAILABLE,
        sentence=sentence.format(
            date=_limit_lifts(state).date().isoformat(), dependency=_dependency(state)
        ),
    )


def _limit_lifts(state: _State) -> datetime:
    """When the next suggestion may be asked: the month's reset when F5's share is spent,
    else when the oldest counted try leaves the day's window."""
    if state.tokens_out or len(state.tries_used) < TRIES_PER_FINDING:
        resets = first_of_next_month(month_of(state.now))
        return datetime(resets.year, resets.month, resets.day, tzinfo=UTC)
    return sorted(state.tries_used)[-TRIES_PER_FINDING] + TRIES_WINDOW


def _dependency(state: _State) -> str:
    """The dependency whose own earlier finding is the likely cause, by its shown name."""
    explanation = state.explanation
    cause = None if explanation is None else explanation.likely_cause
    leading = None if cause is None else cause.get("evidence_id")
    for item in [] if explanation is None else explanation.evidence.get("items") or []:
        if isinstance(item, Mapping) and item.get("id") == leading:
            service = (item.get("values") or {}).get("service")
            if isinstance(service, str):
                return display_name(service)
    return "Another service"


def _offer(state: _State, viewer: Viewer) -> GeneralOffer | None:
    if not (state.eligibility.offered and viewer.may_request and state.connection is not None):
        return None
    return GeneralOffer(
        action=GENERAL_ACTION,
        lead=GENERAL_OFFER_LEAD,
        send_sentence=GENERAL_SEND.format(host=_host(state.connection)),
        languages=list(GENERAL_LANGUAGES),
        preselected=None,
        tries_left=max(0, TRIES_PER_FINDING - len(state.tries_used)),
    )


def _host(connection: AIConnection) -> str:
    """The provider's host alone, as FR33's sentence names it."""
    return provider_host(connection.base_url).split("://", 1)[-1]


# ------------------------------------------------------------------ one row


async def _view(
    session: AsyncSession, row: FindingFix, viewer: Viewer, *, latest_version: int | None
) -> GeneralFix:
    state = _known_state(row.state)
    if state == PROPOSING:
        sentence: str | None = (
            GENERAL_PROPOSING_REQUESTER if viewer.may_request else GENERAL_PROPOSING_VIEWER
        )
        return _row(row, state, SECTION_HEADING_PROPOSING, sentence, None)
    if state == PROPOSED:
        checked = await _checked(session, row, latest_version=latest_version)
        if isinstance(checked, GeneralContent):
            return _row(row, state, GENERAL_HEADING, None, checked)
        reason = checked
    else:
        reason = row.reason if row.reason in REASON_SENTENCES else "nexovue_error"
    return _row(row, state, GENERAL_NONE_HEADING, viewer.sentence(REASON_SENTENCES[reason]), None)


def _row(
    row: FindingFix,
    state: GeneralState,
    heading: str,
    sentence: str | None,
    content: GeneralContent | None,
) -> GeneralFix:
    return GeneralFix(
        id=row.id,
        state=state,
        heading=heading,
        sentence=sentence,
        content=content,
        tokens_used=row.tokens_used,
        requested_at=row.requested_at,
        finished_at=row.finished_at,
    )


def _known_state(value: str) -> GeneralState:
    """A state outside rung 2b's list is read as `failed` (A48's rule for stored words)."""
    return cast(GeneralState, value) if value in (PROPOSING, PROPOSED, "refused") else FAILED


async def _checked(
    session: AsyncSession, row: FindingFix, *, latest_version: int | None
) -> GeneralContent | str:
    """G1 to G9 again, against the version it was written from; the content, or the reason
    it now shows nothing (AC71). Never an alert, never a state change."""
    source = await explanation_queries.version(
        session, org_id=row.org_id, finding_id=row.finding_id, number=row.explanation_version
    )
    if (
        source is None
        or source.model_input is None
        or row.general_language not in GENERAL_LANGUAGES
    ):
        return "nexovue_error"
    language = row.general_language
    verdict = check_general(
        row.general_suggestion, language=language, model_input=source.model_input
    )
    if isinstance(verdict, GeneralRefused):
        return verdict.reason
    if not isinstance(verdict, GeneralSuggestion):
        return "invalid_output"
    book = AliasBook(source.aliases)
    return GeneralContent(
        heading=GENERAL_HEADING,
        label=GENERAL_LABEL,
        lead=GENERAL_LEAD.format(language=language),
        summary=_segments(verdict.summary, book),
        steps=[_segments(step, book) for step in verdict.steps],
        language=language,
        code=verdict.code,
        evidence=_references(source, verdict.evidence_ids, book),
        earlier_reading=GENERAL_EARLIER_READING
        if latest_version is not None and row.explanation_version < latest_version
        else None,
    )


def _segments(text: str, book: AliasBook) -> list[ExplanationSegment]:
    return [ExplanationSegment(**segment) for segment in restored_segments(text, book)]


def _references(
    source: FindingExplanation, fact_ids: Sequence[str], book: AliasBook
) -> list[FactReference]:
    """Each fact the answer rests on, numbered as the explanation numbers its facts."""
    numbers: dict[str, int] = {}
    for item in source.evidence.get("items") or []:
        if isinstance(item, Mapping) and item.get("kind") in KINDS:
            numbers[str(item.get("id"))] = len(numbers) + 1
    references = []
    for fact_id in fact_ids:
        evidence_id = book.evidence_of(fact_id)
        if evidence_id is not None and evidence_id in numbers:
            references.append(FactReference(evidence_id=evidence_id, number=numbers[evidence_id]))
    return references


# ------------------------------------------------------------------ the click


async def ask_general(
    session: AsyncSession,
    *,
    identity: Identity,
    finding_id: uuid.UUID,
    language: GeneralLanguage,
    settings: Settings,
    now: datetime,
) -> FindingGeneralFix:
    """`POST /findings/{id}/fixes/general` (design, *The flow* step 1): FR31 decided again
    under the finding's lock; then, in one transaction, the row in `proposing`, the audit
    entry naming the person and the provider host, and the job with ids only. Refused with
    409 and the reason's code when it is not offered (FR31, AC67)."""
    org_id = identity.org_id
    finding = await lock_finding(session, org_id=org_id, finding_id=finding_id)
    if finding is None:
        raise NotFoundError(NOT_FOUND)
    state = await _state(session, finding, settings=settings, now=now)
    if not state.eligibility.offered:
        code = state.eligibility.reason or NOT_OFFERED_CODE
        raise ConflictError(NOT_OFFERED, code=code)
    assert state.explanation is not None and state.connection is not None
    row = fix_queries.new_general(
        org_id=org_id,
        project_id=finding.project_id,
        finding_id=finding.id,
        explanation_version=state.explanation.version,
        language=language,
        requested_by=identity.user.id,
        at=now,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ConflictError(NOT_OFFERED, code=ALREADY_RUNNING_CODE) from None
    audit.record_ai_event(
        session,
        org_id=org_id,
        actor_user_id=identity.user.id,
        action=audit.FIX_GENERAL_REQUESTED,
        target_id=finding.id,
        target_kind=audit.FINDING_TARGET,
        detail={
            "fix_id": str(row.id),
            "provider_host": provider_host(state.connection.base_url),
            "language": language,
        },
    )
    await fix_queries.queue_suggestion(
        session,
        suggest_general_fix_request(
            fix_id=row.id,
            org_id=org_id,
            requested_by=identity.user.id,
            call_seconds=state.connection.call_seconds,
        ),
    )
    await session.commit()
    return await read_general(
        session, identity=identity, finding_id=finding_id, settings=settings, now=now
    )


async def refuse_code_action(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID, fix_id: uuid.UUID
) -> None:
    """AC73: a general row has no code, so a check, a patch, a draft and CI are refused with
    a named code. Step 3 builds these actions for source rows; until then there are none."""
    row = await fix_queries.fix_of(session, org_id=org_id, finding_id=finding_id, fix_id=fix_id)
    if row is None or row.kind != KIND_GENERAL:
        raise NotFoundError(NO_SUCH_FIX)
    raise ConflictError(GENERAL_HAS_NO_CODE, code=GENERAL_HAS_NO_CODE_CODE)
