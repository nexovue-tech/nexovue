"""The worker's default masking rules, ported for flat text the API stores (F28 FR9, AC53).

The deploy webhook stores a sender's `metadata`, and the default credential rules must
mask it first. They live in the worker (`apps/workers/internal/pipeline/mask`), which
Python cannot import, so this ports the six default families with nothing configured:
credential keys, authorization headers, cookies, authorization credentials in free text,
card numbers, and URL credentials. Both read
`packages/contracts/fixtures/metadata-masking.json`, so a change to one that the other
does not share fails a test.

Case folding is ASCII here, so a scan's positions stay those of the original text; every
marker the rules look for is ASCII.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Final

PLACEHOLDER: Final = "[masked]"

# `rules.go`: key substrings naming a credential, and "token" when not plural.
_CREDENTIAL_KEY_MARKERS: Final = ("password", "secret", "api_key", "apikey", "api-key")
_TOKEN: Final = "token"
_AUTHORIZATION_HEADERS: Final = ("authorization", "proxy-authorization")
_COOKIE_HEADERS: Final = ("cookie", "set-cookie")

# `bearer.go`.
_SCHEMES: Final = ("bearer ", "basic ")
_AUTHORIZATION_MARKER: Final = "auth"
_AUTHORIZATION_REACH: Final = 24
_MIN_CREDENTIAL_LENGTH: Final = 20
_WORD: Final = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_CREDENTIAL: Final = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~+/="
)

# `card.go`.
_MIN_CARD_DIGITS: Final = 13
_MAX_CARD_DIGITS: Final = 19
_DIGITS: Final = frozenset("0123456789")
_CARD_SEPARATORS: Final = frozenset(" -")

# `url.go`.
_SENSITIVE_QUERY_PARAMETERS: Final = (
    "X-Amz-Credential",
    "X-Amz-Security-Token",
    "X-Amz-Signature",
    "X-Goog-Signature",
    "sig",
)
_PASSWORD_IN_URL: Final = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:)([^\s/@]+)@")
_SENSITIVE_PARAMETER: Final = re.compile(
    r"([?&#](?:" + "|".join(map(re.escape, _SENSITIVE_QUERY_PARAMETERS)) + r")=)([^&#\s\"'<>]+)"
)

_ASCII_LOWER: Final = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def mask_flat(entries: Mapping[str, str]) -> dict[str, str]:
    """Each value masked as the worker masks a string attribute under that key."""
    return {key: _mask_entry(key, value) for key, value in entries.items()}


def _mask_entry(key: str, value: str) -> str:
    if _masks_key(key):
        return PLACEHOLDER
    value = _mask_authorization_credentials(value)
    value = _mask_card_numbers(value)
    return _mask_url_credentials(value)


# ----------------------------------------------------------------------- keys


def _masks_key(key: str) -> bool:
    lowered = key.lower()
    if any(marker in lowered for marker in _CREDENTIAL_KEY_MARKERS):
        return True
    if _contains_singular_token(lowered):
        return True
    return _header_name_in(lowered, _AUTHORIZATION_HEADERS) or _header_name_in(
        lowered, _COOKIE_HEADERS
    )


def _contains_singular_token(lowered: str) -> bool:
    start = 0
    while (at := lowered.find(_TOKEN, start)) >= 0:
        after = at + len(_TOKEN)
        if after >= len(lowered) or lowered[after] != "s":
            return True
        start = after
    return False


def _header_name_in(lowered: str, names: tuple[str, ...]) -> bool:
    cut = max(lowered.rfind("."), lowered.rfind("_"))
    tail = lowered[cut + 1 :] if cut >= 0 else lowered
    return any(tail == name or lowered.endswith("." + name) for name in names)


# ------------------------------------------------- authorization credentials


def _mask_authorization_credentials(text: str) -> str:
    if len(text) < len(_SCHEMES[0]) + _MIN_CREDENTIAL_LENGTH:
        return text
    lowered = text.translate(_ASCII_LOWER)
    built: list[str] = []
    changed = False
    cursor = 0
    while cursor < len(text):
        start, scheme = _next_scheme(lowered, cursor)
        if start < 0:
            break
        credential_at = start + len(scheme)
        end = credential_at
        while end < len(text) and text[end] in _CREDENTIAL:
            end += 1
        if end - credential_at < _MIN_CREDENTIAL_LENGTH or not _in_authorization_context(
            lowered, start, end
        ):
            built.append(text[cursor:credential_at])
            cursor = credential_at
            continue
        built.append(text[cursor:credential_at])
        built.append(PLACEHOLDER)
        changed = True
        cursor = end
    if not changed:
        return text
    built.append(text[cursor:])
    return "".join(built)


def _next_scheme(lowered: str, start: int) -> tuple[int, str]:
    best, found = -1, ""
    for candidate in _SCHEMES:
        at = start
        while (offset := lowered.find(candidate, at)) >= 0:
            at = offset
            if at == 0 or lowered[at - 1] not in _WORD:
                break
            at += len(candidate)
        else:
            continue
        if best < 0 or at < best:
            best, found = at, candidate
    return best, found


def _in_authorization_context(lowered: str, start: int, credential_end: int) -> bool:
    if _AUTHORIZATION_MARKER in lowered[max(0, start - _AUTHORIZATION_REACH) : start]:
        return True
    leading = lowered[:start].lstrip(" \t\"'")
    trailing = lowered[credential_end:].rstrip(" \t\"',}")
    return leading == "" and trailing == ""


# -------------------------------------------------------------- card numbers


def _mask_card_numbers(text: str) -> str:
    if not _has_enough_digits(text):
        return text
    built: list[str] = []
    index = copied = 0
    while index < len(text):
        if text[index] not in _DIGITS:
            index += 1
            continue
        end, digits = _scan_card_run(text, index)
        if _is_card_number(text[index:end], digits):
            built.append(text[copied:index])
            built.append(PLACEHOLDER)
            copied = end
        index = end
    if copied == 0:
        return text
    built.append(text[copied:])
    return "".join(built)


def _has_enough_digits(text: str) -> bool:
    if len(text) < _MIN_CARD_DIGITS:
        return False
    run = 0
    for character in text:
        if character in _DIGITS:
            run += 1
            if run >= _MIN_CARD_DIGITS:
                return True
        elif character not in _CARD_SEPARATORS:
            run = 0
    return False


def _scan_card_run(text: str, start: int) -> tuple[int, int]:
    end, digits = start, 0
    while end < len(text):
        if text[end] in _DIGITS:
            digits += 1
            end += 1
        elif text[end] in _CARD_SEPARATORS and end + 1 < len(text) and text[end + 1] in _DIGITS:
            end += 1
        else:
            break
    return end, digits


def _is_card_number(run: str, digits: int) -> bool:
    return (
        _MIN_CARD_DIGITS <= digits <= _MAX_CARD_DIGITS
        and _issued_by_a_known_network(run, digits)
        and _luhn_valid(run)
    )


def _issued_by_a_known_network(run: str, digits: int) -> bool:
    prefix = ("".join(c for c in run if c in _DIGITS)[:4]).ljust(4, "0")

    def between(width: int, low: int, high: int) -> bool:
        return low <= int(prefix[:width]) <= high

    checks: tuple[tuple[Callable[[], bool], Callable[[int], bool]], ...] = (
        (lambda: prefix[0] == "4", lambda n: n in (13, 16, 19)),
        (lambda: between(2, 51, 55) or between(4, 2221, 2720), lambda n: n == 16),
        (lambda: between(2, 34, 34) or between(2, 37, 37), lambda n: n == 15),
        (
            lambda: between(4, 6011, 6011) or between(2, 65, 65) or between(3, 644, 649),
            lambda n: n in (16, 19),
        ),
        (lambda: between(4, 3528, 3589), lambda n: 16 <= n <= 19),
        (
            lambda: between(3, 300, 305) or between(2, 36, 36) or between(2, 38, 39),
            lambda n: n == 14,
        ),
        (lambda: between(2, 62, 62), lambda n: 16 <= n <= 19),
    )
    for issued, lengths in checks:
        if issued():
            return lengths(digits)
    return False


def _luhn_valid(run: str) -> bool:
    total = position = 0
    for character in reversed(run):
        if character not in _DIGITS:
            continue
        digit = int(character)
        if position % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
        position += 1
    return position > 0 and total % 10 == 0


# ------------------------------------------------------------ URL credentials


def _mask_url_credentials(text: str) -> str:
    if "://" not in text and "?" not in text:
        return text
    text = _PASSWORD_IN_URL.sub(lambda match: f"{match.group(1)}{PLACEHOLDER}@", text)
    return _SENSITIVE_PARAMETER.sub(lambda match: f"{match.group(1)}{PLACEHOLDER}", text)
