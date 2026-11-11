"""A finding's explanation as the page and a token read it, and "Read the facts again"
(F3 design, *API*; plan T1.10).

**The cause is held until its bar is met** (FR50, AC92): a stored `likely_cause` is
shown only when `cause_released` says so, or the non-production override is set, which
production refuses to start with. Held, the API names no cause, no card line and no
shared count, and says it is held, so the header reads "The facts that line up with
this are below".

**A token reads the facts and never the words** (BR10, A68). Wording does not exist
before plan Step 5; the field is null for everyone and the state comes from the row.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final, Literal

from nexovue_platform.evidence import (
    BAD_WORDING,
    CAUSE_RULES,
    NOT_USEFUL,
    PERSON,
    RATING_REASONS,
    READ_AGAIN_EVERY,
    WRONG_CAUSE,
    cause_released,
)
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.findings import explanation_queries as queries
from nexovue_api.findings.queries import find_finding
from nexovue_api.findings.schemas import (
    ExplanationCause,
    ExplanationFact,
    ExplanationFactTarget,
    ExplanationFeedbackRequest,
    ExplanationRating,
    ExplanationSegment,
    FindingExplanation,
    RatingReason,
    ReadAgainAccepted,
    RuleCount,
    RuleCountsResponse,
    StoppedRule,
)

NOT_FOUND: Final = "No such finding."
NO_SUCH_VERSION: Final = "No such version of this finding's explanation."
TOO_SOON: Final = "The facts were read less than a minute ago."
# FR20's refusals: a reason must fit what the person was shown.
REASON_NEEDED: Final = "A 'No' needs a reason from the list."
NO_REASON_FOR_YES: Final = "A 'Yes' carries no reason."
NO_CAUSE_STATED: Final = "No likely cause was stated, so it cannot be wrong."
NO_WORDING_SHOWN: Final = "No AI wording was shown for this version."
NO_SUCH_RULE: Final = "No such rule."
# The wording states a row may hold, mapped again on read (A48): anything else is `failed`.
_WORDING_STATES: Final = frozenset(
    {"off", "pending", "done", "failed", "over_limit", "abstained", "held"}
)
_ROLES: Final = frozenset({"last_before", "near"})
_KINDS: Final = frozenset(
    {
        "deploy",
        "error_group",
        "dependency",
        "check",
        "incident",
        "other_finding",
        "traffic",
        "infrastructure",
        "absence",
    }
)
_BASES: Final = frozenset({"verified", "telemetry", "telemetry_text"})
# A fact about another record of nexovue's, linked by that record's id (FR16).
_LINKED_RECORDS: Final[dict[str, tuple[Literal["finding", "check", "incident"], str]]] = {
    "other_finding": ("finding", "finding_id"),
    "check": ("check", "check_id"),
    "incident": ("incident", "incident_id"),
}
# Where a fact's own moment is, per kind.
_MOMENT_KEYS: Final = ("at", "first_seen", "started_at", "opened_at")


async def get_explanation(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    finding_id: uuid.UUID,
    project_scope: uuid.UUID | None,
    number: int | None,
    show_held_causes: bool,
    now: datetime,
    person_id: uuid.UUID | None = None,
) -> FindingExplanation:
    """The latest version, or `?version=n`. A token scoped to another project gets the
    same 404 as a finding that does not exist (AC23). `person_id` is the signed-in
    person, whose own rating is carried (AC83); a token has none."""
    finding = await find_finding(session, org_id=org_id, finding_id=finding_id)
    if finding is None or (project_scope is not None and finding.project_id != project_scope):
        raise NotFoundError(NOT_FOUND)
    row = await queries.version(session, org_id=org_id, finding_id=finding_id, number=number)
    latest = await queries.latest_number(session, org_id=org_id, finding_id=finding_id)
    asked = await queries.last_read_again(session, org_id=org_id, finding_id=finding_id)
    next_read_at = _next_read_at(asked, now)
    if row is None:
        if number is not None:
            raise NotFoundError(NO_SUCH_VERSION)
        queued = await queries.read_is_queued(session, org_id=org_id, finding_id=finding_id)
        return FindingExplanation(
            finding_id=finding_id,
            state="reading" if queued else "not_read",
            version=None,
            latest_version=None,
            read_at=None,
            reads_failed=0,
            facts=[],
            likely_cause=None,
            cause_held=False,
            previous_cause=None,
            shared_cause_count=0,
            wording_state="off",
            next_read_at=next_read_at,
        )
    stored_cause: dict[str, Any] | None = row.likely_cause
    released = stored_cause is not None and cause_released(
        stored_cause.get("rule"), override=show_held_causes
    )
    cause = _cause(stored_cause, row.summary) if released and stored_cause else None
    shared = 0
    previous = None
    if cause is not None:
        shared = await queries.shared_cause_count(
            session, org_id=org_id, project_id=row.project_id, evidence_id=cause.evidence_id
        )
        previous = await _previous_cause(session, org_id, finding_id, row, cause.evidence_id)
    reasons = rating_reasons(cause_stated=cause is not None, wording_shown=_wording_shown(row))
    mine = None
    if person_id is not None:
        mine = await _carried_rating(
            session, org_id, finding_id, person_id, row, show_held_causes=show_held_causes
        )
    return FindingExplanation(
        finding_id=finding_id,
        state="read",
        version=row.version,
        latest_version=latest,
        read_at=row.read_at,
        reads_failed=row.reads_failed,
        facts=_facts(row.evidence, stored_cause if released else None),
        likely_cause=cause,
        cause_held=stored_cause is not None and not released,
        previous_cause=previous,
        shared_cause_count=shared,
        wording_state=row.wording_state if row.wording_state in _WORDING_STATES else "failed",
        next_read_at=next_read_at,
        rating_reasons=reasons if person_id is not None else [],
        my_rating=mine,
    )


async def read_again(
    session: AsyncSession, *, org_id: uuid.UUID, finding_id: uuid.UUID, now: datetime
) -> ReadAgainAccepted:
    """FR41, AC80: a new gather, AI on or off, at most once a minute per finding. Too
    soon is 409 with `next_read_at`, shown inline, never a toast. Costs no tokens."""
    finding = await find_finding(session, org_id=org_id, finding_id=finding_id)
    if finding is None:
        raise NotFoundError(NOT_FOUND)
    latest = await queries.lock_latest(session, org_id=org_id, finding_id=finding_id)
    if latest is not None:
        asked = await queries.last_read_again(session, org_id=org_id, finding_id=finding_id)
        waiting = _next_read_at(asked, now)
        if waiting is not None:
            raise ConflictError(TOO_SOON, next_read_at=waiting.isoformat())
        await queries.mark_read_again(
            session, org_id=org_id, finding_id=finding_id, number=latest.version, at=now
        )
    await queries.queue_read(session, org_id=org_id, finding_id=finding_id, reason=PERSON)
    await session.commit()
    return ReadAgainAccepted(queued=True, next_read_at=now + READ_AGAIN_EVERY)


def _next_read_at(asked: datetime | None, now: datetime) -> datetime | None:
    if asked is None or asked + READ_AGAIN_EVERY <= now:
        return None
    return asked + READ_AGAIN_EVERY


def _segments(raw: Any) -> list[ExplanationSegment]:
    """Stored segments, each kept to its one known key. Anything else is dropped."""
    kept: list[ExplanationSegment] = []
    for segment in raw or []:
        if not isinstance(segment, dict):
            continue
        if isinstance(segment.get("text"), str):
            kept.append(ExplanationSegment(text=segment["text"]))
        elif isinstance(segment.get("name"), str):
            kept.append(ExplanationSegment(name=segment["name"]))
        elif isinstance(segment.get("at"), str):
            kept.append(ExplanationSegment(at=datetime.fromisoformat(segment["at"])))
    return kept


def _cause(stored: dict[str, Any], summary: dict[str, Any]) -> ExplanationCause | None:
    if stored.get("strength") not in ("likely", "possible") or stored.get("basis") not in _BASES:
        return None
    return ExplanationCause(
        evidence_id=str(stored["evidence_id"]),
        rule=str(stored["rule"]),
        strength=stored["strength"],
        basis=stored["basis"],
        same_minute=bool(stored.get("same_minute")),
        header=_segments(summary.get("header")),
        card=_segments(summary.get("card")),
    )


def _facts(evidence: dict[str, Any], cause: dict[str, Any] | None) -> list[ExplanationFact]:
    leading = None if cause is None else cause.get("evidence_id")
    support = set() if cause is None else set(cause.get("supported_by") or [])
    facts: list[ExplanationFact] = []
    for item in evidence.get("items", []):
        if item.get("kind") not in _KINDS:
            continue
        values = item.get("values") or {}
        moment = next((values[key] for key in _MOMENT_KEYS if values.get(key)), None)
        facts.append(
            ExplanationFact(
                id=str(item["id"]),
                number=len(facts) + 1,
                kind=item["kind"],
                basis=item.get("basis") if item.get("basis") in _BASES else None,
                sentence=_segments(item.get("sentence")),
                at=None if moment is None else datetime.fromisoformat(moment),
                read_at=None
                if not item.get("read_at")
                else datetime.fromisoformat(item["read_at"]),
                role=item.get("role") if item.get("role") in _ROLES else None,
                absence=values.get("reason") if item["kind"] == "absence" else None,
                read=values.get("read") if item["kind"] == "absence" else None,
                is_cause=item["id"] == leading,
                supports_cause=item["id"] in support,
                target=_target(item["kind"], values),
            )
        )
    return facts


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _target(kind: str, values: dict[str, Any]) -> ExplanationFactTarget | None:
    """FR16: what a fact's link needs, read from its frozen values. None for a kind with
    no data to open (traffic, an absence) or a value that is not the expected shape."""
    if kind == "error_group" and _text(values.get("group_id")):
        services = values.get("services")
        return ExplanationFactTarget(
            kind="error_group",
            group_id=values["group_id"],
            services=[each for each in services if isinstance(each, str)]
            if isinstance(services, list)
            else [],
        )
    if kind == "dependency" and _text(values.get("callee")):
        return ExplanationFactTarget(
            kind="dependency",
            caller=_text(values.get("caller")),
            callee=values["callee"],
            callee_attr=_text(values.get("callee_attr")),
            failure_share_before=_number(values.get("failure_share_before")),
            failure_share_during=_number(values.get("failure_share_during")),
            p95_before=_number(values.get("p95_before")),
            p95_during=_number(values.get("p95_during")),
        )
    if kind == "deploy":
        return ExplanationFactTarget(kind="deploy", release_id=_text(values.get("release_id")))
    if kind == "infrastructure" and _text(values.get("host")):
        return ExplanationFactTarget(kind="host")
    if kind in _LINKED_RECORDS:
        target, key = _LINKED_RECORDS[kind]
        if _text(values.get(key)):
            return ExplanationFactTarget(kind=target, id=values[key])
    return None


async def _previous_cause(
    session: AsyncSession,
    org_id: uuid.UUID,
    finding_id: uuid.UUID,
    row: Any,
    evidence_id: str,
) -> list[ExplanationSegment] | None:
    """FR3: when the cause changed since the version before, what it was."""
    if row.version <= 1:
        return None
    before = await queries.version(
        session, org_id=org_id, finding_id=finding_id, number=row.version - 1
    )
    if before is None or before.likely_cause is None:
        return None
    if before.likely_cause.get("evidence_id") == evidence_id:
        return None
    return _segments((before.summary or {}).get("header"))


# ------------------------------------------------------------- ratings (FR20)


def rating_reasons(*, cause_stated: bool, wording_shown: bool) -> list[RatingReason]:
    """The reasons a "No" may give: "wrong cause" only when a cause was stated, "poor
    wording" only when wording was shown (AC75)."""
    reasons: list[RatingReason] = []
    if cause_stated:
        reasons.append(WRONG_CAUSE)
    reasons.append(NOT_USEFUL)
    if wording_shown:
        reasons.append(BAD_WORDING)
    return reasons


