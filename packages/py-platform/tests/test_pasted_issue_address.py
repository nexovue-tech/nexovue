"""A pasted issue address, parsed to a key and never fetched (F22 plan T6.1; FR40, AC54).

The parser is the only door a pasted address has into a case: what it does not match is
refused before any repository is looked up, and what it does match becomes a strict key
the console builds its own address from (D81). Each hostile shape here has its exact
outcome.
"""

from __future__ import annotations

import pytest
from nexovue_platform.connections import (
    GITHUB,
    MAX_PASTED_ADDRESS_LENGTH,
    PastedIssue,
    parse_issue_address,
)


@pytest.mark.parametrize(
    ("pasted", "expected"),
    [
        ("https://github.com/acme/api/issues/412", PastedIssue(GITHUB, "acme/api", 412)),
        ("https://github.com/acme/api/issues/412/", PastedIssue(GITHUB, "acme/api", 412)),
        ("  https://github.com/acme/api/issues/412\n", PastedIssue(GITHUB, "acme/api", 412)),
        (
            "https://github.com/acme/api/issues/412#issuecomment-99",
            PastedIssue(GITHUB, "acme/api", 412),
        ),
        ("https://github.com/acme/api/issues/412?q=1", PastedIssue(GITHUB, "acme/api", 412)),
        ("https://www.github.com/Acme/API/issues/7", PastedIssue(GITHUB, "Acme/API", 7)),
        (
            "https://GitHub.com/acme/my.repo_1-x/issues/3",
            PastedIssue(GITHUB, "acme/my.repo_1-x", 3),
        ),
        ("acme/api#412", PastedIssue(GITHUB, "acme/api", 412)),
    ],
)
def test_an_issue_address_or_key_is_parsed_to_its_parts(pasted: str, expected: PastedIssue) -> None:
    assert parse_issue_address(pasted) == expected


@pytest.mark.parametrize(
    "pasted",
    [
        "",
        "https://github.com/acme/api/pull/412",
        "https://github.com/acme/api/issues",
        "https://github.com/acme/api/issues/0",
        "https://github.com/acme/api/issues/012",
        "https://github.com/acme/api/issues/412abc",
        "https://github.com/acme/api/issues/12345678901",
        "https://github.com.evil.example/acme/api/issues/1",
        "https://evil.example/github.com/acme/api/issues/1",
        "https://github.com/acme/../issues/1",
        "https://github.com/acme/./issues/1",
        "https://github.com/-acme/api/issues/1",
        "https://github.com/acme/api/issues/1\nhttps://evil.example",
        "ftp://github.com/acme/api/issues/1",
        "javascript:alert(1)//github.com/acme/api/issues/1",
        "https://user@github.com/acme/api/issues/1",
        "https://github.com:8443/acme/api/issues/1",
        "OPS-12",
        "acme/api#0",
        "acme/api",
        "https://github.com/acme/api/issues/1#" + "x" * MAX_PASTED_ADDRESS_LENGTH,
    ],
)
def test_anything_else_is_refused(pasted: str) -> None:
    assert parse_issue_address(pasted) is None


def test_the_key_takes_the_connected_place_s_spelling() -> None:
    pasted = parse_issue_address("https://github.com/ACME/Api/issues/9")

    assert pasted is not None
    assert pasted.key_in("acme/api") == "acme/api#9"
