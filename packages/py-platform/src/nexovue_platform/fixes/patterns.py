"""Text patterns shared by the rules on diff lines (`checks.py`) and on the model's words
(`prose.py`): addresses (K6), hidden and look-alike letters (K7), and string literals.

One module so the two cannot drift apart (design, *The rules*). Every pattern here is a
guess, with what it misses stated in the design's table.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

# K6: any scheme, `https://`, `ftp://`, `javascript://` and the rest.
URL_SCHEME: Final = re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE)
# A URL as a token, for the "not already in that file" comparison.
URL_TOKEN: Final = re.compile(r"[a-z][a-z0-9+.-]*://[^\s'\"`<>()\[\]{}]*", re.IGNORECASE)
_OCTET: Final = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
# Four dot-separated numbers, each up to 255; a version `1.2.3` has three.
IPV4: Final = re.compile(rf"(?<![0-9.]){_OCTET}(?:\.{_OCTET}){{3}}(?![0-9.])")
# The checked-in list of last labels a host name is recognised by (design, K6).
HOST_LAST_LABELS: Final = (
    "com",
    "net",
    "org",
    "io",
    "dev",
    "ai",
    "app",
    "co",
    "sh",
    "xyz",
    "example",
)
HOST_NAME: Final = re.compile(
    rf"(?<![\w.-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:{'|'.join(HOST_LAST_LABELS)})(?![\w-])",
    re.IGNORECASE,
)

# K7's look-alike scripts: letters a reader takes for Latin ones (a guess).
LOOK_ALIKE_SCRIPTS: Final = ("CYRILLIC", "GREEK", "ARMENIAN", "CHEROKEE", "COPTIC")
_WORD: Final = re.compile(r"\w+")
# Hangul fillers and the like: letters that draw nothing, beyond the format category.
_BLANK_LETTERS: Final = frozenset({"\u115f", "\u1160", "\u3164", "\uffa0"})
_VARIATION_SELECTORS: Final = (range(0xFE00, 0xFE10), range(0xE0100, 0xE01F0))

_QUOTES: Final = "\"'`"


def addresses_in(text: str, *, hosts_anywhere: bool) -> list[str]:
    """Every URL, IPv4 address and, inside a string literal (or anywhere when
    `hosts_anywhere`), host name with a listed last label."""
    found = [match.group(0) for match in URL_TOKEN.finditer(text)]
    found += [match.group(0) for match in IPV4.finditer(text)]
    places = [text] if hosts_anywhere else string_literals(text)
    for place in places:
        found += [match.group(0) for match in HOST_NAME.finditer(URL_TOKEN.sub(" ", place))]
    return found


def string_literals(line: str) -> list[str]:
    """The insides of the quoted strings on one line, as `"…"`, `'…'` or a backtick.

    One line at a time, with backslash escapes; a string left open runs to the line's
    end, which errs toward reading more text as a string.
    """
    literals: list[str] = []
    quote: str | None = None
    current: list[str] = []
    escaped = False
    for character in line:
        if quote is None:
            if character in _QUOTES:
                quote, current = character, []
            continue
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == quote:
            literals.append("".join(current))
            quote = None
        else:
            current.append(character)
    if quote is not None:
        literals.append("".join(current))
    return literals


def is_hidden_character(character: str) -> bool:
    """Invisible, bidirectional and tag characters: Unicode's format category holds
    zero-width spaces and joiners, the bidirectional controls and the tag block."""
    point = ord(character)
    return (
        unicodedata.category(character) == "Cf"
        or character in _BLANK_LETTERS
        or any(point in block for block in _VARIATION_SELECTORS)
    )


def hidden_characters(text: str) -> list[str]:
    return [character for character in text if is_hidden_character(character)]


def _script(character: str) -> str | None:
    """`LATIN`, a look-alike script, `FORM` for a full-width or styled Latin letter, or None."""
    if not character.isalpha():
        return None
    if character.isascii():
        return "LATIN"
    folded = unicodedata.normalize("NFKC", character)
    if folded != character and folded.isascii() and folded.isalpha():
        return "FORM"
    name = unicodedata.name(character, "")
    if name.startswith("LATIN"):
        return "LATIN"
    return next((script for script in LOOK_ALIKE_SCRIPTS if name.startswith(script)), "OTHER")


def mixed_script_words(text: str) -> list[str]:
    """Words mixing Latin letters with look-alike letters of another script, or with
    full-width and styled forms of Latin ones (K7)."""
    mixed = []
    for word in _WORD.findall(text):
        scripts = {_script(character) for character in word} - {None, "OTHER"}
        if "LATIN" in scripts and len(scripts) > 1:
            mixed.append(word)
    return mixed
