"""The diff, the git-format patch, the display facts and the digest (F5 plan T2.3; spec
FR20, FR20a; design D53; AC19, AC19a, AC41).

`git apply` is the judge of the patch: over CRLF, no final newline, non-ASCII text and
mode 100755, a real `git apply --check` must accept it, and the tree it leaves must hold
exactly the bytes the draft job would commit, with the mode unchanged (AC41).
"""

from __future__ import annotations

import random
import shutil
import subprocess
from pathlib import Path

import pytest
from nexovue_platform.fixes.diff import (
    DISPLAY_FACTS_MAX_BYTES,
    EXECUTABLE_FILE_MODE,
    REGULAR_FILE_MODE,
    PatchFile,
    Span,
    StoredEdit,
    apply_spans,
    build_patch,
    derived_hunks,
    display_facts,
    facts_have_shape,
    file_hunks,
    git_blob_id,
    patch_hunks,
    suggestion_digest,
)

# A real git is required, never skipped: the patch is only proved by git accepting it.
GIT = shutil.which("git") or "git"


def span_of(content: str, old: str, new: str) -> Span:
    start = content.index(old)
    assert content.count(old) == 1
    return Span(start, start + len(old), new)


def git(repository: Path, *arguments: str, stdin: bytes | None = None) -> str:
    done = subprocess.run(
        [str(GIT), *arguments],
        cwd=repository,
        input=stdin,
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode()
    return done.stdout.decode()


NUMBERED = "".join(f"line {n}\n" for n in range(1, 41))

# AC41's fixtures: (path, mode, content, edits as (old, new)).
AC41_FILES = [
    (
        "src/crlf.py",
        REGULAR_FILE_MODE,
        "def total(items):\r\n    count = 0\r\n    for item in items:\r\n"
        "        count += item\r\n    return count\r\n",
        [("        count += item\r\n", "        count += item.amount\r\n")],
    ),
    (
        "src/no_final_newline.ts",
        REGULAR_FILE_MODE,
        "export function pay(order) {\n  return charge(order.total)\n}",
        [("charge(order.total)\n}", "charge(order.total, order.currency)\n}")],
    ),
    (
        "src/unicode.py",
        REGULAR_FILE_MODE,
        '# Grüße\nNAME = "café"\ndef greet():\n    return f"Hallo {NAME}"\n',
        [('return f"Hallo {NAME}"', 'return f"Grüß dich, {NAME}"')],
    ),
    (
        "bin/run.py",
        EXECUTABLE_FILE_MODE,
        "#!/usr/bin/env python3\nimport sys\n\nprint(sys.argv)\n",
        [("print(sys.argv)\n", "print(sys.argv[1:])\n")],
    ),
    (
        "src/many.py",
        REGULAR_FILE_MODE,
        NUMBERED,
        [("line 3\n", "line three\n"), ("line 9\n", ""), ("line 30\n", "line 30\nline 30b\n")],
    ),
]


def patch_files() -> list[PatchFile]:
    return [
        PatchFile(path, mode, content, tuple(span_of(content, old, new) for old, new in edits))
        for path, mode, content, edits in AC41_FILES
    ]


def make_repository(root: Path) -> None:
    git(root, "init", "-q")
    for path, mode, content, _ in AC41_FILES:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
        if mode == EXECUTABLE_FILE_MODE:
            target.chmod(0o755)
        git(root, "add", path)
        if mode == EXECUTABLE_FILE_MODE:
            git(root, "update-index", "--chmod=+x", path)


def test_ac41_git_apply_accepts_the_patch_and_leaves_exactly_the_new_tree(
    tmp_path: Path,
) -> None:
    make_repository(tmp_path)
    patch = build_patch(patch_files()).encode("utf-8")

    git(tmp_path, "apply", "--check", "--index", stdin=patch)
    git(tmp_path, "apply", "--index", stdin=patch)

    staged = {
        line.split("\t")[1]: line.split()[:2]
        for line in git(tmp_path, "ls-files", "-s").splitlines()
    }
    for changed in patch_files():
        new_content = apply_spans(changed.old_content, changed.spans)
        assert (tmp_path / changed.path).read_bytes() == new_content.encode("utf-8")
        mode, blob = staged[changed.path]
        assert mode == changed.mode
        assert blob == git_blob_id(new_content)


def test_the_patch_names_both_blob_ids_and_the_mode() -> None:
    [_, _, _, executable, _] = patch_files()
    patch = build_patch([executable])
    new_blob = git_blob_id(apply_spans(executable.old_content, executable.spans))

    assert f"index {git_blob_id(executable.old_content)}..{new_blob} 100755\n" in patch


def test_git_blob_id_matches_git(tmp_path: Path) -> None:
    content = "naïve\r\nlast line without newline"
    (tmp_path / "f").write_bytes(content.encode("utf-8"))

    assert git(tmp_path, "hash-object", "f").strip() == git_blob_id(content)


def test_a_missing_final_newline_is_marked() -> None:
    [_, no_newline, *_] = patch_files()

    assert build_patch([no_newline]).endswith("\n }\n\\ No newline at end of file\n")


def test_a_path_holding_unusual_bytes_is_quoted_as_git_does(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    content = "a = 1\n"
    (tmp_path / "ü.py").write_text(content, encoding="utf-8")
    git(tmp_path, "add", "ü.py")
    patch = build_patch([PatchFile("ü.py", REGULAR_FILE_MODE, content, (Span(4, 5, "2"),))])

    assert patch.startswith('diff --git "a/\\303\\274.py" "b/\\303\\274.py"\n')
    git(tmp_path, "apply", "--check", "--index", stdin=patch.encode("utf-8"))


def test_a_mode_other_than_a_regular_or_executable_file_is_refused() -> None:
    with pytest.raises(ValueError, match="mode"):
        build_patch([PatchFile("a.py", "120000", "x\n", (Span(0, 1, "y"),))])


# --------------------------------------------------------------- the digest


def test_the_digest_is_sha256_over_path_base_blob_and_new_content_in_path_order() -> None:
    files = [("b.py", "1" * 40, "new b\n"), ("a.py", "2" * 40, "new a\n")]
    digest = suggestion_digest(files)

    assert len(digest) == 64
    assert suggestion_digest(list(reversed(files))) == digest
    for changed in (
        [("c.py", "1" * 40, "new b\n"), files[1]],
        [("b.py", "3" * 40, "new b\n"), files[1]],
        [("b.py", "1" * 40, "new b!\n"), files[1]],
    ):
        assert suggestion_digest(changed) != digest


def test_the_digest_cannot_be_matched_by_moving_text_between_fields() -> None:
    assert suggestion_digest([("ab", "c", "d")]) != suggestion_digest([("a", "bc", "d")])


# ------------------------------------------------------- display facts (D53)


def stored(content: str, spans: list[Span], olds: list[str]) -> list[StoredEdit]:
    facts = display_facts(content, spans)
    return [
        StoredEdit(old, span.new_text, fact)
        for old, span, fact in zip(olds, spans, facts, strict=True)
    ]


def test_display_facts_name_the_line_the_rest_of_it_and_three_lines_either_side() -> None:
    span = span_of(NUMBERED, "ne 20", "ne twenty")
    [facts] = display_facts(NUMBERED, [span])

    assert facts == {
        "old_start": 20,
        "first_line_prefix": "li",
        "last_line_suffix": "\n",
        "context_before": "line 17\nline 18\nline 19\n",
        "context_after": "line 21\nline 22\nline 23\n",
        "new_letter_lines": [],
    }
    assert facts_have_shape(facts)


def test_new_letter_lines_name_added_lines_with_letters_new_to_the_file() -> None:
    content = "a = 'é'\nb = 1\nc = 2\n"
    spans = [span_of(content, "b = 1", "b = 'é'"), span_of(content, "c = 2", "c = '\u0436'")]

    facts = display_facts(content, spans)

    assert facts[1]["new_letter_lines"] == [3]
    assert facts[0]["new_letter_lines"] == []


def test_context_is_left_out_over_the_cap() -> None:
    long_lines = "".join("x" * 1500 + "\n" for _ in range(3))
    content = long_lines + "target\n" + long_lines
    [facts] = display_facts(content, [span_of(content, "target", "aim")])

    assert facts["context_before"] is None and facts["context_after"] is None
    assert len(str(facts).encode()) < 2 * DISPLAY_FACTS_MAX_BYTES
    assert derived_hunks(stored(content, [span_of(content, "target", "aim")], ["target"])) is None


@pytest.mark.parametrize(
    "broken",
    [
        {"old_start": 0},
        {"old_start": "3"},
        {"old_start": True},
        {"first_line_prefix": "a\nb"},
        {"context_before": None},
        {"context_after": "1\n2\n3\n4\n"},
        {"new_letter_lines": [0]},
        {"new_letter_lines": "3"},
        {"extra": 1},
    ],
)
def test_a_display_fact_of_the_wrong_shape_is_refused(broken: dict[str, object]) -> None:
    [facts] = display_facts(NUMBERED, [span_of(NUMBERED, "line 7\n", "line seven\n")])

    assert not facts_have_shape({**facts, **broken})


def test_derived_hunks_equal_the_patch_hunks_for_an_unmoved_file() -> None:
    for _, _, content, edits in AC41_FILES:
        spans = [span_of(content, old, new) for old, new in edits]
        olds = [old for old, _ in edits]

        assert derived_hunks(stored(content, spans, olds)) == file_hunks(content, spans)


def random_case(seed: int) -> tuple[str, list[Span], list[str]]:
    """A file of numbered lines and up to four non-overlapping edits, some within a line,
    some across lines, some removing or adding lines, near and far apart."""
    rng = random.Random(seed)
    lines = [f"row {n} {'x' * rng.randrange(3)}\n" for n in range(rng.randrange(1, 30))]
    if rng.random() < 0.3:
        lines[-1] = lines[-1].rstrip("\n")
    content = "".join(lines)
    spans: list[Span] = []
    olds: list[str] = []
    for _ in range(rng.randrange(1, 5)):
        start = rng.randrange(len(content))
        end = min(len(content), start + rng.randrange(1, 40))
        if any(start < s.end and s.start < end for s in spans):
            continue
        old = content[start:end]
        if content.count(old) != 1:
            continue
        new = rng.choice(["", "Z", "new\n", "a\nb\n", old.upper(), old + "\nextra"])
        if new == old:
            continue
        spans.append(Span(start, end, new))
        olds.append(old)
    return content, spans, olds


@pytest.mark.parametrize("seed", range(300))
def test_derived_hunks_equal_the_patch_hunks_over_many_layouts(seed: int) -> None:
    content, spans, olds = random_case(seed)
    if not spans:
        return

    assert derived_hunks(stored(content, spans, olds)) == file_hunks(content, spans)


def test_a_tampered_display_fact_does_not_yield_the_true_hunks() -> None:
    span = span_of(NUMBERED, "line 20\n", "line twenty\n")
    [edit] = stored(NUMBERED, [span], ["line 20\n"])
    moved = StoredEdit(edit.old_text, edit.new_text, {**edit.facts, "old_start": 25})

    assert derived_hunks([moved]) != file_hunks(NUMBERED, [span])
    assert display_facts(NUMBERED, [span]) != [moved.facts]


def test_hunk_numbers_follow_git() -> None:
    span = span_of(NUMBERED, "line 3\n", "")
    [hunk] = file_hunks(NUMBERED, [span])

    assert hunk.header == "@@ -1,6 +1,5 @@"


@pytest.mark.parametrize("seed", range(40))
def test_git_applies_the_patch_over_many_layouts(seed: int, tmp_path: Path) -> None:
    content, spans, _ = random_case(seed)
    if not spans:
        return
    git(tmp_path, "init", "-q")
    (tmp_path / "f.py").write_bytes(content.encode("utf-8"))
    git(tmp_path, "add", "f.py")
    patch = build_patch([PatchFile("f.py", REGULAR_FILE_MODE, content, tuple(spans))])

    git(tmp_path, "apply", "--index", stdin=patch.encode("utf-8"))

    assert (tmp_path / "f.py").read_bytes() == apply_spans(content, spans).encode("utf-8")


def test_an_edit_that_joins_two_lines_takes_in_the_next_line(tmp_path: Path) -> None:
    content = "first\nsecond\nthird\n"
    span = span_of(content, "first\n", "first ")
    patch = build_patch([PatchFile("f.py", REGULAR_FILE_MODE, content, (span,))])

    assert "\\ No newline" not in patch
    assert "+first second\n" in patch
    [edit] = stored(content, [span], ["first\n"])
    assert derived_hunks([edit]) == file_hunks(content, [span])
    git(tmp_path, "init", "-q")
    (tmp_path / "f.py").write_text(content)
    git(tmp_path, "add", "f.py")
    git(tmp_path, "apply", "--index", stdin=patch.encode())
    assert (tmp_path / "f.py").read_text() == "first second\nthird\n"


def test_only_a_line_feed_ends_a_line(tmp_path: Path) -> None:
    content = "page = 1\x0cbreak\nnext = 2\u2028same line\nlast\n"
    span = span_of(content, "last", "final")
    [hunk] = file_hunks(content, [span])

    assert hunk.header == "@@ -1,3 +1,3 @@"
    git(tmp_path, "init", "-q")
    (tmp_path / "f.py").write_bytes(content.encode())
    git(tmp_path, "add", "f.py")
    patch = build_patch([PatchFile("f.py", REGULAR_FILE_MODE, content, (span,))])
    git(tmp_path, "apply", "--index", stdin=patch.encode())


def test_the_patch_reads_back_as_the_hunks_it_was_built_from() -> None:
    """Step 5: a read shows the check run's diff from the patch it stored (AC19), so the
    page and the download are one text. CRLF, a missing final newline, non-ASCII text, an
    executable and several hunks, in the patch's path order."""
    files = sorted(patch_files(), key=lambda each: each.path)

    read = patch_hunks(build_patch(files))

    assert read == [file_hunks(each.old_content, each.spans) for each in files]


@pytest.mark.parametrize("seed", range(100))
def test_the_patch_reads_back_over_many_layouts(seed: int) -> None:
    content, spans, _ = random_case(seed)
    if not spans:
        return
    changed = PatchFile("src/x.py", REGULAR_FILE_MODE, content, tuple(spans))

    assert patch_hunks(build_patch([changed])) == [file_hunks(content, spans)]


@pytest.mark.parametrize(
    "broken",
    ["not a patch\n", "diff --git a/x b/x\n@@ nonsense @@\n", "@@ -1,1 +1,1 @@\n-a\n+b\n"],
)
def test_text_this_module_did_not_write_is_refused(broken: str) -> None:
    with pytest.raises(ValueError):
        patch_hunks(broken)
