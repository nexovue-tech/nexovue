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
from typing import Any, Final

from nexovue_platform.evidence import PERSON, READ_AGAIN_EVERY, cause_released
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.errors.exceptions import ConflictError, NotFoundError
from nexovue_api.findings import explanation_queries as queries
from nexovue_api.findings.queries import find_finding
from nexovue_api.findings.schemas import (
    ExplanationCause,
    ExplanationFact,
    ExplanationSegment,
    FindingExplanation,
    ReadAgainAccepted,
)

NOT_FOUND: Final = "No such finding."
NO_SUCH_VERSION: Final = "No such version of this finding's explanation."
TOO_SOON: Final = "The facts were read less than a minute ago."
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
) -> FindingExplanation:
    """The latest version, or `?version=n`. A token scoped to another project gets the
    same 404 as a finding that does not exist (AC23)."""
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
            )
        )
    return facts


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


def card_line(
    stored_cause: dict[str, Any] | None, summary: dict[str, Any], *, show_held_causes: bool
) -> list[ExplanationSegment] | None:
    """The feed card's cause line (FR18), or None with no cause or while it is held."""
    if stored_cause is None or not cause_released(
        stored_cause.get("rule"), override=show_held_causes
    ):
        return None
    return _segments(summary.get("card")) or None