async def _carried_rating(
    session: AsyncSession,
    org_id: uuid.UUID,
    finding_id: uuid.UUID,
    person_id: uuid.UUID,
    row: Any,
    *,
    show_held_causes: bool,
) -> ExplanationRating | None:
    """AC83: this person's newest rating up to this version, carried while the likely
    cause it rated is the one this version states."""
    shown = _stated_cause_id(row.likely_cause, show_held_causes=show_held_causes)
    for rated in await queries.ratings_of(
        session, org_id=org_id, finding_id=finding_id, user_id=person_id
    ):
        if rated.version > row.version:
            continue
        if _stated_cause_id(rated.likely_cause, show_held_causes=show_held_causes) != shown:
            return None
        return ExplanationRating(version=rated.version, helpful=rated.helpful, reason=rated.reason)
    return None


def _wording_shown(row: Any) -> bool:
    """Checked words were stored for this version, so a person saw them (plan Step 5)."""
    return row.wording_state == "done" and row.wording is not None


def _stated_cause_id(stored: dict[str, Any] | None, *, show_held_causes: bool) -> str | None:
    """The cause a person was shown: a stored one only once its rule is released."""
    if stored is None or not cause_released(stored.get("rule"), override=show_held_causes):
        return None
    return str(stored.get("evidence_id"))


