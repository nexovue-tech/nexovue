"""AC13: the words reliability targets must never say, scanned in what they say.

Whole words, ignoring case, in the user-facing text of each named path: string literals
and JSX text, not identifiers or import paths. Each forbidden word has a named
allow-list of the phrases that may carry it. A named path holding no file fails, so a
moved screen cannot pass by being absent (plan T1.8). T3.6, T4.9, T5.7 and T6.6 add
their paths here.

T3.6 adds the target's tabs and the *Last month* view by name, and "error budget",
which only the *Definition* tab may say (AC13). T4.9 adds the `slo_burn` messages'
wording, `alerts/slo_words.py`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

REPOSITORY: Final = Path(__file__).resolve().parents[3]
# What the scan reads, each with the files it must find there.
SCANNED: Final = {
    "apps/web/src/features/slos": ("*.ts", "*.tsx"),
    # T4.9: what a burn's messages and notices say.
    "apps/scheduler/src/nexovue_scheduler/alerts": ("slo_words.py",),
}
# Files each step names, so a renamed tab cannot leave the scan by being absent.
NAMED: Final = (
    "apps/web/src/features/slos/SloMonthsTab.tsx",
    "apps/web/src/features/slos/SloHistoryTab.tsx",
    "apps/web/src/features/slos/SloDefinitionTab.tsx",
    "apps/web/src/features/slos/LastMonthView.tsx",
    "apps/web/src/features/slos/month-words.ts",
    "apps/scheduler/src/nexovue_scheduler/alerts/slo_words.py",
)
# A phrase one file alone may say, beyond `FORBIDDEN`'s allow-lists (AC13: "error
# budget" only on the Definition tab).
ALLOWED_IN: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "SloDefinitionTab.tsx": {"budget": ("error budget",)},
}
# Test files say what a screen must not say, so they are not what a screen says.
NOT_SCANNED: Final = re.compile(r"\.test(-util)?\.tsx?$")

# AC13's words, each with the phrases that may carry it (lower case).
FORBIDDEN: Final[dict[str, tuple[str, ...]]] = {
    "sla": ("nexovue does not do slas",),
    "uptime": (),
    "tamper-proof": (),
    "audit-grade": (),
    "exact": (),
    "exactly": ("exactly on the target",),
    "budget": (),
    "burning": (),
    # D16: in the page title and the nav only, both of which say "SLOs".
    "slo": (),
}

_STRING: Final = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`")
_JSX_TEXT: Final = re.compile(r">([^<>{}]+)<")
# A placeholder inside a string, `${count}` or Python's `{slo.noun(kind)}`, is code.
_PLACEHOLDER: Final = re.compile(r"\$?\{[^{}]*\}")
_IMPORT: Final = re.compile(r"^\s*(import|export)\b.*\bfrom\b|^\s*import\s", re.MULTILINE)


def said(source: str) -> list[str]:
    """The text a file shows people: its string literals and JSX text, not its imports."""
    lines = [line for line in source.splitlines() if not _IMPORT.match(line)]
    kept = "\n".join(lines)
    return [_PLACEHOLDER.sub(" ", match.group(0)[1:-1]) for match in _STRING.finditer(kept)] + [
        match.group(1) for match in _JSX_TEXT.finditer(kept)
    ]


def offences(text: str, *, also_allowed: dict[str, tuple[str, ...]] | None = None) -> list[str]:
    found = []
    lowered = text.lower()
    extra = also_allowed or {}
    for word, allowed in FORBIDDEN.items():
        cleaned = lowered
        for phrase in (*allowed, *extra.get(word, ())):
            cleaned = cleaned.replace(phrase, " ")
        if re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", cleaned):
            found.append(word)
    return found


def _files() -> list[Path]:
    found: list[Path] = []
    for folder, patterns in SCANNED.items():
        root = REPOSITORY / folder
        matched = [
            path
            for pattern in patterns
            for path in sorted(root.rglob(pattern))
            if not NOT_SCANNED.search(path.name)
        ]
        assert matched, f"{folder} holds no file to scan: it has moved, so this test must follow"
        found.extend(matched)
    return found


@pytest.mark.parametrize("path", _files(), ids=lambda path: path.name)
def test_no_forbidden_word_is_said(path: Path) -> None:
    allowed = ALLOWED_IN.get(path.name, {})
    hits = {
        text: found
        for text in said(path.read_text(encoding="utf-8"))
        if (found := offences(text, also_allowed=allowed))
    }
    assert hits == {}, f"{path.relative_to(REPOSITORY)} says: {hits}"


@pytest.mark.parametrize("named", NAMED)
def test_every_named_screen_file_exists_to_be_scanned(named: str) -> None:
    assert (REPOSITORY / named).is_file(), f"{named} is gone: this test must follow it"


def test_error_budget_is_said_on_the_definition_tab_and_allowed_nowhere_else() -> None:
    definition = (REPOSITORY / NAMED[2]).read_text(encoding="utf-8")
    assert any("error budget" in text.lower() for text in said(definition))
    assert offences("Your error budget") == ["budget"]


@pytest.mark.parametrize(
    ("text", "word"),
    [
        ("Your uptime is fine", "uptime"),
        ("Error Budget left", "budget"),
        ("Exact counts", "exact"),
        ("It is exactly right", "exactly"),
        ("An SLA report", "sla"),
        ("Burning fast", "burning"),
        ("Each SLO here", "slo"),
    ],
)
def test_the_scan_finds_each_word_whole_and_in_any_case(text: str, word: str) -> None:
    assert word in offences(text)


def test_an_allowed_phrase_and_a_part_of_a_word_pass() -> None:
    assert offences("99.90% against 99.9%, exactly on the target.") == []
    assert offences("Reliability targets (SLOs)") == []
    assert offences("exactness was_burning") == []


def test_the_scan_reads_strings_and_jsx_but_not_imports_or_names() -> None:
    source = (
        'import { liveState } from "./slo-words";\n'
        "const wasBurning = true;\n"
        'const words = "Counting";\n'
        "<p>Uptime here</p>\n"
    )
    assert said(source) == ["Counting", "Uptime here"]


def test_a_placeholder_in_a_string_is_code_not_words() -> None:
    assert offences(said('f"{slo.allowance_words(kind)}: 3 of {allowed}"')[0]) == []
    assert offences(said("`${budget} left`")[0]) == []
