"""FR9's ordered reasons and FR31's gate for rung 2b: the first version (spec FR9, FR31,
D66, D67; plan T2b.7). Pure: the reader gathers the facts, this decides.

The first reason that applies, in FR9's order, stops the list. **Before Step 3 no code
location exists** (`service_code_locations` is Step 3's table), so every service finding
reaches FR9's `no_code_location`, the one place rung 2b may be offered (D67); T3.7 extends
this list with reasons 8 to 10 and 13 and the reach table. Rung 2b then needs F3's facts to
be sendable and to hold a cause or an `error_N` alias (D66): otherwise nothing about it is
shown, not even a refusal, because nothing could change it now.

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
    "limit_reached",
    "already_running",
]
# FR9, row 5: closed as not a problem, so there is nothing to change.
NOT_A_PROBLEM: Final = frozenset({"false_alarm", "new_normal", "adopted", "service_gone"})


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


@dataclass(frozen=True, slots=True)
class Eligibility:
    """`reason` is FR9's first reason, or None. `offered` is FR31: show **Suggest a general
    fix**. Both empty means nothing about rung 2b is shown."""

    reason: Reason | None
    offered: bool


NOTHING: Final = Eligibility(reason=None, offered=False)


def general_eligibility(facts: EligibilityFacts, *, now: datetime) -> Eligibility:
    """FR9's order down to the code-access reasons, then FR31's gate."""
    stopped = _stopping_reason(facts, now=now)
    if stopped is not None:
        return Eligibility(reason=stopped, offered=False)
    if not (facts.sendable_facts and facts.cause_or_error_alias):
        return NOTHING
    if not facts.tokens_left or facts.tries_used >= TRIES_PER_FINDING:
        return Eligibility(reason="limit_reached", offered=False)
    if facts.in_flight:
        return Eligibility(reason="already_running", offered=False)
    return Eligibility(reason=None, offered=True)


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
