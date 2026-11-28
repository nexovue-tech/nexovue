"""Model runs: record `model_input` once, replay it across providers offline (plan T6.1, T6.3).

A provider here is any function from F3's `AIRequest` to an `AIResponse`. The fakes below stand in
for a model and call nothing; `http_provider` is the one seam to a real model, through the same wire
formats and connect-time transport the AI role uses, and **it has not been run against a real
model: that is the founder's, after Phase 2 (foundation item 31)**. A fake's numbers say nothing
about any model, and the report never presents them as if they did.

`record_inputs` writes each `model_input` once, with the hash of the request built from it; `replay`
runs a provider over the recorded inputs for as many trials as asked, so every provider sees the
same bytes. A recorded request that no longer rebuilds to its hash (the prompt moved) is refused.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from fixtures.cases import ESCAPES, Escape, ModelCase
from nexovue_platform.ai.contract import AIRequest, AIResponse, estimate_tokens
from nexovue_platform.ai.explain_prompt import explain_request

Provider = Callable[[AIRequest], AIResponse]

# ------------------------------------------------------------------ recorded inputs


@dataclass(frozen=True, slots=True)
class Recorded:
    case_id: str
    model_input: dict[str, Any]
    request_sha256: str


def request_hash(model_input: dict[str, Any]) -> str:
    request = explain_request(model_input)
    return hashlib.sha256(request.user_text().encode()).hexdigest()


def record_inputs(path: Path, cases: Iterable[ModelCase]) -> int:
    """Write each case once. Refuses a file that already holds recorded inputs."""
    if path.exists() and path.stat().st_size > 0:
        raise ValueError(f"{path} already holds recorded inputs: record once, replay many times")
    count = 0
    with path.open("w", encoding="utf-8") as out:
        for case in cases:
            line = {
                "case_id": case.id,
                "model_input": case.model_input,
                "request_sha256": request_hash(case.model_input),
            }
            out.write(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")
            count += 1
    return count


def load_inputs(path: Path) -> list[Recorded]:
    recorded: list[Recorded] = []
    for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not text.strip():
            continue
        line = json.loads(text)
        item = Recorded(line["case_id"], line["model_input"], line["request_sha256"])
        if request_hash(item.model_input) != item.request_sha256:
            raise ValueError(
                f"{path}:{number}: the request no longer rebuilds to its recorded hash "
                "(the prompt or the input's shape moved); record the inputs again"
            )
        recorded.append(item)
    return recorded


# ------------------------------------------------------------------ the fakes


def _input_of(request: AIRequest) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(request.user_text())
    return parsed


def _response(text: str, request: AIRequest) -> AIResponse:
    """A fake's answer with counts estimated from bytes, flagged as estimated."""
    return AIResponse(
        outcome="ok",
        text=text,
        input_tokens=estimate_tokens(len(request.user_text().encode()) + len(request.instructions)),
        output_tokens=estimate_tokens(len(text.encode())),
        tokens_estimated=True,
        model_reported="fake",
    )


def restating_sentence(fact: dict[str, Any]) -> str | None:
    """One plain sentence restating `fact` with only its own aliases and numbers, or None."""
    kind = fact.get("kind")
    if kind == "deploy" and fact.get("minutes_from_start") is not None:
        return (
            f"{fact['subject']} had a deploy of {fact['version']} "
            f"{abs(fact['minutes_from_start'])} minutes before the finding."
        )
    if kind == "dependency" and fact.get("before") is not None and fact.get("during") is not None:
        return (
            f"Calls from {fact['subject']} to {fact['callee']} went from "
            f"{fact['before']:.1f} to {fact['during']:.1f}."
        )
    if kind == "absence":
        return "Nothing was recorded for a reading."
    return None


def obedient(request: AIRequest) -> AIResponse:
    """A careful writer: restates the first fact it can, picks no checks, never abstains if it can
    write. The control for the claims that must survive."""
    model_input = _input_of(request)
    for fact in model_input.get("facts", []):
        sentence = restating_sentence(fact)
        if sentence is not None:
            answer = {
                "claims": [
                    {"type": "restates_fact", "text": sentence, "evidence_ids": [fact["id"]]}
                ],
                "next_checks": [],
                "abstain": False,
            }
            return _response(json.dumps(answer), request)
    return _response(json.dumps({"claims": [], "next_checks": [], "abstain": True}), request)


def abstaining(request: AIRequest) -> AIResponse:
    return _response(json.dumps({"claims": [], "next_checks": [], "abstain": True}), request)


def garbled(request: AIRequest) -> AIResponse:
    return _response("Sure! Here is my analysis, not JSON at all.", request)


def hostile(escape: Escape) -> Provider:
    """Assumes the injection already won: writes `escape` as its one claim, citing fact one."""

    def provider(request: AIRequest) -> AIResponse:
        facts = _input_of(request).get("facts", [])
        cited = facts[0]["id"] if facts else "e1"
        answer = {
            "claims": [{"type": "restates_fact", "text": escape.text, "evidence_ids": [cited]}],
            "next_checks": [],
            "abstain": False,
        }
        return _response(json.dumps(answer, ensure_ascii=False), request)

    return provider


def echoing(request: AIRequest) -> AIResponse:
    """Copies every string it can find in the request into its answer, as a model told to repeat
    what it was sent would. Used to show the request holds no attacker text to copy."""
    strings: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(_input_of(request))
    return _response(" ".join(strings), request)


FAKES: Final[dict[str, Provider]] = {
    "fake-obedient": obedient,
    "fake-abstaining": abstaining,
    "fake-garbled": garbled,
    **{f"fake-hostile-{each.id}": hostile(each) for each in ESCAPES},
}


# ------------------------------------------------------------------ the seam to a real model


def http_provider(
    *, format_name: str, base_url: str, model: str, key: str | None, private: str = ""
) -> Provider:
    """A real model through nexovue's own wire format and transport. **Not run here:** a real
    call is the founder's, after Phase 2. It exists so the harness is the same code either way."""
    from nexovue_platform.ai.formats import wire_format

    from nexovue_scheduler.insight import transport
    from nexovue_scheduler.probes.addresses import PrivateEndpoints

    fmt = wire_format(format_name)
    endpoints = PrivateEndpoints.parse(private)

    def provider(request: AIRequest) -> AIResponse:
        call = fmt.build(request, model=model, base_url=base_url, key=key)
        answer = transport.send(call, timeout_seconds=120, private_endpoints=endpoints)
        if answer.failure is not None:
            return AIResponse(outcome="unavailable")
        return fmt.parse(answer.status or 0, answer.body).with_counts(request_body=call.body)

    return provider


# ------------------------------------------------------------------ replay


@dataclass(frozen=True, slots=True)
class TrialRecord:
    provider: str
    case_id: str
    trial: int
    request_sha256: str
    response: AIResponse


def replay(
    providers: dict[str, Provider], recorded: Sequence[Recorded], *, trials: int
) -> list[TrialRecord]:
    """Every provider over every recorded input, `trials` times each, on identical requests."""
    if trials < 1:
        raise ValueError("at least one trial")
    records: list[TrialRecord] = []
    for name, provider in providers.items():
        for item in recorded:
            request = explain_request(item.model_input)
            for trial in range(trials):
                records.append(
                    TrialRecord(name, item.case_id, trial, item.request_sha256, provider(request))
                )
    return records