async def rate(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    finding_id: uuid.UUID,
    person_id: uuid.UUID,
    request: ExplanationFeedbackRequest,
    show_held_causes: bool,
    now: datetime,
) -> ExplanationRating:
    """FR20: one rating per person per version, a reason only on "No", and only a
    reason that fits what was shown (AC75). Audited."""
    finding = await find_finding(session, org_id=org_id, finding_id=finding_id)
    if finding is None:
        raise NotFoundError(NOT_FOUND)
    row = await queries.version(
        session, org_id=org_id, finding_id=finding_id, number=request.version
    )
    if row is None:
        raise NotFoundError(NO_SUCH_VERSION)
    if request.helpful and request.reason is not None:
        raise InvalidRequestError(NO_REASON_FOR_YES)
    if not request.helpful and request.reason is None:
        raise InvalidRequestError(REASON_NEEDED)
    stated = _stated_cause_id(row.likely_cause, show_held_causes=show_held_causes) is not None
    if request.reason == WRONG_CAUSE and not stated:
        raise InvalidRequestError(NO_CAUSE_STATED)
    if request.reason == BAD_WORDING and not _wording_shown(row):
        raise InvalidRequestError(NO_WORDING_SHOWN)
    await queries.save_rating(
        session,
        org_id=org_id,
        explanation_id=row.id,
        user_id=person_id,
        helpful=request.helpful,
        reason=request.reason,
        at=now,
    )
    audit.record_explanation_rated(
        session,
        org_id=org_id,
        finding_id=finding_id,
        actor_user_id=person_id,
        version=row.version,
        helpful=request.helpful,
        reason=request.reason,
    )
    await session.commit()
    return ExplanationRating(version=row.version, helpful=request.helpful, reason=request.reason)


