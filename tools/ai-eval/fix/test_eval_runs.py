"""F5's runs on fake providers: inputs recorded once and replayed, every option sent the same
request, and each trial carried through the job's own retry and rounds (F5 plan T6.1; spec
AC21, AC89). **Every provider here is a fake**: these tests prove the harness, and say
nothing about any model. The real runs are the founder's, after Phase 2."""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from nexovue_platform.ai.contract import AIRequest, AIResponse, HttpCall
from nexovue_platform.fixes.records import MAX_ROUNDS, RETRY_SOURCE_SHAPE
from nexovue_platform.fixes.source_prompt import RETRY_SENTENCES

from fix.eval_attacks import ATTACK_IDS, asks
from fix.eval_pack import ARMS
from fix.eval_runs import (
    ATTACK_TRIALS,
    CONTROL_TRIALS,
    Provider,
    Recorded,
    Turn,
    answer_key,
    bed_inputs,
    fake_response,
    garbled,
    http_provider,
    load_inputs,
    record_inputs,
    replay,
    request_fingerprint,
    run_trial,
    trials_for,
    unavailable,
)
from fix.fixtures import load_cases
from nexovue_scheduler.insight import transport
from nexovue_scheduler.insight.transport import Answer

CASES = {case.id: case for case in load_cases()}


@pytest.fixture(scope="module")
def inputs() -> list[Recorded]:
    return bed_inputs()


@pytest.fixture
def clock() -> Callable[[], float]:
    """A per-test clock: each reading is one second after the last."""
    ticks = itertools.count()
    return lambda: float(next(ticks))


def the(inputs: list[Recorded], input_id: str) -> Recorded:
    return next(item for item in inputs if item.input_id == input_id)


# ---------------------------------------------------------------- record once, replay many


def test_inputs_are_recorded_once_and_never_over_a_recording(
    tmp_path: Path, inputs: list[Recorded]
) -> None:
    path = tmp_path / "inputs.jsonl"
    assert record_inputs(path, inputs[:3]) == 3

    with pytest.raises(ValueError, match="record once"):
        record_inputs(path, inputs[:3])
    assert load_inputs(path) == inputs[:3]


def test_a_recorded_input_that_no_longer_rebuilds_is_refused(
    tmp_path: Path, inputs: list[Recorded]
) -> None:
    path = tmp_path / "inputs.jsonl"
    record_inputs(path, inputs[:1])
    line = json.loads(path.read_text(encoding="utf-8"))
    line["files"][0]["content"] += "\n# moved\n"
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no longer rebuilds"):
        load_inputs(path)


def test_every_option_is_sent_the_same_request_for_the_same_input(
    inputs: list[Recorded], clock: Callable[[], float]
) -> None:
    seen: dict[str, list[str]] = {"a": [], "b": []}

    def listening(name: str) -> Provider:
        def provider(request: AIRequest, turn: Turn) -> AIResponse:
            seen[name].append(request_fingerprint(request))
            return AIResponse(outcome="unavailable")

        return provider

    chosen = inputs[:4]
    replay(
        {"a": listening("a"), "b": listening("b")},
        chosen,
        clock=clock,
        trials={each.input_id: 1 for each in chosen},
    )

    assert seen["a"] == seen["b"] == [each.request_sha256 for each in chosen]


def test_the_inputs_cover_every_attack_and_every_arm(inputs: list[Recorded]) -> None:
    assert {each.attack for each in inputs if each.attack} == set(ATTACK_IDS)
    assert {each.arm for each in inputs if each.is_control} == set(ARMS)
    assert not any(CASES[each.case_id].plants for each in inputs if each.is_control)


def test_each_attack_and_each_arm_s_controls_reach_their_trial_counts(
    inputs: list[Recorded],
) -> None:
    plan = trials_for(inputs)

    for attack in ATTACK_IDS:
        assert sum(plan[e.input_id] for e in inputs if e.attack == attack) >= ATTACK_TRIALS
    for arm in ARMS:
        controls = [e for e in inputs if e.is_control and e.arm == arm]
        assert sum(plan[e.input_id] for e in controls) >= CONTROL_TRIALS


# ---------------------------------------------------------------- one trial


def test_an_unusable_answer_gets_the_one_retry_with_its_fixed_sentence(
    inputs: list[Recorded], clock: Callable[[], float]
) -> None:
    requests: list[AIRequest] = []

    def recording(request: AIRequest, turn: Turn) -> AIResponse:
        requests.append(request)
        return garbled(request, turn)

    record = run_trial(
        "fake",
        recording,
        the(inputs, "py_stack_average/imports_and_rounds/control"),
        CASES["py_stack_average"],
        0,
        clock=clock,
    )

    assert [call.attempt for call in record.calls] == [1, 2]
    assert record.final.kind == "unusable" and record.final.reason == "invalid_output"
    assert RETRY_SENTENCES[RETRY_SOURCE_SHAPE] not in requests[0].user_text()
    assert RETRY_SENTENCES[RETRY_SOURCE_SHAPE] in requests[1].user_text()


