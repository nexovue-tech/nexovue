"""Which rule refusals show admins a notice, and its words (F5 plan T9.3; spec UR12, AC39,
AC63; open decision 89).

K2, K7, K9 and K10 may mean a planted instruction, so each shows a notice. K6 and K8 refuse
honest code more often, so they stay quiet until the founder has read T6.3's replay report:
a date recorded in code, like the write gate, which no setting can set.
"""

from __future__ import annotations

import ast
import inspect
import re
from datetime import date
from typing import Final

import pytest
from nexovue_platform.fixes import notices
from nexovue_platform.fixes.sentences import REASON_SENTENCES, WITHHELD_RULE_WORDS

BANNED: Final = re.compile(
    r"\b(?:tested|verified|safe|root cause|approved|injection-proof|passed|fix|fixes|fixed)\b"
    r"|your data never leaves|a fix\b|Fix App",
    re.IGNORECASE,
)


def test_as_shipped_only_the_four_planted_text_rules_notice() -> None:
    assert notices.REPLAY_REPORT_READ is None
    assert notices.notice_rules() == ("K2", "K7", "K9", "K10")


def test_k6_and_k8_join_once_the_replay_report_is_read() -> None:
    assert notices.notice_rules(date(2027, 1, 4)) == ("K2", "K6", "K7", "K8", "K9", "K10")


def test_the_read_date_is_a_constant_no_setting_reaches() -> None:
    source = inspect.getsource(notices)
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not imported & {"os", "pydantic", "pydantic_settings", "sqlalchemy"}
    assigned = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and "REPLAY_REPORT_READ" in ast.unparse(node).split("=")[0]
    ]
    assert len(assigned) == 1


@pytest.mark.parametrize("rule", notices.ALWAYS + notices.HELD_UNTIL_REPLAY_READ)
def test_a_notice_names_its_rule_in_the_rule_s_own_words(rule: str) -> None:
    sentence = notices.notice_sentence(rule)

    assert sentence.startswith(f"Rule {rule} withheld a suggestion because it ")
    assert WITHHELD_RULE_WORDS[rule] in sentence
    assert not BANNED.search(sentence.replace("suggestion", ""))


def test_the_requester_s_sentences_are_built_from_the_same_words() -> None:
    for rule, words in WITHHELD_RULE_WORDS.items():
        assert words in REASON_SENTENCES[f"withheld_{rule}"].requester
