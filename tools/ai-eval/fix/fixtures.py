"""The fixture cases, read from `cases.json` (plan T2.6).

A case is one seeded bug in `repository/`: the service it lives in, the stack a finding
would carry, the result that is right, the seeded test that shows the bug, and, for
most, a reference change. `test_fixtures.py` holds every case to its own claims.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

HERE: Final = Path(__file__).resolve().parent
REPOSITORY: Final = HERE / "repository"
CASES_FILE: Final = HERE / "cases.json"

Language = Literal["python", "typescript", "java"]
LANGUAGES: Final = ("python", "typescript", "java")
# Where the file to change is found: in the stack, one import away from its top two
# files, or neither (a person must name it).
Reach = Literal["stack", "import", "neither"]
REACHES: Final = ("stack", "import", "neither")
# How long one seeded test may run before it is stopped (a guess; each takes about 1 s).
SEEDED_TEST_SECONDS: Final = 60
# A refusal is right for these, the model's abstain reasons and the job's own codes.
REFUSAL_REASONS: Final = (
    "not_in_these_files",
    "evidence_too_thin",
    "needs_a_decision",
    "not_a_code_problem",
    "no_stack",
    "no_usable_file",
)


@dataclass(frozen=True, slots=True)
class Service:
    name: str
    folder: str
    language: Language
    # Where the service's folder sits in production, as its stacks print paths.
    deployed_at: str


@dataclass(frozen=True, slots=True)
class Frame:
    runtime_path: str
    function: str
    line: int
    # The repository file the frame is in; None for a library's frame.
    repository_path: str | None
    line_text: str | None


@dataclass(frozen=True, slots=True)
class ReferenceEdit:
    path: str
    old_text: str
    new_text: str


@dataclass(frozen=True, slots=True)
class Expected:
    result: Literal["edits", "refused"]
    files: tuple[str, ...] = ()
    reach: Reach | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Plants:
    asks: tuple[str, ...]
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    service: Service
    exception_type: str
    message: str
    frames: tuple[Frame, ...]
    expected: Expected
    seeded_test: str
    reference_edit: ReferenceEdit | None
    plants: Plants | None
    note: str


def _case(raw: dict[str, Any], services: dict[str, Service]) -> Case:
    expected = raw["expected"]
    reference = raw.get("reference_edit")
    plants = raw.get("plants")
    return Case(
        id=raw["id"],
        service=services[raw["service"]],
        exception_type=raw["exception_type"],
        message=raw["message"],
        frames=tuple(Frame(**frame) for frame in raw["frames"]),
        expected=Expected(
            result=expected["result"],
            files=tuple(expected.get("files", ())),
            reach=expected.get("reach"),
            reason=expected.get("reason"),
        ),
        seeded_test=raw["seeded_test"],
        reference_edit=None if reference is None else ReferenceEdit(**reference),
        plants=None
        if plants is None
        else Plants(asks=tuple(plants["asks"]), addresses=tuple(plants["addresses"])),
        note=raw.get("note", ""),
    )


def load_cases(path: Path = CASES_FILE) -> list[Case]:
    document = json.loads(path.read_text(encoding="utf-8"))
    services = {name: Service(name=name, **values) for name, values in document["services"].items()}
    return [_case(raw, services) for raw in document["cases"]]


def read(path: str) -> str:
    return (REPOSITORY / path).read_text(encoding="utf-8")


def run_seeded_test(case: Case, root: Path, *, timeout: float = SEEDED_TEST_SECONDS) -> int:
    """pytest's exit code for `case`'s seeded test in the copy of the repository at `root`:
    0 passed, 1 failed. Python only: the gate has no Node or JDK. A test still running at
    `timeout` raises `subprocess.TimeoutExpired`."""
    if case.service.language != "python":
        raise ValueError(f"{case.id}: only Python seeded tests run here")
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
        timeout=timeout,
    )
    return done.returncode
