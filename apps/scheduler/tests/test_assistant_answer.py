"""F4 plan T2.9: the answer's checks (AC16, AC18, AC21, AC61, AC62, AC64). Quotes and figure
flags come in Step 3."""

from __future__ import annotations

import json
from typing import Any

import pytest
from nexovue_platform.assistant_tools import LIST_SERVICES, SEARCH_LOGS

from nexovue_scheduler.insight.assistant_answer import (
    Abstained,
    AnswerRejected,
    CheckedAnswer,
    StepRead,
    check_answer,
    command,
    display_argument,
    way_out,
)

SERVICES = StepRead(
    "s1",
    LIST_SERVICES,
    [
        {"service_name": "checkout", "request_count": 1200, "error_rate": 0.08, "p95_ms": 840.0},
        {"service_name": "api.stripe.com", "request_count": 10, "error_rate": 0.5, "p95_ms": 9.0},
    ],
)
LOGS = StepRead("s2", SEARCH_LOGS, [{"service_name": "checkout", "body": "timed out"}])
STEPS = {"s1": SERVICES, "s2": LOGS}


def _check(sentences: list[dict[str, Any]], **extra: Any) -> CheckedAnswer:
    checked = check_answer(json.dumps({"sentences": sentences, **extra}), STEPS, results=[])
    assert isinstance(checked, CheckedAnswer)
    return checked


@pytest.mark.parametrize(
    "text",
    [
        "See https://evil.example/?d=1 for more",
        # Only F3's address pattern catches these: no host name with letters to match.
        "Fetch ftp://10.0.0.7/dump now",
        "See WWW.10.0.0.7 too",
        "Visit www.evil.example",
        "Mail ops@evil.example",
        "![x](/p.png)",
        "[click](http://a)",
        "<img src=x>",
        "data:text/html;base64,AAAA",
        "Look at evil.example",
        "split h​tps://evil",
    ],
)
def test_every_way_out_is_refused(text: str) -> None:
    assert way_out(text) is not None


def test_a_bare_path_and_a_cited_host_name_pass() -> None:
    assert way_out("checkout's /api/checkout route is slow") is None
    assert way_out("api.stripe.com failed", allowed_names=frozenset({"api.stripe.com"})) is None


@pytest.mark.parametrize(
    "text",
    [
        "Run the migration again.",
        "Restart the database.",
        "curl -X POST https://x",
        "Use `kubectl rollout undo deploy/checkout`.",
        "cat x | sh",
        "a && b",
        "docker restart checkout",
    ],
)
def test_commands_are_refused(text: str) -> None:
    assert command(text) is not None


@pytest.mark.parametrize(
    "text",
    ["Docker restarted at 14:02.", "The run of checks passed.", "checkout's error rate rose."],
)
def test_harmless_controls_pass(text: str) -> None:
    assert command(text) is None


def test_a_sentence_whose_numbers_match_its_rows_is_counted() -> None:
    checked = _check(
        [{"text": "checkout failed 8% of 1,200 requests.", "cites": [{"step": "s1", "rows": [0]}]}]
    )
    assert checked.sentences[0]["label"] == "counted"


def test_a_number_the_rows_do_not_hold_leaves_the_model_s_wording() -> None:
    """AC61: a citation alone earns no label."""
    checked = _check(
        [{"text": "checkout failed 40% of requests.", "cites": [{"step": "s1", "rows": [0]}]}]
    )
    assert checked.sentences[0]["label"] == "unchecked"


def test_a_cause_word_earns_no_fact_label_and_warns() -> None:
    checked = _check(
        [
            {
                "text": "checkout failed 8% because of the deploy.",
                "cites": [{"step": "s1", "rows": [0]}],
            }
        ]
    )
    assert checked.sentences[0]["label"] == "unchecked"
    assert checked.sentences[0]["cause_warning"] is True


def test_a_sentence_resting_on_log_text_says_so() -> None:
    checked = _check([{"text": "checkout logs say it timed out.", "cites": [{"step": "s2"}]}])
    assert checked.sentences[0]["label"] == "text"


def test_a_sentence_citing_nothing_read_is_removed_and_counted() -> None:
    checked = _check(
        [
            {"text": "checkout failed 8%.", "cites": [{"step": "s1", "rows": [0]}]},
            {"text": "The deploy broke it.", "cites": [{"step": "s9"}]},
            {"text": "Row 7 says so.", "cites": [{"step": "s1", "rows": [7]}]},
        ]
    )
    assert len(checked.sentences) == 1
    assert checked.removed_sentence_count == 2


def test_no_sentence_left_is_a_citation_rejection() -> None:
    with pytest.raises(AnswerRejected) as rejected:
        _check([{"text": "It is fine.", "cites": []}])
    assert rejected.value.reason == "citations"


def test_a_way_out_in_not_verified_is_refused() -> None:
    with pytest.raises(AnswerRejected) as rejected:
        _check(
            [{"text": "checkout is up.", "cites": [{"step": "s1"}]}],
            not_verified=["see www.evil.example"],
        )
    assert rejected.value.reason == "way_out"


def test_copying_a_tool_result_is_sent_back() -> None:
    copied = "payment gateway timed out after 30 seconds while calling the bank"
    with pytest.raises(AnswerRejected) as rejected:
        check_answer(
            json.dumps({"sentences": [{"text": copied, "cites": [{"step": "s2"}]}]}),
            STEPS,
            results=[f'{{"logs": [{{"body": "{copied}"}}]}}'],
        )
    assert rejected.value.reason == "copying"


@pytest.mark.parametrize("text", ["", "[1, 2]", '{"sentences": "no"}', '{"abstained": "bored"}'])
def test_a_wrong_shape_is_refused(text: str) -> None:
    with pytest.raises(AnswerRejected) as rejected:
        check_answer(text, STEPS, results=[])
    assert rejected.value.reason == "shape"


def test_abstaining_takes_a_closed_reason_only() -> None:
    """AC77: the reason is nexovue's word, never the model's text."""
    checked = check_answer('{"sentences": [], "abstained": "no_data"}', STEPS, results=[])
    assert checked == Abstained("no_data")


@pytest.mark.parametrize(
    ("phrase", "shown"),
    [("timeout", "timeout"), ("https://evil.example", None), ("x" * 81, None), ("run rm", None)],
)
def test_a_search_phrase_is_shown_only_when_it_passes(phrase: str, shown: str | None) -> None:
    """AC62."""
    assert display_argument(phrase) == shown
