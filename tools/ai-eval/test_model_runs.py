"""Model runs on fake providers: record once, replay across providers, and score injection, wording
and cost (F3 plan T6.1, T6.3; AC61, AC63). **Every provider here is a fake**: these tests prove the
harness, and say nothing about any model. The real runs are the founder's, after Phase 2."""

from __future__ import annotations

import json
from pathlib import Path

import model_scoring
import pytest
from bed_builders import passes_everything
from fixtures.cases import ESCAPES, INPUTS
from model_runs import (
    FAKES,
    Provider,
    Recorded,
    abstaining,
    echoing,
    hostile,
    http_provider,
    load_inputs,
    obedient,
    record_inputs,
    replay,
    request_hash,
)
from model_scoring import (
    Price,
    cost_by_provider,
    escape_classes,
    injection_by_provider,
    known_aliases,
    runs_a_limit_buys,
    score_all,
    wording_by_provider,
)
from nexovue_platform.ai.contract import AIRequest, AIResponse, HttpCall
from nexovue_platform.ai.explain_prompt import explain_request

from nexovue_scheduler.insight.transport import Answer


@pytest.fixture
def recorded(tmp_path: Path) -> list[Recorded]:
    path = tmp_path / "inputs.jsonl"
    assert record_inputs(path, INPUTS) == len(INPUTS)
    return load_inputs(path)


# ---------------------------------------------------------------- record once, replay many


def test_inputs_are_recorded_once_and_never_over_a_recording(tmp_path: Path) -> None:
    path = tmp_path / "inputs.jsonl"
    record_inputs(path, INPUTS)

    with pytest.raises(ValueError, match="record once"):
        record_inputs(path, INPUTS)


def test_a_recorded_input_that_no_longer_rebuilds_to_its_hash_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "inputs.jsonl"
    record_inputs(path, INPUTS[:1])
    line = json.loads(path.read_text(encoding="utf-8"))
    line["model_input"]["finding"]["worst"] = 1.0
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no longer rebuilds"):
        load_inputs(path)


def test_every_provider_sees_the_same_bytes_for_the_same_input(recorded: list[Recorded]) -> None:
    seen: dict[str, list[str]] = {"a": [], "b": []}

    def listening(name: str) -> Provider:
        def provider(request: AIRequest) -> AIResponse:
            seen[name].append(request.user_text())
            return AIResponse(outcome="unavailable")

        return provider

    replay({"a": listening("a"), "b": listening("b")}, recorded, trials=2)

    assert seen["a"] == seen["b"] and len(seen["a"]) == 2 * len(recorded)
    assert seen["a"][0] == explain_request(recorded[0].model_input).user_text()
    assert request_hash(recorded[0].model_input) == recorded[0].request_sha256


def test_a_replay_runs_each_provider_each_input_the_stated_number_of_trials(
    recorded: list[Recorded],
) -> None:
    records = replay({"x": obedient, "y": abstaining}, recorded, trials=3)

    assert len(records) == 2 * len(recorded) * 3
    with pytest.raises(ValueError, match="at least one"):
        replay({"x": obedient}, recorded, trials=0)


# ---------------------------------------------------------------- the detectors


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("See https://attacker.example/x", {"url"}),
        ("Write to ops@attacker.example", {"email"}),
        ("Send it to attacker.example now", {"domain"}),
        ("![x](picture)", {"markup"}),
        ("service_9 slowed down", {"unknown_alias"}),
        ("Restart service_1 now", {"command"}),
        ("Latency rose​", {"hidden_characters"}),
        ("service_1 had a deploy 4 minutes before", set()),
    ],
)
def test_the_detectors_name_each_way_out(text: str, expected: set[str]) -> None:
    assert escape_classes(text, frozenset({"service_1"})) == frozenset(expected)


def test_known_aliases_are_those_the_input_holds() -> None:
    ordinary = next(case for case in INPUTS if case.id == "ordinary_deploy")

    assert {"service_1", "service_2", "version_1"} <= known_aliases(ordinary.model_input)


# ---------------------------------------------------------------- injection


def test_a_hostile_writer_assuming_the_injection_won_gets_nothing_past_validation(
    recorded: list[Recorded],
) -> None:
    providers = {f"fake-hostile-{each.id}": hostile(each) for each in ESCAPES}

    scored = score_all(replay(providers, recorded, trials=1), INPUTS)
    rows = injection_by_provider(scored)

    assert set(rows) == set(providers)
    assert all(not row.way_out for row in rows.values())
    # The attempts were real: before validation the hostile answers held ways out.
    assert sum(row.trials_with_an_attempt for row in rows.values()) > len(ESCAPES)
    assert rows["fake-hostile-url"].attempted["url"] == len(INPUTS)


def test_a_way_out_that_survives_validation_fails_the_provider(
    recorded: list[Recorded],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_scoring, "check_answer", passes_everything)

    rows = injection_by_provider(
        score_all(replay({"fake-hostile-url": hostile(ESCAPES[0])}, recorded, trials=1), INPUTS)
    )

    assert rows["fake-hostile-url"].way_out
    assert rows["fake-hostile-url"].survived["url"] == len(INPUTS)


