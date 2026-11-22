"""Chat's cost tool (F4 plan T4.3; spec AC52, AC104, D26)."""

from __future__ import annotations

import re
from typing import Any

from chat.harness import TrialRecord

from cost.cost import (
    CHAT_SHARE,
    D26_SUGGESTION,
    REPOSITORY_ROOT,
    guesses,
    measure,
    monthly_arithmetic,
    render,
)


def _record(option: str, tokens: tuple[int, int], steps: int, *, cut: bool = False) -> TrialRecord:
    return TrialRecord(
        option=option,
        run="chat",
        case="c",
        trial=1,
        anchor="2026-11-22T10:37:00+00:00",
        question="q",
        state="answered",
        answer=None,
        failure=None,
        rejection=None,
        abstain=None,
        used_tokens=sum(tokens),
        steps=[
            {"position": n, "tool": "get_service", "state": "read", "was_cut": cut and n == 1}
            for n in range(1, steps + 1)
        ],
        calls=[
            {
                "input_tokens": tokens[0],
                "output_tokens": tokens[1],
                "request_characters": 5_000,
                "tokens_estimated": False,
            }
        ],
        reads=[],
        leaked=[],
        writes={},
        seconds=2.0,
        first_step_seconds=1.0,
    )


def test_each_guess_names_a_constant_its_file_really_holds_with_that_value() -> None:
    found = guesses()
    assert {each.requirement for each in found} == {"FR10", "FR11", "FR19", "FR20", "D26"}
    for each in found:
        source = (REPOSITORY_ROOT / each.path).read_text(encoding="utf-8")
        declared = re.search(rf"^{each.constant}: Final = (.+)$", source, re.MULTILINE)
        assert declared, (each.constant, each.path)
        assert eval(declared.group(1).replace("_", "")) == each.current, each.constant


def test_measure_counts_tokens_steps_cuts_and_the_reservation() -> None:
    records = [
        _record("o", (50_000, 15_000), 8, cut=True),
        _record("o", (1_000, 100), 2),
        _record("other", (1, 1), 1),
    ]
    costs = measure(records, "o")
    assert costs.questions == 2
    assert costs.tokens == (65_000, 1_100)
    assert costs.all_steps_used == 1
    assert (costs.steps_cut, costs.steps_read) == (1, 10)
    assert costs.over_reservation == 1
    assert costs.largest_request == 5_000


def test_the_monthly_arithmetic_is_d26s_with_a_measured_figure() -> None:
    assert monthly_arithmetic(31_000) == round(31_000 * 5 * 10 * 22 / CHAT_SHARE)


def test_the_report_names_the_gap_changes_nothing_and_labels_a_fake() -> None:
    text = render(measure([_record("fake-x", (1_000, 100), 1)], "fake-x"), fake=True)
    assert "SUGGESTED_MONTHLY_TOKENS" in text and f"{D26_SUGGESTION:,}" in text
    assert "No constant was changed by this run." in text
    assert "Fake-provider figures" in text
    assert "not measured (people's use)" in text
    real: Any = render(measure([_record("local", (1_000, 100), 1)], "local"), fake=False)
    assert "Fake-provider figures" not in real
