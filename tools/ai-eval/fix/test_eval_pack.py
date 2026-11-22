"""The bed reads the fixture repository through the pack job's own reader (F5 plan T6.1;
ADR-098): the local repository answers as GitHub's read routes would, the pack is chosen
in the product's order, the bed's two arms change only what they say, forged frames (A1)
steer nothing past the lists, and a later round admits asked-for names as the job does."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from nexovue_platform.fixes.proposal import AskedFile
from nexovue_platform.fixes.records import MAX_ROUNDS

from fix.eval_attacks import MOVES_BY_ID
from fix.eval_pack import (
    HEAD,
    Arm,
    LocalRepository,
    first_round,
    has_imports,
    next_round,
    pack_for,
)
from fix.fixtures import REPOSITORY, Case, load_cases

CASES = {case.id: case for case in load_cases()}
GIT_SECONDS = 30


def case(case_id: str) -> Case:
    return CASES[case_id]


def test_a_blob_id_is_git_s_own() -> None:
    path = REPOSITORY / "services/checkout/checkout/cart.py"
    expected = (
        subprocess.run(
            ["git", "hash-object", "--no-filters", str(path)],
            capture_output=True,
            check=True,
            timeout=GIT_SECONDS,
        )
        .stdout.decode()
        .strip()
    )
    entries = LocalRepository().tree("t", "k", "tree:services/checkout/checkout", recursive=False)

    assert {e.path: e.sha for e in entries}["cart.py"] == expected


def test_the_tree_walks_down_by_name_as_github_s_does(tmp_path: Path) -> None:
    copy = tmp_path / "repository"
    shutil.copytree(REPOSITORY, copy)
    (copy / "services/checkout/checkout/__pycache__").mkdir(exist_ok=True)
    (copy / "services/checkout/checkout/__pycache__/cart.cpython-313.pyc").write_bytes(b"x")
    local = LocalRepository(copy)
    top = local.tree("t", "k", local.tree_of_commit("t", "k", HEAD), recursive=False)
    services = next(e for e in top if e.path == "services")
    below = local.tree("t", "k", services.sha, recursive=True)

    assert {e.path for e in top} >= {".github", "README.md", "services"}
    assert services.kind == "tree"
    assert "checkout/checkout/cart.py" in {e.path for e in below if e.kind == "blob"}
    assert not any("__pycache__" in e.path for e in below)


def test_the_pack_is_stack_then_imports_then_tests_in_the_product_s_order() -> None:
    pack = pack_for(case("py_stack_average"), arm="imports_and_rounds")

    assert [(f.ref, f.path.rsplit("/", 1)[1], f.role) for f in pack.files] == [
        ("file_1", "cart.py", "stack"),
        ("file_2", "handlers.py", "stack"),
        ("file_3", "discount.py", "imported"),
        ("file_4", "test_cart.py", "test"),
    ]
    assert pack.frames == ({"ref": "file_1", "line": 15}, {"ref": "file_2", "line": 24})
    assert pack.round_number == 1


def test_without_imports_drops_imported_files_and_numbers_the_rest_again() -> None:
    pack = pack_for(case("py_stack_average"), arm="no_imports")

    assert [(f.ref, f.role) for f in pack.files] == [
        ("file_1", "stack"),
        ("file_2", "stack"),
        ("file_3", "test"),
    ]
    assert pack.frames == ({"ref": "file_1", "line": 15}, {"ref": "file_2", "line": 24})


@pytest.mark.parametrize(
    ("arm", "imports", "round_number"),
    [
        ("imports_and_rounds", True, 1),
        ("no_imports", False, 1),
        ("no_rounds", True, MAX_ROUNDS),
        ("neither", False, MAX_ROUNDS),
    ],
)
def test_each_arm_says_what_it_changes(arm: Arm, imports: bool, round_number: int) -> None:
    assert has_imports(arm) is imports
    assert first_round(arm) == round_number


def test_forged_frames_steer_an_allowed_file_and_nothing_kept_back() -> None:
    move = MOVES_BY_ID[("A1", "steered_file_edited")]
    pack = pack_for(case("py_stack_average"), arm="imports_and_rounds", forged=move.forged)

    paths = [f.path for f in pack.files]
    assert "services/checkout/checkout/notify.py" in paths
    assert not any("config" in path or ".env" in path for path in paths)
    assert [(e.path, e.reason) for e in pack.left_out] == [
        ("services/checkout/config/timeouts.py", "configuration")
    ]
    assert pack.unmatched == 2  # `.env` and `/etc/passwd`: counted, never stored


def test_a_stack_of_library_frames_only_has_nothing_to_send() -> None:
    pack = pack_for(case("ts_third_party_only"), arm="imports_and_rounds")

    assert pack.files == () and pack.unmatched == 2


def test_a_later_round_admits_asked_names_through_the_lists() -> None:
    receipts = case("py_planted_receipts")
    first = pack_for(receipts, arm="imports_and_rounds")
    asked = [
        AskedFile(".env", "file_1", 3, "r"),
        AskedFile("config/timeouts.py", "file_1", 3, "r"),
        AskedFile("checkout/cart.py", "file_1", 3, "r"),
    ]

    later = next_round(receipts, first, asked)

    assert later.round_number == 2
    assert [(f.ref, f.path.rsplit("/", 1)[1], f.role) for f in later.files] == [
        ("file_1", "receipts.py", "stack"),
        ("file_2", "test_receipts.py", "test"),
        ("file_3", "cart.py", "asked"),
    ]
    assert sorted(later.asked_refused) == ["configuration", "matched_no_file"]
