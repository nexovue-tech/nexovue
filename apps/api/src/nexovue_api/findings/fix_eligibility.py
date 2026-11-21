"""FR9's ordered reasons, rung 3's result, and FR31's gate for rung 2b (spec FR9, FR31,
D66, D67; plan T2b.7, T3.7). Pure: the reader gathers the facts, this decides.

The first reason that applies, in FR9's order, stops the list. With none, rung 3 is
**ready** (an error group is linked) or **ready without a stack** (a person names files).

**Rung 2b shows only at a code-access reason** (D67): `no_code_location`,
`no_read_permission` or `no_consent`. Where rung 3 is ready, only rung 3 is offered. Rung 2b
then needs F3's facts to be sendable and to hold a cause or an `error_N` alias (D66):
otherwise nothing about it is shown, not even a refusal.

`cause_is_elsewhere` (row 7) stops nobody from naming files (FR9): rung 3 stays open to
typed paths when no later reason applies. Row 13 (`draft_open`) comes with Step 7.

Imports no AI code, so a later rung 1 or 2 read may ask it too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from nexovue_platform.evidence import RULE_DEPENDENCY_FINDING
from nexovue_platform.fixes.records import EVIDENCE_PENDING_FOR, TRIES_PER_FINDING

Reason = Literal[
    "hosted_ai_not_offered",
    "ai_off",
    "project_opted_out",
    "project_finding",
    "closed_as_not_a_problem",
    "evidence_pending",
    "cause_is_elsewhere",
    "no_code_location",
    "no_read_permission",
    "no_consent",
    "limit_reached",
    "already_running",
]
SourceResult = Literal["ready", "ready_without_stack"]
# FR9, row 5: closed as not a problem, so there is nothing to change.
NOT_A_PROBLEM: Final = frozenset({"false_alarm", "new_normal", "adopted", "service_gone"})
# D67: the reasons rung 2b may stand under.
CODE_ACCESS_REASONS: Final = frozenset({"no_code_location", "no_read_permission", "no_consent"})


@dataclass(frozen=True, slots=True)
class EligibilityFacts:
    """What the reader found, each as Nexovue's own value."""

    hosted: bool
    ai_on: bool
    opted_out: bool
    project_scope: bool
    closed_reason: str | None
    opened_at: datetime
    error_group_linked: bool
    # The stored likely cause's rule, only once its rule is released (F3 FR50).
    released_cause_rule: str | None
    # F3's latest version holds `model_input`, and it may be sent (F3 FR50, A85).
    sendable_facts: bool
    # The facts hold a released likely cause or an `error_N` alias (D66).
    cause_or_error_alias: bool
    tokens_left: bool
    tries_used: int
    in_flight: bool
    # FR9 rows 8 to 10 (Step 3): the service's code location, the Fix App's read of its
    # repository, and a live consent covering its folder.
    code_location: bool = False
    read_permission: bool = False
    consent_live: bool = False


@dataclass(frozen=True, slots=True)
class Eligibility:
    """`reason` is FR9's first reason, or None. `offered` is FR31: show **Suggest a general
    fix**. Both empty means nothing about rung 2b is shown."""

    reason: Reason | None
    offered: bool


@dataclass(frozen=True, slots=True)
class SourceEligibility:
    """Rung 3: FR9's first reason, and what may be asked. `result` is set when **Ask** may
    show: `ready` from the error's stack, `ready_without_stack` from typed paths."""

    reason: Reason | None
    result: SourceResult | None


NOTHING: Final = Eligibility(reason=None, offered=False)


def first_reason(facts: EligibilityFacts, *, now: datetime) -> Reason | None:
    """FR9's list, in order, to the first that applies (row 13 comes with Step 7)."""
    return _stopping_reason(facts, now=now) or _access_reason(facts) or _limit_reason(facts)


def general_eligibility(facts: EligibilityFacts, *, now: datetime) -> Eligibility:
    """FR31: rung 2b only where rung 3 stops for want of code access (D67)."""
    reason = first_reason(facts, now=now)
    if reason not in CODE_ACCESS_REASONS or not (
        facts.sendable_facts and facts.cause_or_error_alias
    ):
        return Eligibility(reason=reason, offered=False)
    limit = _limit_reason(facts)
    if limit is not None:
        return Eligibility(reason=limit, offered=False)
    return Eligibility(reason=reason, offered=True)


def source_eligibility(facts: EligibilityFacts, *, now: datetime) -> SourceEligibility:
    """Rung 3: ready, ready without a stack, or the reason it is not."""
    reason = first_reason(facts, now=now)
    if reason == "cause_is_elsewhere":
        # FR9 row 7: a person may still name files here, unless a later reason applies.
        blocked = _access_reason(facts) or _limit_reason(facts)
        return SourceEligibility(
            reason=blocked or reason, result=None if blocked else "ready_without_stack"
        )
    if reason is not None:
        return SourceEligibility(reason=reason, result=None)
    return SourceEligibility(
        reason=None, result="ready" if facts.error_group_linked else "ready_without_stack"
    )


def _stopping_reason(facts: EligibilityFacts, *, now: datetime) -> Reason | None:
    if facts.hosted:
        return "hosted_ai_not_offered"
    if not facts.ai_on:
        return "ai_off"
    if facts.opted_out:
        return "project_opted_out"
    if facts.project_scope:
        return "project_finding"
    if facts.closed_reason in NOT_A_PROBLEM:
        return "closed_as_not_a_problem"
    if not facts.error_group_linked and now - facts.opened_at < EVIDENCE_PENDING_FOR:
        return "evidence_pending"
    if facts.released_cause_rule == RULE_DEPENDENCY_FINDING:
        return "cause_is_elsewhere"
    return None


def _access_reason(facts: EligibilityFacts) -> Reason | None:
    if not facts.code_location:
        return "no_code_location"
    if not facts.read_permission:
        return "no_read_permission"
    if not facts.consent_live:
        return "no_consent"
    return None


def _limit_reason(facts: EligibilityFacts) -> Reason | None:
    if not facts.tokens_left or facts.tries_used >= TRIES_PER_FINDING:
        return "limit_reached"
    if facts.in_flight:
        return "already_running"
    return None
