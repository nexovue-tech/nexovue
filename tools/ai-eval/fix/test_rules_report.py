"""The rules report (F5 plan T2.8): each planted change meets the result it names, the
subtle bug passes, and the checked-in report is what the rules produce now."""

from __future__ import annotations

import pytest

from fix.rules_report import PLANTED, REPORT, Planted, evaluate, report


@pytest.mark.parametrize("planted", PLANTED, ids=[p.id for p in PLANTED])
def test_each_planted_change_meets_its_named_result(planted: Planted) -> None:
    assert evaluate(planted).found == planted.expected


def test_every_rule_stops_at_least_one_planted_change() -> None:
    stopped = {
        p.expected.removeprefix("refused:") for p in PLANTED if p.expected.startswith("refused:")
    }

    assert stopped == {f"K{n}" for n in range(1, 11)}


def test_the_subtle_bug_passes_with_nothing_flagged() -> None:
    [subtle] = [p for p in PLANTED if p.id == "subtle_bug"]

    assert evaluate(subtle).found == "passed"
    assert "passes every rule with no flag" in report()


def test_the_checked_in_report_is_current() -> None:
    assert REPORT.read_text(encoding="utf-8") == report(), (
        "the rules report is out of date: run `uv run python tools/ai-eval/fix/rules_report.py`"
    )
