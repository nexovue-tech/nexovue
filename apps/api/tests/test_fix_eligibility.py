"""FR9's list in order, rung 3's result, and where rung 2b may stand (F5 plan T3.7; spec AC8,
AC8a's results by kind of cause, AC10, D67).

Pure: each reason is reached in its stated order by switching one fact at a time from a
finding where everything holds; the next reason down must not show through.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from nexovue_platform.evidence import RULE_DEPENDENCY_FINDING

from nexovue_api.findings.fix_eligibility import (
    EligibilityFacts,
    first_reason,
    general_eligibility,
    source_eligibility,
)

NOW: Final = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
READY: Final = EligibilityFacts(
    hosted=False,
    ai_on=True,
    opted_out=False,
    project_scope=False,
    closed_reason=None,
    opened_at=NOW - timedelta(hours=1),
    error_group_linked=True,
    released_cause_rule="signed_deploy",
    sendable_facts=True,
    cause_or_error_alias=True,
    tokens_left=True,
    tries_used=0,
    in_flight=False,
    code_location=True,
    read_permission=True,
    consent_live=True,
)
# FR9's order, each with the one fact that brings it about.
ORDER: Final[list[tuple[str, dict[str, Any]]]] = [
    ("hosted_ai_not_offered", {"hosted": True}),
    ("ai_off", {"ai_on": False}),
    ("project_opted_out", {"opted_out": True}),
    ("project_finding", {"project_scope": True}),
    ("closed_as_not_a_problem", {"closed_reason": "false_alarm"}),
    (
        "evidence_pending",
        {"error_group_linked": False, "opened_at": NOW - timedelta(minutes=5)},
    ),
    ("cause_is_elsewhere", {"released_cause_rule": RULE_DEPENDENCY_FINDING}),
    ("no_code_location", {"code_location": False}),
    ("no_read_permission", {"read_permission": False}),
    ("no_consent", {"consent_live": False}),
    ("limit_reached", {"tries_used": 3}),
    ("already_running", {"in_flight": True}),
]


@pytest.mark.parametrize("index", range(len(ORDER)))
def test_ac8_each_reason_is_reached_and_wins_over_every_later_one(index: int) -> None:
    reason, _change = ORDER[index]
    facts = READY
    for _, later in ORDER[index:]:
        facts = replace(facts, **later)
    if reason == "evidence_pending":
        facts = replace(facts, released_cause_rule=None)

    assert first_reason(facts, now=NOW) == reason


def test_ready_and_ready_without_a_stack() -> None:
    assert source_eligibility(READY, now=NOW).result == "ready"
    stackless = replace(READY, error_group_linked=False, opened_at=NOW - timedelta(minutes=30))
    assert source_eligibility(stackless, now=NOW).result == "ready_without_stack"


def test_ac8a_a_dependency_s_finding_still_lets_a_person_name_files() -> None:
    elsewhere = replace(READY, released_cause_rule=RULE_DEPENDENCY_FINDING)

    found = source_eligibility(elsewhere, now=NOW)

    assert (found.reason, found.result) == ("cause_is_elsewhere", "ready_without_stack")
    blocked = source_eligibility(replace(elsewhere, consent_live=False), now=NOW)
    assert (blocked.reason, blocked.result) == ("no_consent", None)


def test_ac8a_no_likely_cause_with_a_stack_is_still_ready() -> None:
    assert source_eligibility(replace(READY, released_cause_rule=None), now=NOW).result == "ready"


def test_evidence_pending_ends_by_itself_after_its_limit() -> None:
    new = replace(READY, error_group_linked=False, opened_at=NOW - timedelta(minutes=5))

    assert first_reason(new, now=NOW) == "evidence_pending"
    assert first_reason(new, now=NOW + timedelta(minutes=11)) is None


@pytest.mark.parametrize("reason", ["no_code_location", "no_read_permission", "no_consent"])
def test_d67_rung_2b_stands_only_under_a_code_access_reason(reason: str) -> None:
    change = dict(ORDER)[reason]

    assert general_eligibility(replace(READY, **change), now=NOW).offered
    assert not general_eligibility(READY, now=NOW).offered


def test_d67_rung_2b_is_not_offered_past_the_limits() -> None:
    facts = replace(READY, code_location=False, tries_used=3)

    found = general_eligibility(facts, now=NOW)

    assert (found.reason, found.offered) == ("limit_reached", False)