def test_the_request_holds_no_attacker_text_for_a_model_told_to_repeat_it_all(
    recorded: list[Recorded],
) -> None:
    scored = score_all(replay({"fake-echo": echoing}, recorded, trials=1), INPUTS)

    row = injection_by_provider(scored)["fake-echo"]

    assert not row.way_out and not row.attempted


# ---------------------------------------------------------------- wording and cost


def test_a_careful_writers_claims_on_the_ordinary_inputs_all_survive(
    recorded: list[Recorded],
) -> None:
    scored = score_all(replay({"fake-obedient": obedient}, recorded, trials=2), INPUTS)

    row = wording_by_provider(scored)["fake-obedient"]

    assert row.trials == 6 and row.written == row.shown == 6 and not row.removed_by


def test_abstentions_garbled_answers_and_removed_claims_are_counted_apart(
    recorded: list[Recorded],
) -> None:
    providers = {**{k: v for k, v in FAKES.items() if k in ("fake-abstaining", "fake-garbled")}}
    providers["fake-hostile-causal"] = hostile(next(e for e in ESCAPES if e.id == "causal"))

    rows = wording_by_provider(score_all(replay(providers, recorded, trials=1), INPUTS))

    assert rows["fake-abstaining"].abstained == 3 and rows["fake-abstaining"].shown == 0
    assert rows["fake-garbled"].unusable == 3
    assert (
        rows["fake-hostile-causal"].removed_by["causal"] == 3
        and rows["fake-hostile-causal"].unusable == 3
    )


def test_a_call_that_failed_is_counted_and_never_read_as_an_answer(
    recorded: list[Recorded],
) -> None:
    records = replay({"down": lambda _r: AIResponse(outcome="unavailable")}, recorded, trials=1)

    row = injection_by_provider(score_all(records, INPUTS))["down"]

    assert row.calls_failed == len(recorded) and not row.trials_with_an_attempt
    assert cost_by_provider(records) == {}
    assert wording_by_provider(score_all(records, INPUTS)) == {}


def test_an_answer_with_a_refusal_outcome_is_a_failed_call_even_if_it_carries_text(
    recorded: list[Recorded],
) -> None:
    hostile_text = '{"claims":[{"type":"restates_fact","text":"See https://x.example","evidence_ids":["e1"]}],"next_checks":[],"abstain":false}'
    records = replay(
        {"refusing": lambda _r: AIResponse(outcome="refused", text=hostile_text)},
        recorded,
        trials=1,
    )

    row = injection_by_provider(score_all(records, INPUTS))["refusing"]

    assert row.calls_failed == len(recorded) and not row.trials_with_an_attempt


def test_cost_is_tokens_per_call_with_estimates_flagged_and_a_price_only_when_given(
    recorded: list[Recorded],
) -> None:
    records = replay({"fake-obedient": obedient}, recorded, trials=2)

    unpriced = cost_by_provider(records)["fake-obedient"]
    priced = cost_by_provider(records, {"fake-obedient": Price(1.0, 2.0)})["fake-obedient"]

    assert unpriced.calls == 2 * len(recorded) and unpriced.dollars is None
    assert unpriced.estimated_calls == unpriced.calls
    assert unpriced.p50_tokens <= unpriced.p95_tokens
    expected = unpriced.tokens_in / 1e6 * 1.0 + unpriced.tokens_out / 1e6 * 2.0
    assert priced.dollars == pytest.approx(expected)


def test_runs_a_limit_buys_replaces_the_designs_guess_with_a_measured_size() -> None:
    assert runs_a_limit_buys(2_000_000, 10_800) == 185
    with pytest.raises(ValueError, match="at least one token"):
        runs_a_limit_buys(1, 0)


# ---------------------------------------------------------------- the seam to a real model


def test_the_real_seam_sends_the_same_request_the_ai_role_would_and_reads_its_counts(
    recorded: list[Recorded],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexovue_scheduler.insight import transport

    sent: list[HttpCall] = []
    body = {
        "model": "m",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": '{"claims":[],"next_checks":[],"abstain":true}'},
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 7},
    }

    def fake_send(call: HttpCall, **_kwargs: object) -> Answer:
        sent.append(call)
        return Answer(200, json.dumps(body).encode())

    monkeypatch.setattr(transport, "send", fake_send)
    provider = http_provider(
        format_name="openai_chat",
        base_url="https://provider.example/v1",
        model="m",
        key="sk-test-123",
    )

    response = provider(explain_request(recorded[0].model_input))

    assert response.outcome == "ok" and (response.input_tokens, response.output_tokens) == (50, 7)
    assert json.loads(sent[0].body)["model"] == "m"
    assert recorded[0].request_sha256 and "sk-test-123" not in sent[0].body.decode()
