"""What a pack may hold (F5 plan T3.10; spec FR12, FR12a, FR13, AC12, AC12f, AC13, AC45,
AC81).

Pure: the builder decides which files the job may fetch and keeps; the caps hold in order,
stack files before imports before tests; a file left out is listed by name only when it
exists in the tree, and a name matching nothing is a count.
"""

from __future__ import annotations

from typing import Final

import pytest
from nexovue_platform.fixes.pack import (
    PackBuilder,
    TreeFile,
    files_digest,
    owner_lists_hash,
)
from nexovue_platform.fixes.policy import OwnerLists
from nexovue_platform.fixes.records import (
    MAX_FILES,
    MAX_SENT_BYTES,
    WHOLE_FILE_MAX_BYTES,
    WINDOWED_FILE_MAX_BYTES,
)

SHA: Final = "a" * 40


def _tree(*files: tuple[str, int], mode: str = "100644") -> dict[str, TreeFile]:
    return {path: TreeFile(path, mode, SHA, size) for path, size in files}


def _take(builder: PackBuilder, path: str, data: bytes, **why: object) -> bool:
    if not builder.consider(path, "stack", {"kind": "stack", **why}, ()):
        return False
    return builder.admit(path, data) is not None


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("prod.env", "secret_or_key"),
        (".ENV", "secret_or_key"),
        ("services/a/.github/x.py", "ci"),
        ("id_ed25519", "secret_or_key"),
        ("config/prod.py", "configuration"),
        ("application-prod.yml", "not_source"),
        ("notes.txt", "not_source"),
    ],
)
def test_ac12_a_listed_or_unsourced_file_is_never_fetched_and_is_named(
    path: str, reason: str
) -> None:
    builder = PackBuilder(tree=_tree((path, 10)))

    assert not builder.consider(path, "stack", {"kind": "stack"})
    assert [(each.path, each.reason) for each in builder.left_out] == [(path, reason)]


def test_ac12_and_ac12g_a_name_matching_no_file_is_a_count_never_a_path() -> None:
    builder = PackBuilder(tree=_tree(("src/a.py", 10)))

    assert not builder.consider("src/forged.py", "stack", {"kind": "stack"})
    assert builder.unmatched == 1
    assert builder.left_out == []


def test_an_owner_may_allow_a_configuration_path_by_name() -> None:
    owner = OwnerLists(allowed_config_paths=("config/prod.py",))
    builder = PackBuilder(tree=_tree(("config/prod.py", 10)), owner=owner)

    assert builder.consider("config/prod.py", "stack", {"kind": "stack"})


def test_ac13_a_secret_shaped_file_is_dropped_before_it_is_kept_and_listed() -> None:
    key = "AKIA" + "QWERTYUIOPASDFGH"
    builder = PackBuilder(tree=_tree(("src/keys.py", 40)))

    assert not _take(builder, "src/keys.py", f'aws = "{key}"\n'.encode())
    assert builder.files == []
    assert [each.reason for each in builder.left_out] == ["secret_pattern"]


@pytest.mark.parametrize(
    ("data", "reason"),
    [(b"x = 1\x00\n", "not_text"), (b"\xff\xfe\xfa", "not_text")],
)
def test_ac45_a_nul_byte_or_bytes_that_are_not_text_are_left_out(data: bytes, reason: str) -> None:
    builder = PackBuilder(tree=_tree(("src/a.py", len(data))))

    assert not _take(builder, "src/a.py", data)
    assert builder.left_out[0].reason == reason
    assert builder.left_out[0].bytes == len(data)


def test_ac45_a_file_over_256_kib_is_left_out_with_its_bytes_and_never_fetched() -> None:
    size = WINDOWED_FILE_MAX_BYTES + 1
    builder = PackBuilder(tree=_tree(("src/big.py", size)))

    assert not builder.consider("src/big.py", "stack", {"kind": "stack"}, (10,))
    assert (builder.left_out[0].reason, builder.left_out[0].bytes) == ("too_large", size)


def test_ac45_a_13th_file_is_left_out_and_listed() -> None:
    paths = [f"src/f{n}.py" for n in range(MAX_FILES + 1)]
    builder = PackBuilder(tree=_tree(*((path, 10) for path in paths)))

    for path in paths:
        _take(builder, path, b"x = 1\n")

    assert len(builder.files) == MAX_FILES
    assert [(each.path, each.reason) for each in builder.left_out] == [(paths[-1], "over_file_cap")]


def test_ac45_and_ac81_the_64_kib_total_keeps_earlier_files_before_later_ones() -> None:
    chunk = b"x" * (WHOLE_FILE_MAX_BYTES - 10) + b"\n"
    paths = ["src/stack1.py", "src/stack2.py", "src/imported.py"]
    builder = PackBuilder(tree=_tree(*((path, len(chunk)) for path in paths)))

    for path in paths:
        _take(builder, path, chunk)

    assert [file.path for file in builder.files] == paths[:2]
    assert builder.sent_bytes <= MAX_SENT_BYTES
    assert builder.left_out[0].reason == "over_byte_cap"


def test_ac12f_a_100_kib_top_file_sends_only_a_window_and_keeps_its_whole_text() -> None:
    lines = [f"line_{number} = {number}\n" for number in range(1, 6001)]
    data = "".join(lines).encode()
    assert WHOLE_FILE_MAX_BYTES < len(data) < WINDOWED_FILE_MAX_BYTES
    builder = PackBuilder(tree=_tree(("src/big.py", len(data))))

    assert builder.consider("src/big.py", "stack", {"kind": "stack", "line": 3000}, (3000,))
    kept = builder.admit("src/big.py", data)

    assert kept is not None and kept.window is not None
    start, end = kept.window
    assert start <= 3000 <= end
    assert kept.sent_bytes <= WHOLE_FILE_MAX_BYTES
    assert kept.content == data.decode()


def test_a_large_file_with_no_line_is_left_out() -> None:
    size = WHOLE_FILE_MAX_BYTES + 100
    builder = PackBuilder(tree=_tree(("src/big.py", size)))

    assert not builder.consider("src/big.py", "imported", {"kind": "imported_by"})
    assert builder.left_out[0].reason == "no_line_for_window"


def test_a_symlink_or_submodule_is_not_a_regular_file() -> None:
    builder = PackBuilder(tree=_tree(("src/link.py", 10), mode="120000"))

    assert not builder.consider("src/link.py", "stack", {"kind": "stack"})
    assert builder.left_out[0].reason == "not_a_regular_file"


def test_frames_are_numbers_against_refs_only() -> None:
    builder = PackBuilder(tree=_tree(("src/a.py", 10)))
    builder.note_frame("src/a.py", 7)
    builder.note_frame("src/forged.py", 9)
    _take(builder, "src/a.py", b"x = 1\n")

    assert builder.frames() == [{"ref": "file_1", "line": 7}]


def test_the_digest_follows_the_list_not_its_order() -> None:
    one = files_digest([("a.py", SHA), ("b.py", "b" * 40)])

    assert one == files_digest([("b.py", "b" * 40), ("a.py", SHA)])
    assert one != files_digest([("a.py", SHA)])
    assert owner_lists_hash(OwnerLists()) != owner_lists_hash(OwnerLists(("x.py",), ()))
