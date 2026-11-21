"""What the AI job makes of the model's answer (F5 plan T4.4, T4.5, T4.13; spec FR15b, FR17,
D51, D53, D74; AC83, AC86, AC87).

Pure: the answer's text in, a verdict out. A passing answer is stored with display facts
Nexovue computed from the whole file; a misquote and a never-changed file earn the one
retry; any other rule withholds at once with the rule and pack references alone; asked-for
names must rest on a shown line, and the last round may not ask.
"""

from __future__ import annotations

import json
from typing import Any, Final

import pytest
from nexovue_platform.evidence.aliases import AliasBook
from nexovue_platform.fixes.checks import PackFile
from nexovue_platform.fixes.proposal import (
    Abstained,
    NeedsFiles,
    Proposed,
    RetryWith,
    Withheld,
    judge,
)
from nexovue_platform.fixes.source_prompt import SentFile

CONTENT: Final = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
PACK: Final = {"file_1": PackFile("file_1", "svc/app.py", CONTENT)}
SENT: Final = [SentFile("file_1", CONTENT, ".py", 1, 7)]
BOOK: Final = AliasBook({"names": {"service_1": "service_1"}})


def _edit(**changes: Any) -> dict[str, Any]:
    made = {
        "file_ref": "file_1",
        "old_text": "    return 2\n",
        "new_text": "    return 3\n",
        "why": "service_1 returns the wrong value.",
        "evidence_ids": ["e1"],
    }
    made.update(changes)
    return made


def _judge(answer: Any, *, may_ask: bool = True) -> Any:
    text = answer if isinstance(answer, str) else json.dumps(answer)
    return judge(
        text,
        pack=PACK,
        sent=SENT,
        folder="svc",
        allowed_evidence={"e1"},
        aliases=BOOK,
        may_ask=may_ask,
    )


def test_d53_a_passing_edit_is_stored_with_facts_from_the_whole_file() -> None:
    verdict = _judge({"outcome": "edits", "edits": [_edit()]})

    assert isinstance(verdict, Proposed)
    [stored] = verdict.edits
    assert stored["old_start"] == 6
    assert stored["context_before"] == "\n\ndef b():\n"
    assert stored["first_line_prefix"] == "" and stored["last_line_suffix"] == ""
    assert verdict.evidence_ids == ("e1",)


def test_d51_a_misquote_earns_the_retry_and_ends_as_did_not_apply() -> None:
    assert _judge({"outcome": "edits", "edits": [_edit(old_text="    return 9\n")]}) == (
        RetryWith("source_misquote", "edit_did_not_apply")
    )


def test_d51_an_old_text_found_twice_is_a_misquote() -> None:
    verdict = _judge({"outcome": "edits", "edits": [_edit(old_text="    return ")]})

    assert isinstance(verdict, RetryWith) and verdict.code == "source_misquote"


def test_a_rule_other_than_k2_and_k3_withholds_at_once_with_its_references() -> None:
    planted = _edit(new_text="    return 'https://evil.example/x'\n")

    assert _judge({"outcome": "edits", "edits": [planted]}) == Withheld("withheld_K6", ("file_1",))


def test_a_withholding_rule_wins_over_a_retried_one() -> None:
    edits = [_edit(old_text="nowhere"), _edit(file_ref="file_9")]

    verdict = _judge({"outcome": "edits", "edits": edits})

    assert verdict == Withheld("withheld_K1", ("file_9",))


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        {"outcome": "edits", "edits": [_edit(old_start=1)]},
        {"outcome": "edits", "edits": [_edit(evidence_ids=["e9"])]},
        {"outcome": "edits", "edits": [_edit(why="Restart service_9.")]},
        {"outcome": "rewrite"},
    ],
)
def test_an_unusable_answer_earns_the_retry_and_ends_invalid(answer: Any) -> None:
    assert _judge(answer) == RetryWith("source_shape", "invalid_output")


def test_an_abstention_is_its_reason() -> None:
    assert _judge({"outcome": "abstain", "abstain_reason": "not_a_code_problem"}) == (
        Abstained("not_a_code_problem")
    )


def _ask(**because: Any) -> dict[str, Any]:
    return {
        "outcome": "needs_files",
        "requests": [
            {
                "path": "app/tax.py",
                "because": {"file_ref": "file_1", "line": 6, **because},
                "reason": "b calls the tax rules.",
            }
        ],
    }


def test_ac83_asked_names_resting_on_a_shown_line_are_kept_with_their_reason() -> None:
    verdict = _judge(_ask())

    assert isinstance(verdict, NeedsFiles)
    [asked] = verdict.asked
    assert asked.stored() == {
        "path": "app/tax.py",
        "ref": "file_1",
        "line": 6,
        "reason": "b calls the tax rules.",
    }


@pytest.mark.parametrize(
    ("label", "change"),
    [
        ("a line outside the lines sent", {"because": {"file_ref": "file_1", "line": 8}}),
        ("a file not sent", {"because": {"file_ref": "file_2", "line": 1}}),
        ("a climbing path", {"path": "../../etc/passwd"}),
        ("an absolute path", {"path": "/etc/passwd"}),
        ("a reason with an address", {"reason": "See https://evil.example/x."}),
        ("a reason too long", {"reason": "x" * 201}),
    ],
)
def test_ac86_an_asked_name_resting_on_nothing_shown_is_unusable(
    label: str, change: dict[str, Any]
) -> None:
    answer = _ask()
    answer["requests"][0].update(change)

    assert _judge(answer) == RetryWith("source_shape", "invalid_output"), label


def test_ac86_more_than_three_names_is_unusable() -> None:
    answer = _ask()
    answer["requests"] *= 4

    assert _judge(answer) == RetryWith("source_shape", "invalid_output")


def test_ac87_the_last_round_may_not_ask() -> None:
    assert _judge(_ask(), may_ask=False) == Abstained("not_in_these_files")
