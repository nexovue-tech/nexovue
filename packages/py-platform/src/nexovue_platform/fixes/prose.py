"""The rules on the model's own words: `why` on each edit (spec FR16; foundation U3, U4;
BR11), and later a reason for asking for files and the requester's note.

A compromised AI process can write these words, so the same checks run at the model's
return, at every read and at every check run (design, *The prose checks*). Model words
are shown as plain text, so the danger is a way out: a URL, an email address, a
Markdown link or a host name a browser, a chat tool or a reader's own AI may follow.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Final, Literal

from nexovue_platform.evidence.aliases import ALIAS_TOKEN, AliasBook, restored_segments
from nexovue_platform.fixes.patterns import addresses_in, hidden_characters

# FR16's cap (a guess).
WHY_MAX_CHARACTERS: Final = 300
# FR16: what a `why` holding a word BR11 bans reads instead.
WHY_REPLACED: Final = (
    "The model's explanation was removed because it used a word Nexovue does not allow."
)
# BR11's words. A bare "fix" is banned, so are its plain inflections, read as claims
# that the change fixes something (a guess at the boundary).
BANNED_WORDS: Final = (
    "tested",
    "safe",
    "verified",
    "approved",
    "root cause",
    "fix",
    "fixes",
    "fixed",
)
BANNED: Final = re.compile(
    r"\b(?:" + "|".join(word.replace(" ", r"\s+") for word in BANNED_WORDS) + r")\b",
    re.IGNORECASE,
)
# Tried only where a run of address characters starts, so a long run with no `@` costs one
# pass, not one per character (CodeQL py/polynomial-redos).
EMAIL: Final = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A Markdown link or image, an autolink, or an HTML tag: markup a renderer may follow. Each
# is found by its opening, then its close anywhere after (`has_markup`).
_IMAGE_OPENING: Final = "!["
# A link's text in brackets, then `(`; brackets inside it start a later one.
_LINK_OPENING: Final = re.compile(r"\[[^\][]*\]\(")
_TAG_OPENING: Final = re.compile(r"<[a-z/!]", re.IGNORECASE)
_ALLOWED_CONTROLS: Final = frozenset({"\n", "\t"})

ProseRefusal = Literal[
    "too_long",
    "control_character",
    "hidden_character",
    "address",
    "email",
    "markup",
    "unknown_alias",
]


@dataclass(frozen=True, slots=True)
class ProseVerdict:
    """`refusal` is None when the words may be shown; `text` is what is shown then,
    which is `WHY_REPLACED` when a banned word was found."""

    refusal: ProseRefusal | None
    text: str


def prose_refusal(text: str, book: AliasBook | None) -> ProseRefusal | None:
    """Why the words cannot be shown at all, or None."""
    if len(text) > WHY_MAX_CHARACTERS:
        return "too_long"
    if any((ord(c) < 0x20 and c not in _ALLOWED_CONTROLS) or ord(c) == 0x7F for c in text):
        return "control_character"
    if hidden_characters(text):
        return "hidden_character"
    if EMAIL.search(text):
        return "email"
    if has_markup(text):
        return "markup"
    if addresses_in(text, hosts_anywhere=True):
        return "address"
    if any(book is None or book.name_of(alias) is None for alias in ALIAS_TOKEN.findall(text)):
        return "unknown_alias"
    return None


def has_markup(text: str) -> bool:
    """`[text](target)`, `![` or `<tag…>`. Only the first opening of each is looked for: a
    close after any later opening is after the first too, so a run of openings is one pass."""
    if _IMAGE_OPENING in text:
        return True
    link = _LINK_OPENING.search(text)
    if link is not None and ")" in text[link.end() :]:
        return True
    tag = _TAG_OPENING.search(text)
    return tag is not None and ">" in text[tag.end() :]


def check_why(text: str, book: AliasBook | None) -> ProseVerdict:
    """FR16 on one `why`: refused, replaced for a banned word, or shown as written."""
    refusal = prose_refusal(text, book)
    if refusal is not None:
        return ProseVerdict(refusal=refusal, text="")
    if BANNED.search(text):
        return ProseVerdict(refusal=None, text=WHY_REPLACED)
    return ProseVerdict(refusal=None, text=text)


def why_segments(text: str, book: AliasBook) -> list[dict[str, str]]:
    """The checked words as F3's segments, each alias put back as a name segment, so
    restoring can never build a link (AC56)."""
    return restored_segments(text, book)


def evidence_ids_known(evidence_ids: Collection[str], allowed: Collection[str]) -> bool:
    """U3: an edit names at least one id, and only ids that were in its input."""
    return bool(evidence_ids) and all(each in allowed for each in evidence_ids)
