"""F4 FR40: chat's send-time masks, applied to every tool result before the model reads it,
and to the consent preview's samples, so an owner reads what would really be sent.

Credential forms intake masking may not reach, and email addresses unless an admin turned
that off. **A net with holes:** names and customer ids stay, and the consent says so.
"""

from __future__ import annotations

import re
from typing import Any, Final

MASKED: Final = "[masked]"
_CREDENTIALS: Final = (
    re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"),
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b(\s*[:=]\s*)\S+",
    ),
)
_EMAIL: Final = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def mask(text: str, *, mask_emails: bool) -> str:
    for pattern in _CREDENTIALS:
        text = pattern.sub(_masked, text)
    return _EMAIL.sub(MASKED, text) if mask_emails else text


def _masked(match: re.Match[str]) -> str:
    groups = match.groups()
    if len(groups) == 2 and groups[1] is not None:  # `password = value`: keep the name
        return f"{groups[0]}{groups[1]}{MASKED}"
    if len(groups) == 1 and groups[0] is not None:  # `Bearer value`
        return f"{groups[0]} {MASKED}"
    return MASKED


def masked_value(value: Any, *, mask_emails: bool) -> Any:
    if isinstance(value, str):
        return mask(value, mask_emails=mask_emails)
    if isinstance(value, dict):
        return {key: masked_value(item, mask_emails=mask_emails) for key, item in value.items()}
    if isinstance(value, list):
        return [masked_value(item, mask_emails=mask_emails) for item in value]
    return value
