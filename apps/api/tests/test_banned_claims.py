"""F4's public words, read as a customer reads them (plan T8.3; spec BR18, AC99).

- The consent step and chat's setup page carry BR18's sentence, word for word, taken from the
  approved spec so the three cannot drift.
- No customer-facing setup page, and none of chat's or the AI settings' screen words, says
  what BR18 forbids: "no way out", "injection-proof", "proved safe", "your logs stay on your
  server", or "cheaper than". The last two are refused everywhere rather than only to a
  cloud provider's user, since a page cannot know which one reads it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[3]
SPEC: Final = ROOT / "docs/features/ai-chatbot/spec.md"
CHAT_PAGE: Final = ROOT / "docs/product/setup/chat.md"
SETUP_PAGES: Final = ROOT / "docs/product/setup"
CONSENT_WORDS: Final = ROOT / "apps/web/src/features/aisettings/ai-words.ts"
# What a person sees: the screens' own files, not their tests, which may name a banned phrase to
# prove it absent.
SCREEN_WORDS: Final = (
    CONSENT_WORDS,
    *(
        path
        for folder in ("assistant", "runbooks")
        for path in sorted((ROOT / "apps/web/src/features" / folder).glob("*.ts*"))
        if ".test." not in path.name
    ),
)
BANNED: Final = re.compile(
    r"no way out|injection[- ]proof|proved safe|your logs stay on your server|cheaper than",
    re.IGNORECASE,
)


def _flat(text: str) -> str:
    """Markdown and TypeScript wrap long sentences; a sentence is read with its breaks closed."""
    return re.sub(r"\s+", " ", re.sub(r'"\s*\+\s*"', "", text))


def _br18_sentence() -> str:
    match = re.search(
        r"\*\*BR18\. No promise beyond the facts\.\*\* [^\"]*\"([^\"]+)\"", _flat(SPEC.read_text())
    )
    assert match, "BR18's sentence was not found in the spec"
    return match.group(1)


def test_the_sentence_is_the_one_the_spec_approved() -> None:
    assert _br18_sentence().startswith("Chat has no tool that can write.")


@pytest.mark.parametrize("path", [CHAT_PAGE, CONSENT_WORDS], ids=lambda path: path.name)
def test_the_consent_step_and_the_setup_page_carry_br18s_sentence(path: Path) -> None:
    assert _br18_sentence() in _flat(path.read_text(encoding="utf-8"))


def test_the_setup_index_links_chats_page() -> None:
    assert "(chat.md)" in (SETUP_PAGES / "index.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "path",
    [*sorted(SETUP_PAGES.rglob("*.md")), *SCREEN_WORDS],
    ids=lambda path: str(path.relative_to(ROOT)),
)
def test_no_public_text_makes_a_banned_claim(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    found = [
        f"line {text.count(chr(10), 0, match.start()) + 1}: {match.group(0)!r}"
        for match in BANNED.finditer(text)
    ]
    assert not found, f"{path.relative_to(ROOT)} says what BR18 forbids: {found}"
