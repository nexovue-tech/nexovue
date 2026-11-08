"""A removed person, taken out of reliability targets' free text (F19 AC88; design,
*Inventories*).

`members.scrub` replaces a person's address and name only where a value *is* one
(`_without_person`). A target's name and description, and its change history's
`before` and `after`, are words a member typed, so the person may sit inside them:
"Checkout for Priya Raman". Here their address and full name are replaced wherever
they appear as whole words, ignoring case, in every row of the organization, live or
deleted, whoever wrote it. A first name or a nickname cannot be found, and the docs
say so. People's ids need no change: the user row stays, renamed by the scrub.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Final

from nexovue_platform import slo
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.slos import queries

# The columns' own limits, so a longer replacement never breaks a row's CHECK.
_LONGEST: Final = {"name": slo.NAME_LONGEST, "description": slo.DESCRIPTION_LONGEST}


async def scrub_person(
    db: AsyncSession, *, org_id: Any, address: str, name: str | None, replacement: str
) -> None:
    """Replace the person's address and full name in every target and change of the
    organization, in the caller's transaction."""
    words = [word for word in (address, name) if word]
    pattern = _whole_words(words)
    for target in await queries.targets_naming(db, org_id=org_id, needles=words):
        for column, longest in _LONGEST.items():
            replaced = pattern.sub(replacement, getattr(target, column))
            setattr(target, column, replaced[:longest])
    # JSON text escapes a quote or a backslash, so the prefilter looks for each word
    # as `jsonb::text` writes it.
    as_json = [json.dumps(word, ensure_ascii=False)[1:-1] for word in words]
    for change in await queries.changes_naming(db, org_id=org_id, needles=as_json):
        change.before = _replaced(change.before, pattern, replacement)
        change.after = _replaced(change.after, pattern, replacement)


def _whole_words(words: Sequence[str]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


def _replaced(value: Any, pattern: re.Pattern[str], replacement: str) -> Any:
    """Every string at any depth, rebuilt, so the JSONB column sees the change."""
    if isinstance(value, str):
        return pattern.sub(replacement, value)
    if isinstance(value, dict):
        return {key: _replaced(item, pattern, replacement) for key, item in value.items()}
    if isinstance(value, list):
        return [_replaced(item, pattern, replacement) for item in value]
    return value
