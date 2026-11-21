"""A stored suggestion read back without its files (F5 plan T4.7, T4.8; spec AC19's read half,
AC55, AC56; design, *Display facts (D53)*, *Tamper signals*).

Pure: what a read and the sweep believe of `edits`, which is nothing. The hunks rebuilt
from display facts equal the check run's from the whole file; a fact of the wrong shape,
one that disagrees with the old text, a file outside the listing or the folder, a
never-changed file, an added line K7 to K10 refuse, or a `why` FR16 refuses shows no code.
"""

from __future__ import annotations

from typing import Any, Final

import pytest
from nexovue_platform.evidence.aliases import AliasBook
from nexovue_platform.fixes.checks import Edit, PackFile
from nexovue_platform.fixes.diff import Span, file_hunks
from nexovue_platform.fixes.proposal import stored_edits
from nexovue_platform.fixes.stored import Failure, StoredProposal, StoredRefusal, read_stored

PATH: Final = "svc/app.py"
CONTENT: Final = "".join(f"line {n}\n" for n in range(1, 21))
OLD: Final = "line 10\nline 11\n"
NEW: Final = "line ten\n# a comment\n"
LISTING: Final = {"commit": "c" * 40, "files": [{"ref": "file_1", "path": PATH}]}
BOOK: Final = AliasBook({"names": {"service_1": "checkout"}})


def _stored(
    old: str = OLD, new: str = NEW, why: str = "service_1 reads it."
) -> list[dict[str, Any]]:
    edit = Edit("file_1", old, new, why, ("e1",))
    return list(stored_edits([edit], {"file_1": PackFile("file_1", PATH, CONTENT)}))


def _read(edits: Any, *, listing: Any = LISTING, folder: str = "svc") -> Any:
    return read_stored(edits, listing, folder=folder, allowed_evidence={"e1"}, aliases=BOOK)


def test_ac19_hunks_rebuilt_from_facts_equal_the_check_run_s_from_the_file() -> None:
    read = _read(_stored())

    assert isinstance(read, StoredProposal)
    [file] = read.files
    start = CONTENT.index(OLD)
    assert list(file.hunks or []) == file_hunks(CONTENT, [Span(start, start + len(OLD), NEW)])
    assert (file.added, file.removed) == (2, 2)
    assert {flag.kind for flag in read.flags} == {"comment"}


@pytest.mark.parametrize(
    ("label", "tamper"),
    [
        ("a fact of the wrong type", lambda e: e[0].update(old_start="10")),
        ("a fact disagreeing with the old text", lambda e: e[0].update(old_start=3)),
        ("a missing fact", lambda e: e[0].pop("context_after")),
        ("an extra key", lambda e: e[0].update(extra=1)),
        ("a model text unchanged", lambda e: e[0].update(new_text=e[0]["old_text"])),
    ],
)
def test_ac19_facts_that_do_not_hold_show_no_code(label: str, tamper: Any) -> None:
    edits = _stored()
    tamper(edits)

    assert _read(edits) == StoredRefusal("shape"), label


def test_k1_a_file_outside_the_listing_or_the_folder_shows_no_code() -> None:
    edits = _stored()
    edits[0]["file_ref"] = "file_9"

    assert _read(edits) == StoredRefusal("K1")
    assert _read(_stored(), folder="other") == StoredRefusal("K1")


def test_k2_a_never_changed_file_shows_no_code() -> None:
    listing = {"commit": "c" * 40, "files": [{"ref": "file_1", "path": "svc/conftest.py"}]}

    assert _read(_stored(), listing=listing) == StoredRefusal("K2")


@pytest.mark.parametrize(
    ("rule", "line"),
    [
        ("K7", "x = 1 \u202e"),
        ("K8", "blob = 'Zm9vYmFyYmF6cXV4cXV1eHh5enp6QUJDREVGR0hJSktMTU5PUFFS'"),
        ("K9", "token = 'ghp_" + "a1B2c3D4e5" * 4 + "'"),
        ("K10", "x = " + "1 + " * 70 + "1"),
    ],
)
def test_an_added_line_the_rules_refuse_shows_no_code(rule: Failure, line: str) -> None:
    edits = _stored()
    edits[0]["new_text"] = line + "\n"

    assert _read(edits) == StoredRefusal(rule)


def test_ac56_a_why_fr16_refuses_shows_no_code_and_an_unknown_id_too() -> None:
    assert _read(_stored(why="See https://evil.example/x.")) == StoredRefusal("prose")
    edits = _stored()
    edits[0]["evidence_ids"] = ["e9"]
    assert _read(edits) == StoredRefusal("prose")


def test_k5_a_suggestion_too_large_shows_no_code() -> None:
    big = "".join(f"x{n} = {n}\n" for n in range(70))

    assert _read(_stored(new=big)) == StoredRefusal("K5")


def test_context_too_long_to_store_shows_the_file_without_hunks() -> None:
    edits = _stored()
    edits[0]["context_before"] = edits[0]["context_after"] = None

    read = _read(edits)

    assert isinstance(read, StoredProposal)
    assert read.files[0].hunks is None and read.files[0].added == 2