# ------------------------------------------------- rules on /settings/explanations


async def rule_counts(
    session: AsyncSession, *, org_id: uuid.UUID, show_held_causes: bool
) -> RuleCountsResponse:
    """FR54: per rule, the findings it names a cause for and what people said; counts
    only, never a name. A held cause is counted as no cause, as the page shows it."""
    stopped = await queries.stopped_rules(session, org_id=org_id)
    named = dict.fromkeys(CAUSE_RULES, 0)
    for stored in await queries.latest_causes_in_organization(session, org_id=org_id):
        rule = _stated_rule(stored, show_held_causes=show_held_causes)
        if rule in named:
            named[rule] += 1
    said = {rule: dict.fromkeys(("useful", *RATING_REASONS), 0) for rule in CAUSE_RULES}
    without = {"useful": 0, "not_useful": 0}
    for rated in await queries.ratings_in_organization(session, org_id=org_id):
        rule = _stated_rule(rated.likely_cause, show_held_causes=show_held_causes)
        bucket = "useful" if rated.helpful else str(rated.reason)
        if rule in said:
            said[rule][bucket] = said[rule].get(bucket, 0) + 1
        else:
            without["useful" if rated.helpful else "not_useful"] += 1
    return RuleCountsResponse(
        rules=[
            RuleCount(
                rule=rule,
                named=named[rule],
                useful=said[rule]["useful"],
                wrong_cause=said[rule][WRONG_CAUSE],
                not_useful=said[rule][NOT_USEFUL],
                bad_wording=said[rule][BAD_WORDING],
                stopped=rule in stopped,
            )
            for rule in CAUSE_RULES
        ],
        without_cause_useful=without["useful"],
        without_cause_not_useful=without["not_useful"],
    )


def _stated_rule(stored: dict[str, Any] | None, *, show_held_causes: bool) -> str | None:
    if stored is None or not cause_released(stored.get("rule"), override=show_held_causes):
        return None
    return str(stored.get("rule"))


async def set_rule_stopped(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    person_id: uuid.UUID,
    rule: str,
    stopped: bool,
    now: datetime,
) -> StoppedRule:
    """FR54: a rule stops naming causes in this organization, or starts again. Its facts
    stay either way. An audit row when the state changed."""
    if rule not in CAUSE_RULES:
        raise NotFoundError(NO_SUCH_RULE)
    if stopped:
        changed = await queries.stop_rule(
            session, org_id=org_id, rule=rule, user_id=person_id, at=now
        )
    else:
        changed = await queries.resume_rule(session, org_id=org_id, rule=rule)
    if changed:
        audit.record_explanation_rule(
            session, org_id=org_id, actor_user_id=person_id, rule=rule, stopped=stopped
        )
    await session.commit()
    return StoppedRule(rule=rule, stopped=stopped)


def card_line(
    stored_cause: dict[str, Any] | None, summary: dict[str, Any], *, show_held_causes: bool
) -> list[ExplanationSegment] | None:
    """The feed card's cause line (FR18), or None with no cause or while it is held."""
    if stored_cause is None or not cause_released(
        stored_cause.get("rule"), override=show_held_causes
    ):
        return None
    return _segments(summary.get("card")) or None
