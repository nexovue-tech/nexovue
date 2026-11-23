"""Text patterns shared by the rules on diff lines (`checks.py`) and on the model's words
(`prose.py`): addresses (K6), hidden and look-alike letters (K7), and string literals.

One module so the two cannot drift apart (design, *The rules*). Every pattern here is a
guess, with what it misses stated in the design's table.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from typing import Final

# K6: a URL with any scheme, as a token for the "not already in that file" comparison: a
# letter, then letters, digits and `+.-` up to `://`, then the URL's tail. Each pattern is one
# character class, and `_url_spans` joins them in code, so no pattern can re-read a run that
# fails to become a URL (CodeQL py/polynomial-redos reads any repeated class followed by
# something that can fail as quadratic, a guard like a lookbehind notwithstanding).
_SCHEME_RUN: Final = re.compile(r"[a-z0-9+.-]+", re.IGNORECASE)
_SCHEME_LETTER: Final = re.compile(r"[a-z]", re.IGNORECASE)
_SCHEME_END: Final = "://"
_URL_TAIL: Final = re.compile(r"[^\s'\"`<>()\[\]{}]*")
_OCTET: Final = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
# Four dot-separated numbers, each up to 255; a version `1.2.3` has three. A dot is part of
# the address only before another number, so one ending a sentence is still an address.
IPV4: Final = re.compile(rf"(?<![0-9])(?<![0-9]\.){_OCTET}(?:\.{_OCTET}){{3}}(?![0-9])(?!\.[0-9])")
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
    found = [text[start:end] for start, end in _url_spans(text)]
    found += [match.group(0) for match in IPV4.finditer(text)]
    places = [text] if hosts_anywhere else string_literals(text)
    for place in places:
        found += [match.group(0) for match in HOST_NAME.finditer(without_urls(place))]
    return found


def without_urls(text: str) -> str:
    """The text with each URL replaced by one space, so no rule reads inside one twice."""
    pieces: list[str] = []
    kept_from = 0
    for start, end in _url_spans(text):
        pieces += [text[kept_from:start], " "]
        kept_from = end
    pieces.append(text[kept_from:])
    return "".join(pieces)


def _url_spans(text: str) -> Iterator[tuple[int, int]]:
    """Where each URL starts and ends, left to right, as a search for the whole URL would
    find them. A URL starts at the first letter of a run of scheme characters that `://`
    follows; the digits and signs before that letter stay outside it. A run inside the
    tail of the URL before is part of that URL, not a new one. Each character is read a
    fixed number of times: in its run, in that run's search for a letter, and in a tail."""
    url_end = 0
    for run in _SCHEME_RUN.finditer(text):
        if run.start() < url_end or not text.startswith(_SCHEME_END, run.end()):
            continue
        letter = _SCHEME_LETTER.search(text, run.start(), run.end())
        if letter is None:
            continue
        tail = _URL_TAIL.match(text, run.end() + len(_SCHEME_END))
        assert tail is not None  # A pattern that may match nothing always matches.
        url_end = tail.end()
        yield letter.start(), url_end


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
