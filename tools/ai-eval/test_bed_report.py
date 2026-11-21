"""The two reports: each skeleton says what it waits for and holds no result, the checked-in files
are current, and the filled forms say what they must (F3 plan T2.17, T6.3; AC61, AC62, AC63)."""

from __future__ import annotations

from pathlib import Path

import bed_report
import forged as forged_module
import healthy as healthy_module
import model_scoring
from bed_builders import decision, staged
from fixtures.cases import INPUTS
from model_runs import FAKES, load_inputs, record_inputs, replay
from score import WARNING, score_all


def test_the_checked_in_reports_are_what_the_skeletons_render() -> None:
    assert bed_report.RULES_REPORT.read_text(encoding="utf-8") == bed_report.skeleton_rules(), (
        "run `uv run python tools/ai-eval/bed_report.py`"
    )
    assert bed_report.BED_REPORT.read_text(encoding="utf-8") == bed_report.skeleton_bed(), (
        "run `uv run python tools/ai-eval/bed_report.py`"
    )


def test_the_rules_skeleton_says_what_it_waits_for_and_holds_no_result() -> None:
    text = bed_report.skeleton_rules()

    assert "Not yet run" in text and "Oracle after Phase 2" in text
    assert "11.5 days" in text and "7 days" in text and "facts log" in text
    assert "|" not in text, "a skeleton has no table: there is no result to put in one"
    assert "CAUSE_BAR_RECORDED" in text and "Demo results on synthetic data" in text


def test_the_bed_skeleton_names_the_founders_run_and_says_nothing_was_called() -> None:
    text = bed_report.skeleton_bed()

    assert "Not yet run: the founder's, after Phase 2" in text
    assert "No real provider was called" in text and "fake providers only" in text
    assert "stand-ins" in text and "paid providers are not yet tested" in text.lower()
    assert "|" not in text
    assert "injection-proof" in text  # the sentence saying it never means that


def test_both_skeletons_repeat_what_passing_will_not_prove() -> None:
    for text in (bed_report.skeleton_rules(), bed_report.skeleton_bed()):
        assert "What passing these tests will not prove" in text
        assert "says nothing about the model" in text


def test_a_filled_models_report_labels_fakes_as_test_doubles_and_states_what_was_sent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inputs.jsonl"
    record_inputs(path, INPUTS)
    records = replay(dict(list(FAKES.items())[:3]), load_inputs(path), trials=1)
    scored = model_scoring.score_all(records, INPUTS)

    text = bed_report.render_bed_report(
        scored, model_scoring.cost_by_provider(records), machine="a test"
    )

    assert "test doubles" in text and "Paid providers are not yet tested" in text
    assert "only synthetic data went" in text and "not priced" in text
    assert "## Injection" in text and "## Wording" in text and "## Cost" in text


def test_a_filled_rules_report_has_counts_the_bar_and_the_warning_and_no_percentages() -> None:
    built, planted = staged("adFailure", "ad", 3)
    scored = score_all([decision("adFailure", "ad", 3, built=built, planted=planted)])
    healthy = healthy_module.HealthyResult(0, (), 0, ())
    forged = forged_module.ForgedReport({}, {"dependency_edge": 5})

    text = bed_report.render_rules_report(scored, healthy, forged)

    assert WARNING in text
    assert "| signed_deploy | 1 | 0 | silent |" in text
    assert "| dependency_edge | 5 | 5 | missed |" in text
    assert "Healthy run, no cause stated at all: **met**" in text
    assert "%" not in text
