"""Whether an owner's source consent still covers a suggestion (spec FR6, FR8; design,
`fix_source_consents`; ADR-084).

Pure, so the API (at Ask, Send and each check run), the pack job and the AI job ask the
same question in one place. **Consent is worked out at each use**, by comparing what the
owner agreed to with the AI connection as it is now: no path can forget to end it, and no
statement ever touches a consent row to end it (AC9). Saving a new key changes its id;
re-sealing under key rotation keeps it, so rotation does not end consent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# A folder as an admin types it (FR5b): relative, no `..`, no control characters.
FOLDER_MAX_CHARACTERS: Final = 200
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class ProviderTerms:
    """What a consent binds, and what the live AI connection says now (FR6)."""

    base_url: str
    wire_format: str
    key_id: str | None
    model: str


def terms_match(agreed: ProviderTerms, current: ProviderTerms | None) -> bool:
    """FR8: the full address, the format, the key's id and the model, all unchanged."""
    return current is not None and agreed == current


def normal_folder(folder: str | None) -> str | None:
    """A folder as stored: no leading or trailing slash, and None for the whole repository.
    Refuses (ValueError) a folder FR5b forbids."""
    if folder is None:
        return None
    stripped = folder.strip()
    if stripped in ("", "/", "."):
        return None
    if (
        stripped.startswith("/")
        or _CONTROL.search(stripped)
        or len(stripped) > FOLDER_MAX_CHARACTERS
    ):
        raise ValueError("folder")
    segments = stripped.rstrip("/").split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError("folder")
    return "/".join(segments)


def folder_covered(scope: str | None, folder: str | None) -> bool:
    """Whether a consent's scope covers a code location's folder. A whole-repository
    consent covers every folder; a folder's consent covers itself and what is under it,
    never its parent nor a sibling sharing a prefix (`services/a` does not cover
    `services/ab`)."""
    if scope is None:
        return True
    if folder is None:
        return False
    return folder == scope or folder.startswith(scope + "/")
