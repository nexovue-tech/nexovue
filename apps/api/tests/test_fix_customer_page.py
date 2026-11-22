"""F5's customer page, read as a customer reads it (plan T9.5; spec AC37, AC52's page half,
AC58's page half, AC62).

- **AC37:** none of the banned words, outside the named allow-list ("Suggested fix" and rung
  2b's two names, and code identifiers in backticks).
- **AC52:** the exact scope sentence, taken from the approved spec so the two cannot drift,
  and the source sentence the founder approved (spec, founder decision 2).
- **AC62:** the login sentence, exactly as the link and draft confirm say it.
- The other sentences the page quotes from the product, exactly: write access off, what
  GitHub Actions is asked to hold, who authors the draft, how far write access reaches, Run
  CI's warning and what is not checked. Plus the facts settled for it: drafts on every GitHub
  plan (D62) and the 90-day case (AC58).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest
from nexovue_platform.fixes.records import WRITES_OFF
from nexovue_platform.fixes.sentences import (
    CONFIRM_AUTHOR,
    CONFIRM_HOLDS,
    CONFIRM_LOGIN,
    CONFIRM_REACH,
    DRAFT_REFUSAL_SENTENCES,
    NOT_CHECKED,
    RUN_CI_CONFIRM,
)

ROOT: Final = Path(__file__).resolve().parents[3]
PAGE: Final = ROOT / "docs/product/setup/ai-suggested-fixes.md"
INDEX: Final = ROOT / "docs/product/setup/index.md"
SPEC: Final = ROOT / "docs/features/ai-solution/spec.md"

BANNED: Final = re.compile(
    r"\b(?:tested|verified|safe|root cause|approved|injection-proof|passed|fix|fixes|fixed)\b"
    r"|your data never leaves|a fix\b|Fix App",
    re.IGNORECASE,
)
ALLOWED: Final = re.compile(
    r"Suggest a general fix|General fix suggestion|general fix suggestion|[Ss]uggested fix(?:es)?"
    r"|`[^`]*`"
)
# The approval marker at the top is the founder's to remove; it is not customer wording.
DRAFT_MARKER: Final = re.compile(r"\A# [^\n]+\n\n(?:> [^\n]*\n)+")
SOURCE_SENTENCE: Final = (
    "Suggested fixes, if you turn them on, send the source files you see listed, from "
    "repositories you agreed to, with the finding's facts and any hint you type, to the AI you "
    "chose. Nothing is sent until you click."
)


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Line breaks and quote markers are layout; a sentence is the same across them."""
    return re.sub(r"\s+", " ", re.sub(r"(?m)^\s*> ?", "", text))


def _scope_sentence() -> str:
    found = re.search(
        r'\*\*The scope sentence every public description must carry:\*\* "([^"]+)"',
        SPEC.read_text(encoding="utf-8"),
    )
    assert found, "the spec no longer states the scope sentence in its usual form"
    return found.group(1)


def test_ac37_the_page_says_no_banned_word() -> None:
    customer_words = DRAFT_MARKER.sub("", _page(), count=1)

    found = BANNED.findall(ALLOWED.sub("", customer_words))

    assert found == []


def test_ac52_the_page_carries_the_exact_scope_sentence() -> None:
    assert _scope_sentence() in _flat(_page())


def test_the_page_carries_the_approved_source_sentence() -> None:
    assert SOURCE_SENTENCE in _flat(_page())


@pytest.mark.parametrize(
    "sentence",
    [
        CONFIRM_LOGIN,
        CONFIRM_HOLDS,
        CONFIRM_AUTHOR,
        CONFIRM_REACH,
        RUN_CI_CONFIRM,
        NOT_CHECKED,
        DRAFT_REFUSAL_SENTENCES[WRITES_OFF],
    ],
    ids=["ac62_login", "holds", "author", "reach", "run_ci", "not_checked", "writes_off"],
)
def test_the_page_quotes_the_product_s_own_sentence_exactly(sentence: str) -> None:
    assert sentence in _flat(_page())


def test_d62_drafts_are_said_to_work_on_every_github_plan() -> None:
    assert "Drafts work on every GitHub plan." in _flat(_page())
    assert "Team or Enterprise" not in _page()


def test_ac58_a_case_left_90_days_leaves_its_pull_request_untouched() -> None:
    flat = _flat(_page())
    assert "A case nobody acts on for 90 days ends by itself." in flat
    assert "left as it is on GitHub, untouched" in flat


def test_the_page_says_branches_to_protect_and_pull_request_target() -> None:
    flat = _flat(_page())
    assert "Protect `main` and every release branch" in flat
    assert "`pull_request_target`" in flat


def test_the_cost_example_is_labelled_an_estimate_with_its_date() -> None:
    assert "This is an estimate, from one provider's prices read on 22 November 2026" in _flat(
        _page()
    )


def test_the_setup_front_page_links_it() -> None:
    assert "(ai-suggested-fixes.md)" in INDEX.read_text(encoding="utf-8")
