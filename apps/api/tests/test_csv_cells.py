"""A cell a spreadsheet would run is written as text (group 9, B2; OWASP CSV injection)."""

from __future__ import annotations

import pytest

from nexovue_api.exports.csv_cells import spreadsheet_safe


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r", "\n"])
def test_a_cell_beginning_with_a_formula_character_is_prefixed(lead: str) -> None:
    assert spreadsheet_safe(f"{lead}cmd") == f"'{lead}cmd"


@pytest.mark.parametrize("text", ["", "checkout", "payment failed: =x", "a-b"])
def test_any_other_text_is_untouched(text: str) -> None:
    assert spreadsheet_safe(text) == text
