"""Whether a consent covers a suggestion (F5 plan T3.5; spec FR6, FR8, AC9, AC12c).

Live only while the provider's full address, format, key id and model all equal what the
owner agreed to; a folder's consent covers itself and what is under it, never a sibling
sharing a prefix; a whole-repository consent covers every folder.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final

import pytest
from nexovue_platform.fixes.consent import (
    ProviderTerms,
    folder_covered,
    normal_folder,
    terms_match,
)

AGREED: Final = ProviderTerms("https://api.provider.example/v1", "openai", "key-1", "model-a")


@pytest.mark.parametrize(
    ("label", "change"),
    [
        ("the address", {"base_url": "https://other.example/v1"}),
        ("the format", {"wire_format": "anthropic"}),
        ("a new key", {"key_id": "key-2"}),
        ("the model", {"model": "model-b"}),
    ],
)
def test_ac9_any_change_to_the_provider_ends_consent(label: str, change: dict[str, str]) -> None:
    assert not terms_match(AGREED, replace(AGREED, **change)), label


def test_ac9_the_same_terms_keep_it_and_ai_off_ends_it() -> None:
    assert terms_match(AGREED, replace(AGREED))
    assert not terms_match(AGREED, None)


def test_ac12c_a_folder_consent_covers_itself_and_below_never_a_sibling() -> None:
    assert folder_covered("services/a", "services/a")
    assert folder_covered("services/a", "services/a/api")
    assert not folder_covered("services/a", "services/ab")
    assert not folder_covered("services/a", "services/b")
    assert not folder_covered("services/a", None)
    assert folder_covered(None, "services/b")
    assert folder_covered(None, None)


@pytest.mark.parametrize(
    ("typed", "stored"), [("", None), ("/", None), ("services/a/", "services/a"), (" src ", "src")]
)
def test_a_folder_is_stored_plainly(typed: str, stored: str | None) -> None:
    assert normal_folder(typed) == stored


@pytest.mark.parametrize("typed", ["/abs", "a/../b", "a//b", "a\x07b", "x" * 201])
def test_fr5b_a_folder_that_climbs_or_hides_is_refused(typed: str) -> None:
    with pytest.raises(ValueError, match="folder"):
        normal_folder(typed)
