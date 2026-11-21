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

from nexovue_platform.evidence.aliases import ALIAS_KINDS, AliasBook, restore
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
_BANNED: Final = re.compile(
    r"\b(?:" + "|".join(word.replace(" ", r"\s+") for word in BANNED_WORDS) + r")\b",
    re.IGNORECASE,
)
_EMAIL: Final = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A Markdown link or image, an autolink, or an HTML tag: markup a renderer may follow.
_MARKUP: Final = re.compile(r"\[[^\]]*\]\([^)]*\)|!\[|<[a-z/!][^>]*>", re.IGNORECASE)
# F3's alias shape (`service_1`, `version_2`): a stand-in the API must restore.
ALIAS_TOKEN: Final = re.compile(rf"\b(?:{'|'.join(ALIAS_KINDS)})_[0-9]+\b")
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
    if _EMAIL.search(text):
        return "email"
    if _MARKUP.search(text):
        return "markup"
    if addresses_in(text, hosts_anywhere=True):
        return "address"
    if any(book is None or book.name_of(alias) is None for alias in ALIAS_TOKEN.findall(text)):
        return "unknown_alias"
    return None


def check_why(text: str, book: AliasBook | None) -> ProseVerdict:
    """FR16 on one `why`: refused, replaced for a banned word, or shown as written."""
    refusal = prose_refusal(text, book)
    if refusal is not None:
        return ProseVerdict(refusal=refusal, text="")
    if _BANNED.search(text):
        return ProseVerdict(refusal=None, text=WHY_REPLACED)
    return ProseVerdict(refusal=None, text=text)


def why_segments(text: str, book: AliasBook) -> list[dict[str, str]]:
    """The checked words as F3's segments, each alias put back as a name segment by F3's
    `restore`, so restoring can never build a link (AC56)."""
    segments: list[dict[str, str]] = []
    position = 0
    for match in ALIAS_TOKEN.finditer(text):
        if match.start() > position:
            segments.append({"text": text[position : match.start()]})
        segments.append({"alias": match.group(0)})
        position = match.end()
    if position < len(text):
        segments.append({"text": text[position:]})
    return restore(segments, book)


def evidence_ids_known(evidence_ids: Collection[str], allowed: Collection[str]) -> bool:
    """U3: an edit names at least one id, and only ids that were in its input."""
    return bool(evidence_ids) and all(each in allowed for each in evidence_ids)
