"""Stack paths matched to a folder's tree (F5 plan T3.10; spec FR12, AC12b, AC12e).

Every stack path is attacker-writable, so a match must name exactly one real file: the
longest suffix wins with at least two segments, a path in production maps exactly, a build
folder tries the source, and an ambiguous suffix is dropped, never guessed.
"""

from __future__ import annotations

from typing import Final

from nexovue_platform.fixes.matching import TreeIndex, conventional_test_file, match_path

TREE: Final = TreeIndex(
    [
        "main.py",
        "src/checkout/pay.ts",
        "src/checkout/pay.test.ts",
        "src/api.py",
        "app/src/checkout/pay.py",
        "tests/test_pay.py",
        "billing/a/Handler.java",
        "billing/b/Handler.java",
        "src/main/java/com/acme/billing/Handler.java",
        "src/main/java/com/acme/Handler.java",
        "store/db.go",
        "store/db_test.go",
    ]
)


def test_ac12b_a_compiled_frame_finds_its_typescript_source() -> None:
    assert match_path("/srv/app/dist/checkout/pay.js", TREE).path == "src/checkout/pay.ts"


def test_ac12b_a_site_packages_frame_does_not_match_on_its_file_name_alone() -> None:
    assert match_path("/x/site/api.py", TREE).path is None


def test_the_longest_suffix_names_the_file() -> None:
    assert match_path("/build/app/src/checkout/pay.py", TREE).path == "app/src/checkout/pay.py"


def test_ac12e_a_path_in_production_maps_a_one_segment_file_exactly() -> None:
    assert match_path("/app/main.py", TREE, path_in_production="/app").path == "main.py"
    assert match_path("/app/main.py", TREE).path is None


def test_ac12e_two_handler_files_are_told_apart_by_the_qualified_name() -> None:
    assert (
        match_path("com/acme/billing/Handler.java", TREE).path
        == "src/main/java/com/acme/billing/Handler.java"
    )
    assert match_path("com/acme/Handler.java", TREE).path == "src/main/java/com/acme/Handler.java"


def test_an_ambiguous_suffix_is_dropped_and_said_so() -> None:
    found = match_path("Handler.java", TREE)

    assert found.path is None
    assert found.ambiguous


def test_a_climbing_path_matches_nothing() -> None:
    assert match_path("../../src/api.py", TREE).path is None


def test_a_conventional_test_file_is_found_beside_or_under_a_test_folder() -> None:
    assert conventional_test_file("src/checkout/pay.ts", TREE) == "src/checkout/pay.test.ts"
    assert conventional_test_file("app/src/checkout/pay.py", TREE) == "tests/test_pay.py"
    assert conventional_test_file("store/db.go", TREE) == "store/db_test.go"
    assert conventional_test_file("src/api.py", TREE) is None
