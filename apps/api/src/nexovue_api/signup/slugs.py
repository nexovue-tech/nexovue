"""Turning a company's name into the identifier that appears in URLs."""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from nexovue_api.data.postgres.models import SLUG_MAX_LENGTH

_NON_SLUG_CHARACTERS: Final = re.compile(r"[^a-z0-9]+")

# What an organization called "###" gets, so a slug is never empty.
FALLBACK_SLUG: Final = "org"


def slugify(name: str) -> str:
    """A lowercase, hyphen-separated identifier, bounded to the column width."""
    collapsed = _NON_SLUG_CHARACTERS.sub("-", _transliterate(name).strip().lower()).strip("-")
    # Trimming can land on a hyphen, which would be a trailing separator.
    return collapsed[:SLUG_MAX_LENGTH].strip("-") or FALLBACK_SLUG


def _transliterate(name: str) -> str:
    """Fold accents onto their base letters, so "Ünïcôde" slugs as "unicode".

    Without this every accented character becomes a separator and a perfectly
    ordinary European company name turns into a row of hyphens.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def disambiguate(base: str, attempt: int) -> str:
    """The `attempt`-th candidate for a base slug that is already taken.

    The suffix has to fit inside the column, so the base gives way to it rather
    than the other way round.
    """
    if attempt == 0:
        return base
    suffix = f"-{attempt + 1}"
    return f"{base[: SLUG_MAX_LENGTH - len(suffix)].strip('-')}{suffix}"
