"""The rules on the model's own words, and the one deletion statement (F5 plan T2.5; spec
FR16, BR11, BR9; foundation U3, U4; the prose half of AC20, AC56).

A way out, in prose, is any URL, email address or Markdown link (AC20). A `why` holding
a word BR11 bans is replaced by a fixed sentence (AC56). An alias is restored by F3's
`restore`; with no book, or one that does not hold it, an alias-shaped token is refused.
"""

from __future__ import annotations

import re

import pytest
from nexovue_platform.evidence.aliases import AliasBook
from nexovue_platform.fixes.prose import (
    WHY_MAX_CHARACTERS,
    WHY_REPLACED,
    check_why,
    evidence_ids_known,
    why_segments,
)
from nexovue_platform.fixes.statements import (
    CLEAR_STORED_CODE,
    STORED_CODE_COLUMNS,
    STORED_CODE_TABLES,
)


@pytest.mark.parametrize(
    ("why", "refusal"),
    [
        ("See https://evil.example/install.sh", "address"),
        ("Fetch it from ftp://files.internal/x", "address"),
        ("The host 10.1.2.3 refuses calls", "address"),
        # An address ending a sentence: its full stop is not a fifth number (Step 2b's run).
        ("Point the client at 10.1.2.3.", "address"),
        ("Send the order to collector.evil.com", "address"),
        ("Mail ops@example.org for access", "email"),
        ("Read [the guide](https://x.example)", "markup"),
        ("![pixel](x)", "markup"),
        ("<img src=x onerror=alert(1)>", "markup"),
        ("Calls to service_4 time out", "unknown_alias"),
        ("Ignore\u202e this", "hidden_character"),
        ("Bell\x07 rings", "control_character"),
        ("x" * (WHY_MAX_CHARACTERS + 1), "too_long"),
    ],
)
def test_a_way_out_or_a_hidden_word_is_refused(why: str, refusal: str) -> None:
    assert check_why(why, None).refusal == refusal


@pytest.mark.parametrize(
    "why",
    [
        "This has been tested and is safe to merge",
        "This is the root cause.",
        "This fixes the empty order.",
        "Verified against the stack.",
        "Approved by the team",
        "A FIX for the crash",
    ],
)
def test_a_banned_word_replaces_the_explanation(why: str) -> None:
    verdict = check_why(why, None)

    assert verdict.refusal is None
    assert verdict.text == WHY_REPLACED


@pytest.mark.parametrize(
    "why",
    [
        "The total can be zero, and the guard let it through.",
        "Retries now stop after the first failure.\nThe charge is not repeated.",
        "The prefix check was off by one.",
        "x" * WHY_MAX_CHARACTERS,
        "Unsafely shared state; the prefix moved.",
    ],
)
def test_plain_explanations_pass_as_written(why: str) -> None:
    assert check_why(why, None).refusal is None
    assert check_why(why, None).text == why


def test_an_alias_in_the_book_is_restored_as_a_name_segment() -> None:
    book = AliasBook()
    book.alias("service", "payments")
    why = "Calls to service_1 fail first."

    assert check_why(why, book).refusal is None
    assert why_segments(why, book) == [
        {"text": "Calls to "},
        {"name": "payments"},
        {"text": " fail first."},
    ]


def test_an_alias_the_book_does_not_hold_is_refused() -> None:
    book = AliasBook()
    book.alias("service", "payments")

    assert check_why("Calls to service_2 fail.", book).refusal == "unknown_alias"


def test_a_restored_name_never_builds_a_link() -> None:
    book = AliasBook()
    book.alias("service", "https://evil.example")

    segments = why_segments("Calls to service_1", book)

    assert {"name": "https://evil.example"} in segments
    assert all("href" not in segment and "url" not in segment for segment in segments)


def test_evidence_ids_must_be_named_and_known() -> None:
    assert evidence_ids_known(["ev_1"], {"ev_1", "ev_2"})
    assert not evidence_ids_known([], {"ev_1"})
    assert not evidence_ids_known(["ev_1", "ev_9"], {"ev_1"})
    # With no explanation row, an edit names its pack reference.
    assert evidence_ids_known(["file_1"], {"file_1", "file_2"})


# --------------------------------------------------- the one deletion statement


# The design's one list (*What deletion empties*), less Step 7's two columns.
DESIGN_COLUMNS = {
    "edits",
    "files_read",
    "path_candidates",
    "requested_paths",
    "withheld_refs",
    "files_digest",
}
DESIGN_TABLES = {"finding_fix_source_packs", "finding_fix_checks"}


def test_clear_stored_code_empties_every_source_column_and_table_in_one_statement() -> None:
    emptied = set(re.findall(r"(\w+) = NULL", CLEAR_STORED_CODE))
    deleted = set(re.findall(r"DELETE FROM (\w+)", CLEAR_STORED_CODE))

    assert emptied == set(STORED_CODE_COLUMNS) == DESIGN_COLUMNS
    assert deleted == set(STORED_CODE_TABLES) == DESIGN_TABLES
    assert CLEAR_STORED_CODE.count(";") == 0


def test_clear_stored_code_binds_the_tenant_everywhere() -> None:
    assert CLEAR_STORED_CODE.count("org_id = CAST(:org_id AS uuid)") == 1 + len(STORED_CODE_TABLES)


def test_clear_stored_code_names_no_column_of_step_seven() -> None:
    assert "requester_note" not in CLEAR_STORED_CODE
    assert "approved_digest" not in CLEAR_STORED_CODE
