"""The chat bed's report and command line (F4 plan Step 4; foundation founder decision 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chat.answers import CANONICAL_ANCHOR, questions
from chat.bed import done, main, outside_repository
from chat.report import (
    FAKE_BANNER,
    NOT_REAL_TRAFFIC,
    REPOSITORY,
    InsideRepositoryError,
    render,
    write_report,
)
from chat.test_chat_score import record

KNOWN = {each.id: each for each in questions(CANONICAL_ANCHOR)}


def test_the_report_refuses_a_path_inside_the_repository(tmp_path: Path) -> None:
    with pytest.raises(InsideRepositoryError, match="outside"):
        write_report(REPOSITORY / "docs" / "research" / "f4-chat.md", "x")
    with pytest.raises(InsideRepositoryError):
        outside_repository(REPOSITORY / "records.jsonl")
    written = write_report(tmp_path / "report.md", "x")
    assert written.read_text(encoding="utf-8") == "x"


def test_a_fake_run_is_labelled_fake_and_partial_and_says_it_is_not_real_traffic() -> None:
    text = render([record("requests-hour-checkout", option="fake-answer-key")], KNOWN)
    assert FAKE_BANNER in text
    assert NOT_REAL_TRAFFIC in text
    assert "(fake provider: not a result)" in text
    assert "Partial run" in text
    assert "is supported" not in text and "Supported:" not in text


def test_a_real_option_has_no_fake_banner() -> None:
    text = render([record("requests-hour-checkout", option="gemini-free")], KNOWN)
    assert FAKE_BANNER not in text


def test_a_saved_trial_is_skipped_when_the_run_starts_again() -> None:
    saved = [record("requests-hour-checkout", option="local")]
    assert done(saved, "local") == {("chat", "requests-hour-checkout", 1)}
    assert done(saved, "other") == set()


def test_a_real_option_needs_its_address_and_model() -> None:
    with pytest.raises(SystemExit, match="--base-url and --model"):
        main(["run", "--option", "local", "--records", "/tmp/never.jsonl"])
