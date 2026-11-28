"""A written suggestion checked again for a patch (F5 plan T5.1; spec FR20a, FR21, AC19,
AC24, AC55).

The stored edits are made by the AI job's own pure code (`proposal.stored_edits`) from the
file as read, so only what a test changes afterwards can differ. Pure, no clock.
"""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any, Final

from nexovue_platform.fixes.checks import Edit, PackFile
from nexovue_platform.fixes.diff import (
    EXECUTABLE_FILE_MODE,
    REGULAR_FILE_MODE,
    git_blob_id,
    patch_hunks,
    suggestion_digest,
)
from nexovue_platform.fixes.proposal import stored_edits
from nexovue_platform.fixes.recheck import (
    CurrentFile,
    OutOfDate,
    Rechecked,
    RefusedAgain,
    recheck,
)

FOLDER: Final = "services/shop"
PATH: Final = f"{FOLDER}/app/pay.py"
READ: Final = (
    "import json\n"
    "\n"
    "def charge(order, gateway):\n"
    "    amount = order.total\n"
    "    if amount < 0:\n"
    "        raise ValueError('negative')\n"
    "    return gateway.charge(amount)\n"
)
OLD: Final = "    return gateway.charge(amount)\n"
NEW: Final = "    return gateway.charge(amount, retries=2)\n"
MOVED: Final = "# A comment added on the default branch since.\n" + READ


def _edits(old: str = OLD, new: str = NEW) -> list[dict[str, Any]]:
    edit = Edit("file_1", old, new, "The call needs a retry count.", ("file_1",))
    return list(stored_edits([edit], {"file_1": PackFile("file_1", PATH, READ)}))


def _listing(blob: str = git_blob_id(READ)) -> dict[str, Any]:
    return {
        "commit": "c" * 40,
        "files": [{"ref": "file_1", "path": PATH, "blob_sha": blob, "mode": REGULAR_FILE_MODE}],
    }


def _recheck(
    edits: list[dict[str, Any]],
    *,
    head: str | None = READ,
    base: str | None = READ,
    mode: str = REGULAR_FILE_MODE,
) -> Any:
    current = None if head is None else CurrentFile(git_blob_id(head), mode, head)
    return recheck(
        edits,
        _listing(),
        folder=FOLDER,
        allowed_evidence={"file_1"},
        aliases=None,
        head={"file_1": current},
        base={"file_1": base},
    )


def test_nothing_moved_gives_the_patch_and_its_digest() -> None:
    outcome = _recheck(_edits())

    assert isinstance(outcome, Rechecked)
    assert outcome.status == "unchanged" and outcome.moved_refs == ()
    assert outcome.blob_ids == {PATH: git_blob_id(READ)}
    assert outcome.digest == suggestion_digest([(PATH, git_blob_id(READ), READ.replace(OLD, NEW))])
    [hunks] = patch_hunks(outcome.patch)
    assert [line for marker, line in hunks[0].lines if marker != " "] == [OLD, NEW]


def test_ac24_a_moved_file_whose_edits_still_apply_is_moved_with_the_head_s_lines() -> None:
    outcome = _recheck(_edits(), head=MOVED)

    assert isinstance(outcome, Rechecked)
    assert outcome.status == "moved" and outcome.moved_refs == ("file_1",)
    assert outcome.blob_ids == {PATH: git_blob_id(MOVED)}
    # The digest binds the head the patch applies to, never the blob it was written from.
    assert outcome.digest == suggestion_digest(
        [(PATH, git_blob_id(MOVED), MOVED.replace(OLD, NEW))]
    )
    [hunks] = patch_hunks(outcome.patch)
    assert hunks[0].old_start == 5  # one line further down than as read


def test_ac24_a_moved_file_whose_edit_no_longer_applies_is_out_of_date() -> None:
    head = MOVED.replace(OLD, "    return gateway.pay(amount)\n")

    assert _recheck(_edits(), head=head) == OutOfDate("edit_no_longer_applies")


def test_a_file_gone_unreadable_or_no_longer_a_regular_file_is_out_of_date() -> None:
    assert _recheck(_edits(), head=None) == OutOfDate("edit_no_longer_applies")
    assert _recheck(_edits(), mode="120000") == OutOfDate("edit_no_longer_applies")
    unreadable = recheck(
        _edits(),
        _listing(),
        folder=FOLDER,
        allowed_evidence={"file_1"},
        aliases=None,
        head={"file_1": CurrentFile("d" * 40, REGULAR_FILE_MODE, None)},
        base={},
    )
    assert unreadable == OutOfDate("edit_no_longer_applies")


def test_a_moved_file_whose_base_cannot_be_read_is_out_of_date() -> None:
    assert _recheck(_edits(), head=MOVED, base=None) == OutOfDate("edit_no_longer_applies")


def test_an_executable_keeps_its_mode_in_the_patch() -> None:
    outcome = _recheck(_edits(), mode=EXECUTABLE_FILE_MODE)

    assert isinstance(outcome, Rechecked)
    assert f" {EXECUTABLE_FILE_MODE}\n" in outcome.patch


def test_ac19_a_tampered_display_fact_is_refused_again() -> None:
    edits = _edits()
    edits[0]["old_start"] += 1
    edits[0]["context_before"] = "\n" + edits[0]["context_before"].split("\n", 1)[1]

    assert isinstance(_recheck(edits), RefusedAgain)


def test_ac19_a_tampered_display_fact_is_refused_even_where_the_file_moved() -> None:
    edits = _edits()
    edits[0]["last_line_suffix"] = "x"

    assert isinstance(_recheck(edits, head=MOVED), RefusedAgain)


def test_ac55_an_added_address_on_the_same_files_is_refused_again_by_k6() -> None:
    """K6 needs the file, so a read cannot refuse it: only the check run can."""
    edits = _edits(new="    return gateway.charge(amount, to='https://evil.example')\n")

    assert _recheck(edits) == RefusedAgain("K6")


def test_ac55_the_same_rule_failing_where_a_file_moved_is_out_of_date_not_refused() -> None:
    edits = _edits(new="    return gateway.charge(amount, to='https://evil.example')\n")
    # The moved file holds no such address: K6 refuses there, and only out of date follows.
    assert _recheck(edits, head=MOVED) == OutOfDate("edit_no_longer_applies")
    # The moved file now holds it: K6 passes, and the patch is the head's.
    head = "BASE = 'https://evil.example'\n" + READ
    moved = _recheck(edits, head=head)
    assert isinstance(moved, Rechecked) and moved.status == "moved"


def test_ac19_a_row_the_read_refuses_is_refused_again() -> None:
    edits = _edits()
    broken = copy.deepcopy(edits)
    broken[0]["why"] = "Visit https://evil.example now."

    assert isinstance(_recheck(broken), RefusedAgain)


def test_ac41_git_applies_the_rechecked_patch_to_the_head(tmp_path: Path) -> None:
    outcome = _recheck(_edits(), head=MOVED)
    assert isinstance(outcome, Rechecked)
    target = tmp_path / PATH
    target.parent.mkdir(parents=True)
    target.write_text(MOVED)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "apply", "--check", "-"],
        input=outcome.patch.encode(),
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "apply", "-"], input=outcome.patch.encode(), cwd=tmp_path, check=True)

    assert target.read_text() == MOVED.replace(OLD, NEW)