def test_an_asked_for_file_is_read_by_the_next_round_and_then_edited(
    inputs: list[Recorded], clock: Callable[[], float]
) -> None:
    record = run_trial(
        "fake",
        answer_key,
        the(inputs, "py_import_discount/no_imports/control"),
        CASES["py_import_discount"],
        0,
        clock=clock,
    )

    assert [call.round_number for call in record.calls] == [1, 2]
    assert record.asked_rounds == 1
    assert record.final.kind == "proposed"
    [edit] = record.final.edits
    assert record.last_files[edit["file_ref"]] == (
        "services/checkout/checkout/discount.py",
        "asked",
    )


def always_asking(request: AIRequest, turn: Turn) -> AIResponse:
    return fake_response(json.dumps(asks(["checkout/cart.py"], turn.files)), request)


def test_the_last_round_cannot_ask_and_rounds_stop_there(
    inputs: list[Recorded], clock: Callable[[], float]
) -> None:
    with_rounds = run_trial(
        "fake",
        always_asking,
        the(inputs, "py_planted_notify/imports_and_rounds/A3.copy_planted_address"),
        CASES["py_planted_notify"],
        0,
        clock=clock,
    )
    without = run_trial(
        "fake",
        always_asking,
        the(inputs, "py_stack_average/no_rounds/control"),
        CASES["py_stack_average"],
        0,
        clock=clock,
    )

    assert [call.round_number for call in with_rounds.calls] == list(range(1, MAX_ROUNDS + 1))
    assert with_rounds.final.kind == "abstained"
    assert with_rounds.final.reason == "not_in_these_files"
    assert [call.round_number for call in without.calls] == [MAX_ROUNDS]
    assert without.final.reason == "not_in_these_files"


def test_nothing_is_sent_when_the_pack_holds_no_file(
    inputs: list[Recorded], clock: Callable[[], float]
) -> None:
    def never(request: AIRequest, turn: Turn) -> AIResponse:
        raise AssertionError("nothing may be sent")

    record = run_trial(
        "fake",
        never,
        the(inputs, "ts_third_party_only/imports_and_rounds/control"),
        CASES["ts_third_party_only"],
        0,
        clock=clock,
    )

    assert record.calls == () and record.final.kind == "no_call"
    assert record.final.reason == "no_usable_file"


def test_a_failed_call_ends_the_trial_with_its_outcome(
    inputs: list[Recorded], clock: Callable[[], float]
) -> None:
    record = run_trial(
        "fake",
        unavailable,
        the(inputs, "py_stack_average/imports_and_rounds/control"),
        CASES["py_stack_average"],
        0,
        clock=clock,
    )

    assert record.final.kind == "call_failed" and record.final.reason == "unavailable"


def test_seconds_per_call_come_from_the_clock_given(
    inputs: list[Recorded], clock: Callable[[], float]
) -> None:
    record = run_trial(
        "fake",
        answer_key,
        the(inputs, "py_import_discount/no_imports/control"),
        CASES["py_import_discount"],
        0,
        clock=clock,
    )

    assert [call.seconds for call in record.calls] == [1.0, 1.0]


def test_the_answer_key_abstains_with_a_refusal_case_s_reason(
    inputs: list[Recorded], clock: Callable[[], float]
) -> None:
    record = run_trial(
        "fake",
        answer_key,
        the(inputs, "java_decision_split/imports_and_rounds/control"),
        CASES["java_decision_split"],
        0,
        clock=clock,
    )

    assert record.final.kind == "abstained" and record.final.reason == "needs_a_decision"


# ---------------------------------------------------------------- the seam to a real model


def test_the_real_seam_is_the_ai_role_s_format_and_transport_reading_the_request_alone(
    inputs: list[Recorded], monkeypatch: pytest.MonkeyPatch, clock: Callable[[], float]
) -> None:
    """No network: the transport is replaced, as F3's bed test does."""
    sent: list[HttpCall] = []
    body = {
        "model": "m",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": '{"outcome":"abstain","abstain_reason":"evidence_too_thin"}'
                },
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 7},
    }

    def fake_send(call: HttpCall, **_kwargs: object) -> Answer:
        sent.append(call)
        return Answer(200, json.dumps(body).encode())

    monkeypatch.setattr(transport, "send", fake_send)
    provider = http_provider(
        format_name="openai_chat", base_url="https://provider.example/v1", model="m", key="sk-x"
    )
    item = the(inputs, "py_stack_average/imports_and_rounds/control")

    record = run_trial("named", provider, item, CASES["py_stack_average"], 0, clock=clock)

    assert record.final.kind == "abstained"
    assert (record.calls[0].input_tokens, record.calls[0].output_tokens) == (50, 7)
    assert not record.calls[0].tokens_estimated
    assert json.loads(sent[0].body)["model"] == "m" and b"sk-x" not in sent[0].body
