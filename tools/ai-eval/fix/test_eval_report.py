"""F5's evaluation report (plan T6.1, T6.5): the skeleton says what it waits for and holds
no result, the checked-in file is current, saved trials read back as they were, and the
filled form labels a fake as a fake and says what AC21 and AC38 require."""

from __future__ import annotations

import itertools
import re
from dataclasses import replace
from pathlib import Path

import pytest

from fix import eval_report
from fix.eval_runs import FAKES, Ending, Recorded, TrialRecord, bed_inputs, replay
from fix.fixtures import load_cases

CASES = load_cases()


@pytest.fixture(scope="module")
def records() -> list[TrialRecord]:
    """One trial of each input on each fake, on a clock of its own."""
    inputs: list[Recorded] = bed_inputs()
    ticks = itertools.count()
    return replay(
        FAKES,
        inputs,
        clock=lambda: float(next(ticks)),
        trials={each.input_id: 1 for each in inputs},
        cases=CASES,
    )


def test_the_checked_in_report_is_what_the_skeleton_renders() -> None:
    assert eval_report.REPORT.read_text(encoding="utf-8") == eval_report.skeleton(), (
        "run `PYTHONPATH=tools/ai-eval uv run python -m fix.eval_report`"
    )


def test_the_skeleton_names_the_founders_run_and_holds_no_result() -> None:
    text = " ".join(eval_report.skeleton().split())

    assert "Not yet run: the founder's, after Phase 2" in text
    assert "fake providers only" in text and "No real provider was called" in text
    assert "nothing wrote to any repository" in text
    assert "|" not in eval_report.skeleton(), (
        "a skeleton has no table: there is no result to put in one"
    )
    assert not re.search(r"\d+ ?%", text), "a skeleton states no rate"
    assert "**Not given.**" in text and "write_gate.py" in text
    assert "Authors' seeded fixture repository" in text
    assert "injection-proof" in text and "one consenting real repository" in text


def test_saved_trials_read_back_as_they_were(tmp_path: Path, records: list[TrialRecord]) -> None:
    path = tmp_path / "records.jsonl"
    chosen = [each for each in records if each.final.edits or each.asked_rounds][:5]

    assert eval_report.save_records(path, chosen) == len(chosen)
    assert eval_report.load_records(path) == chosen


def test_the_filled_form_labels_each_fake_and_reports_per_attack(
    records: list[TrialRecord],
) -> None:
    text = eval_report.render(records, CASES, seeded={})

    for option in FAKES:
        assert f"## {option} (fake: tests the harness, not a model)" in text
    assert "| A19 |" in text and "| `no_rounds` |" in text
    assert "Writes: none possible" in text
    assert "slower: no fixture" in text and "traffic: no fixture" in text
    assert text.count("**passed** on these trials.") == len(FAKES)
    assert "engineer's who did not write the fixtures: _not yet given_" in text
    assert "Authors' seeded fixture repository" in text and "injection-proof" in text


def test_a_failed_gate_says_to_leave_the_option_out(records: list[TrialRecord]) -> None:
    proposed = next(each for each in records if each.final.kind == "proposed")
    unresolved = replace(
        proposed,
        option="leaky",
        final=Ending("proposed", None, proposed.final.edits, ("nowhere",)),
    )

    text = eval_report.render([unresolved], CASES, seeded={})

    assert "**FAILED: leave this option out of the docs.**" in text


def test_a_named_local_model_s_machine_is_shown(records: list[TrialRecord]) -> None:
    text = eval_report.render(
        records[:1], CASES, seeded={}, machines={records[0].option: "a 16 GB laptop"}
    )

    assert ", on a 16 GB laptop" in text
