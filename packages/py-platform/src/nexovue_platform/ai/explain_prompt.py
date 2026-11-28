"""F3's one request: put a finding's checked facts into plainer words (spec FR36).

The instructions are short and constant. They say what the facts are and what shape to
return, and **they are not a safety control** (research, section 7): what keeps attacker text
out is that `model_input` holds none (BR8), and what keeps a bad answer off the page is
`validate.py` (plan T5.1). Changing any constant here changes the bytes of every request, so
`PROMPT_VERSION` moves with it, and an older run then reads "Cannot be reproduced" (AC36).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from nexovue_platform.ai.contract import AIRequest, Trusted, canonical_json
from nexovue_platform.evidence.model_facts import (
    Alias,
    Cause,
    CheckId,
    FactId,
    FindingFacts,
    ModelFact,
    ModelFacts,
    NextCheck,
)

PROMPT_VERSION: Final = "explain-1"
# The most a run may write (design, step 3; a guess the bed replaces).
MAX_OUTPUT_TOKENS: Final = 800
SCHEMA_NAME: Final = "finding_wording"

INSTRUCTIONS: Final = (
    "You are given the checked facts about one finding in a software system, as JSON. "
    "Names are stand-ins such as service_1; times are minutes from the finding's start. "
    "Restate up to three of the facts in plain English for an on-call engineer. "
    "Each claim must cite the ids of the facts it rests on, use only numbers that appear in "
    "those facts, and say 'before' rather than claim that one thing caused another. "
    "You may pick up to two next checks by id from next_checks. "
    "If the facts give you nothing to add, set abstain to true and return no claims. "
    "Return only JSON that fits the schema."
)

CLAIM_TYPES: Final = ("restates_fact", "timing", "explains_term")
MAX_CLAIMS: Final = 3
MAX_PICKS: Final = 2

RESPONSE_SCHEMA: Final[Mapping[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["claims", "next_checks", "abstain"],
    "properties": {
        "claims": {
            "type": "array",
            "maxItems": MAX_CLAIMS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "text", "evidence_ids"],
                "properties": {
                    "type": {"type": "string", "enum": list(CLAIM_TYPES)},
                    "text": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "next_checks": {"type": "array", "maxItems": MAX_PICKS, "items": {"type": "string"}},
        "abstain": {"type": "boolean"},
    },
}

# Every field and fact kind `ModelFacts` can send, for the preview's list (FR25). Held equal
# to the dataclasses by `test_ai_wire_formats.py`, so a new field cannot be sent unlisted.
SENDABLE_FIELDS: Final = (
    "finding.subject",
    "finding.signal",
    "finding.severity",
    "finding.unit",
    "finding.normal",
    "finding.worst",
    "finding.started_minutes_ago",
    "facts[].id",
    "facts[].kind",
    "facts[].basis",
    "facts[].subject",
    "facts[].callee",
    "facts[].callee_kind",
    "facts[].version",
    "facts[].error",
    "facts[].check",
    "facts[].check_state",
    "facts[].measure",
    "facts[].before",
    "facts[].during",
    "facts[].minutes_from_start",
    "facts[].deploys_last_7_days",
    "facts[].days_of_records",
    "facts[].chance",
    "facts[].absence",
    "likely_cause.evidence_id",
    "likely_cause.strength",
    "next_checks[].id",
    "next_checks[].kind",
    "next_checks[].subject",
    "schema",
)


def explain_request(model_input: Mapping[str, Any]) -> AIRequest:
    """The request for one stored `model_input`, byte for byte the same each time it is built
    from the same row: the preview, the run and "View what was sent" share it (AC36)."""
    return AIRequest(
        instructions=INSTRUCTIONS,
        parts=(Trusted(canonical_json(dict(model_input)).decode()),),
        response_schema=RESPONSE_SCHEMA,
        schema_name=SCHEMA_NAME,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        purpose="explain",
    )


def fields_sent(model_input: Mapping[str, Any]) -> list[str]:
    """The names of the fields one input holds, never their values (FR39, `ai_runs`)."""
    names: set[str] = set()
    for key, value in model_input.items():
        if isinstance(value, Mapping):
            names.update(f"{key}.{inner}" for inner in value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    names.update(f"{key}[].{inner}" for inner in item)
        else:
            names.add(key)
    return sorted(names)


def _sample() -> dict[str, Any]:
    service, database, version = Alias("service_1"), Alias("service_2"), Alias("version_1")
    return ModelFacts(
        finding=FindingFacts(
            subject=service,
            signal="slower",
            severity="warning",
            unit="ms",
            normal=120.0,
            worst=480.0,
            started_minutes_ago=12,
        ),
        facts=(
            ModelFact(
                id=FactId("e1"),
                kind="deploy",
                basis="verified",
                subject=service,
                version=version,
                minutes_from_start=-4,
                deploys_last_7_days=3,
                days_of_records=7,
                chance=0.07,
            ),
            ModelFact(
                id=FactId("e2"),
                kind="dependency",
                basis="telemetry",
                subject=service,
                callee=database,
                callee_kind="database",
                measure="latency_p95_ms",
                before=12.0,
                during=95.0,
                minutes_from_start=-1,
            ),
            ModelFact(id=FactId("e3"), kind="absence", basis=None, absence="no_host_names"),
        ),
        likely_cause=Cause(evidence_id=FactId("e1"), strength="likely"),
        next_checks=(
            NextCheck(id=CheckId("c1"), kind="open_traces", subject=service),
            NextCheck(id=CheckId("c2"), kind="open_deploys", subject=service),
        ),
    ).as_json()


# The preview's built-in sample, for an organization with no finding yet (FR25), labelled a
# sample wherever it shows, with names that say so.
SAMPLE_MODEL_INPUT: Final[dict[str, Any]] = _sample()
SAMPLE_ALIASES: Final[dict[str, str]] = {
    "service_1": "checkout (sample)",
    "service_2": "orders-db (sample)",
    "version_1": "v2.1 (sample)",
}
