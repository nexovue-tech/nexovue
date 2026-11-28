"""All 42 payloads through the pipeline, with no model (plan T6.3; spec AC76; design, *Injection
suite in the gate*): none may reach the outgoing bytes.

Each payload is planted in every text field F3 reads from telemetry and records: the service's
name, a deploy's version, a callee, its attribute, an error's type, a check's name, an incident's
rule name and another finding's service. The facts then go through the same code the evidence job
runs after its reads (`rules.decide`, `sentences.record`, `inputs.build`) and the request F3 would
send (`explain_request`). A payload that appears in the instructions or the input fails.

The pipeline starts at `Facts` because the job's reads, which need Postgres and ClickHouse, are
proved apart by `test_insight_canary.py` through the real gather. The one filter the reads apply to
telemetry text, the exception-type identifier pattern, is applied here as well, from the store's
own function.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from fixtures.payloads import Payload
from nexovue_platform.ai.explain_prompt import explain_request
from nexovue_platform.evidence.aliases import AliasBook

from nexovue_scheduler.evidence import inputs, rules, sentences
from nexovue_scheduler.evidence.rules import (
    CheckChange,
    DeployFact,
    DeployHistory,
    EdgeFact,
    ErrorGroup,
    Facts,
    FindingInput,
    LinkedIncident,
    OtherFinding,
    Traffic,
)
from nexovue_scheduler.evidence.store import exception_type_shown

START: Final = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
NOW: Final = START + timedelta(minutes=5)
FIELDS: Final = (
    "service_name",
    "deploy_version",
    "callee",
    "callee_attribute",
    "error_type",
    "check_name",
    "incident_rule",
    "other_finding_service",
)


@dataclass(frozen=True, slots=True)
class Leak:
    payload_id: str
    field: str


def facts_with(text: str, field: str) -> Facts:
    """Facts with `text` in one field and plain values elsewhere, so a leak has one possible door.
    The edge and the deploy are moved and lined up, so the rules have something to name."""
    service = text if field == "service_name" else "checkout"
    callee = text if field == "callee" else "orders-db"
    return Facts(
        finding=FindingInput(
            id="payload", scope="service", service=service, started_at=START, signals=("slower",)
        ),
        now=NOW,
        zone=ZoneInfo("UTC"),
        deploys=(
            DeployFact(
                id="d1",
                service=service,
                version=text if field == "deploy_version" else "v2",
                at=START - timedelta(minutes=4),
                signed=True,
            ),
        ),
        histories={
            service: DeployHistory(
                service, (START - timedelta(minutes=4),), START - timedelta(days=9)
            )
        },
        edges=(
            EdgeFact(
                callee=callee,
                calls_before=100,
                calls_during=100,
                failures_before=1,
                failures_during=40,
                p95_before=10.0,
                p95_during=30.0,
                callee_attr=text if field == "callee_attribute" else "",
                callee_kind="database",
            ),
        ),
        other_findings=(
            OtherFinding("f1", text if field == "other_finding_service" else "cart", START),
        ),
        traffic=Traffic(10.0, 30.0),
        error_groups=(
            ErrorGroup(
                "g1",
                exception_type_shown(text) if field == "error_type" else "TimeoutError",
                (service,),
                5,
                START - timedelta(minutes=2),
            ),
        ),
        checks=(CheckChange("c1", text if field == "check_name" else "homepage", "down", START),),
        incidents=(LinkedIncident("i1", text if field == "incident_rule" else "latency", START),),
    )


def outgoing_text(facts: Facts) -> str:
    """Every byte F3 would send for these facts: the instructions and the input."""
    cause = rules.decide(facts)
    record = sentences.record(facts, cause)
    model_input = inputs.build(
        finding={
            "service_name": facts.finding.service,
            "signals": [{"signal": "slower", "key": "slower", "normal": 110.0, "worst": 412.0}],
            "severity": "warning",
            "started_at": facts.finding.started_at,
        },
        items=record.items,
        likely_cause=None if cause is None else cause.as_json(),
        book=AliasBook(None),
        now=NOW,
    ).as_json()
    request = explain_request(model_input)
    return f"{request.instructions}\n{request.user_text()}"


_WORD: Final = re.compile(r"[a-z0-9]{6,}")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def run(payloads: list[Payload]) -> list[Leak]:
    """Every (payload, field) where the payload, or any word of it that the same facts do not
    already send, appears in the outgoing bytes. Empty passes (AC76)."""
    leaks: list[Leak] = []
    for field in FIELDS:
        baseline = _words(outgoing_text(facts_with("benign", field)))
        for payload in payloads:
            sent = outgoing_text(facts_with(payload.text, field))
            if payload.text in sent or (_words(payload.text) - baseline) & _words(sent):
                leaks.append(Leak(payload.id, field))
    return leaks
