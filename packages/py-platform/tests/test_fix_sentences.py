"""Every sentence F5 says about a suggestion, walked (F5 plan T2b.7; spec AC8, AC37, AC72).

Each value of `finding_fixes.reason`'s CHECK list has a requester's and a viewer's sentence,
and none says a word AC37 bans: a bare "fix", "tested", "safe", "verified", "approved",
"root cause", "passed" or the rest. "Suggested fix" and rung 2b's two names are AC72's
allow-list.
"""

from __future__ import annotations

import re
from typing import Final

import pytest
from nexovue_platform.fixes import sentences
from nexovue_platform.fixes.records import FIX_REASONS
from nexovue_platform.fixes.sentences import ELIGIBILITY_SENTENCES, REASON_SENTENCES

# AC37's scan.
BANNED: Final = re.compile(
    r"\b(?:tested|verified|safe|root cause|approved|injection-proof|passed|fix|fixes|fixed)\b"
    r"|your data never leaves|a fix\b|Fix App",
    re.IGNORECASE,
)
# AC72's allow-list, and FR1's "Suggested fix", always with "suggested".
ALLOWED: Final = re.compile(
    r"Suggest a general fix|General fix suggestion|general fix suggestion|[Ss]uggested fix(?:es)?"
)


def _every_sentence() -> list[tuple[str, str]]:
    found = [
        (f"{code} ({who})", text)
        for table in (ELIGIBILITY_SENTENCES, REASON_SENTENCES)
        for code, sentence in table.items()
        for who, text in (("requester", sentence.requester), ("viewer", sentence.viewer))
        if text is not None
    ]
    found += [
        (name, value)
        for name, value in vars(sentences).items()
        if name.isupper() and isinstance(value, str)
    ]
    return found


@pytest.mark.parametrize("reason", FIX_REASONS)
def test_ac8_every_stored_reason_has_a_requester_s_and_a_viewer_s_sentence(reason: str) -> None:
    sentence = REASON_SENTENCES[reason]

    assert sentence.requester.endswith(".")
    assert reason in {"ai_off"} or sentence.viewer


@pytest.mark.parametrize(("where", "text"), _every_sentence())
def test_ac37_no_sentence_says_a_banned_word(where: str, text: str) -> None:
    assert not BANNED.search(ALLOWED.sub("", text)), where


def test_ac37_the_scan_finds_a_bare_fix_when_one_is_there() -> None:
    assert BANNED.search(ALLOWED.sub("", "This fixes the timeouts. Suggested fix ready."))
    assert not BANNED.search(ALLOWED.sub("", "Suggest a general fix. Suggested fixes stop."))


def test_ac72_the_label_is_word_for_word() -> None:
    assert sentences.GENERAL_LABEL == (
        "Example pattern, not your code. Nexovue has not seen your code. Check before using."
    )
    assert sentences.GENERAL_HEADING == "General fix suggestion"
