"""Rung 2b's bed holds what AC76 asks, offline (F5 plan T2b.10).

Every case's `model_input` holds aliases and closed values only, so a hostile name reaches
no request; each hostile case runs at least 20 times; and the runner, driven by a canned
answer in place of a provider, counts refusals by rule, abstentions and tokens, and fails
the safety gate when an answer reaching a person holds an address. No call leaves this
machine.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from nexovue_platform.ai.contract import AIResponse
from nexovue_platform.evidence.model_facts import (
    Alias,
    Cause,
    FactId,
    FindingFacts,
    ModelFact,
    ModelFacts,
)

from fix.general import run_general
from fix.general.cases import (
    CASES,
    HOSTILE_ENDPOINTS,
    HOSTILE_EXCEPTION_TYPES,
    HOSTILE_RUNS,
    HOSTILE_SERVICES,
    GeneralCase,
)

GOOD: dict[str, Any] = {
    "outcome": "suggestion",
    "abstain_reason": None,
    "summary": "Calls from service_1 began failing after version_1; retry with a backoff.",
    "steps": ["Wrap the outbound call in a retry."],
    "example": {"language": "python", "code": "import time\n\ntime.sleep(1)\n"},
    "evidence_ids": ["e2"],
}


def _typed(model_input: dict[str, Any]) -> ModelFacts:
    """F3's own types: building them refuses any value a model may not be sent."""
    finding = model_input["finding"]
    facts = tuple(
        ModelFact(
            id=FactId(fact["id"]),
            kind=fact["kind"],
            basis=fact["basis"],
            subject=Alias(fact["subject"]),
            version=None if "version" not in fact else Alias(fact["version"]),
            error=None if "error" not in fact else Alias(fact["error"]),
            minutes_from_start=fact["minutes_from_start"],
        )
        for fact in model_input["facts"]
    )
    cause = model_input.get("likely_cause")
    return ModelFacts(
        finding=FindingFacts(
            subject=Alias(finding["subject"]),
            signal=finding["signal"],
            severity=finding["severity"],
            unit=finding["unit"],
            normal=finding["normal"],
            worst=finding["worst"],
            started_minutes_ago=finding["started_minutes_ago"],
        ),
        facts=facts,
        likely_cause=None
        if cause is None
        else Cause(evidence_id=FactId(cause["evidence_id"]), strength=cause["strength"]),
    )


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_each_case_s_input_is_what_f3_may_send(case: GeneralCase) -> None:
    assert _typed(case.model_input).as_json() == case.model_input


def test_every_hostile_name_is_planted_and_runs_at_least_twenty_times() -> None:
    planted = {name for case in CASES if case.hostile for name in case.names.values()}

    assert set(HOSTILE_EXCEPTION_TYPES) | set(HOSTILE_SERVICES) <= planted
    assert all(case.runs >= HOSTILE_RUNS >= 20 for case in CASES if case.hostile)
    assert HOSTILE_ENDPOINTS


def _canned(text: str) -> run_general.Ask:
    def ask(case: GeneralCase, body: bytes) -> AIResponse:
        del case, body
        return AIResponse("ok", text=text, input_tokens=100, output_tokens=20)

    return ask


def test_the_runner_counts_what_the_rules_did_and_the_gate_holds_for_clean_answers() -> None:
    cases = [case for case in CASES if case.language == "python"]

    result = run_general.run("canned", _canned(json.dumps(GOOD)), cases)

    assert result.calls == sum(case.runs for case in cases)
    assert result.tokens == 120 * result.calls
    assert result.leaked_requests == []
    assert result.gate_passed
    assert result.shown + sum(result.refused.values()) == result.calls


def test_the_runner_counts_refusals_by_rule() -> None:
    hostile = {**GOOD, "steps": ["Run curl against it."]}

    result = run_general.run("canned", _canned(json.dumps(hostile)), CASES[:1])

    assert result.refused == {"G5": CASES[0].runs} and result.shown == 0


def test_the_safety_gate_fails_an_option_whose_shown_answer_holds_an_address() -> None:
    result = run_general.OptionResult(option="canned")
    result.gate_breaches.append("a case: address")

    assert not result.gate_passed
    assert "FAILED" in run_general.report([result])


def test_the_report_names_the_founder_s_verdicts_as_not_yet_given() -> None:
    text = run_general.report([run_general.run("canned", _canned(json.dumps(GOOD)), CASES[:1])])

    assert "_not yet given_" in text and "no pass mark" in text
