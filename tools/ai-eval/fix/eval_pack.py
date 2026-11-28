"""The fixture repository, read through the product's own pack reader (F5 plan T6.1; ADR-098).

`LocalRepository` answers the six read routes choosing a pack uses
(`source_pack.RepositoryReads`) from `repository/` on disk, so the bed's packs are chosen
by the pack job's own code: the stack's files, one hop of imports from the top two, their
test files, and in a later round the names the model asked for, each through the same
lists, caps and secret scan. Nothing here talks to GitHub, and nothing writes.

Two arms exist only in the bed (plan T6.1, "with and without imports and rounds"):
**without imports** drops the files the reader added as imports and numbers the rest
again; **without rounds** starts at the last round, so the model may not ask for files.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final, Literal

from nexovue_platform.fixes.diff import REGULAR_FILE_MODE
from nexovue_platform.fixes.pack import LeftOut, PackFile
from nexovue_platform.fixes.policy import NO_OWNER_LISTS
from nexovue_platform.fixes.proposal import AskedFile
from nexovue_platform.fixes.records import FROM_MODEL, FROM_STACK, MAX_ROUNDS

from fix.fixtures import REPOSITORY, Case, Frame
from nexovue_scheduler.connections.fix_github import RepositoryFacts, TreeEntry
from nexovue_scheduler.connections.source_pack import (
    PackContext,
    PackReader,
    PackReading,
    files_read_of,
)

Arm = Literal["imports_and_rounds", "no_imports", "no_rounds", "neither"]
ARMS: Final[tuple[Arm, ...]] = ("imports_and_rounds", "no_imports", "no_rounds", "neither")
MAIN_ARM: Final[Arm] = "imports_and_rounds"
# The bed's one branch and commit: the fixture repository has no history.
BRANCH: Final = "main"
HEAD: Final = "0" * 40
_TREE_PREFIX: Final = "tree:"
_DIRECTORY_MODE: Final = "040000"
_SKIPPED: Final = frozenset({"__pycache__", ".pytest_cache"})


def has_imports(arm: Arm) -> bool:
    return arm in ("imports_and_rounds", "no_rounds")


def first_round(arm: Arm) -> int:
    return 1 if arm in ("imports_and_rounds", "no_imports") else MAX_ROUNDS


def _blob_sha(data: bytes) -> str:
    """Git's own blob id, so a file's sha reads as GitHub's would."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


class LocalRepository:
    """`repository/` as the Fix App's read routes would show it, at one commit."""

    def __init__(self, root: Path = REPOSITORY) -> None:
        self._files: dict[str, bytes] = {}
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if path.is_file() and not _SKIPPED & set(relative.parts):
                self._files[relative.as_posix()] = path.read_bytes()
        self._blobs = {_blob_sha(data): data for data in self._files.values()}

    def repository(self, token: str, key: str) -> RepositoryFacts:
        return RepositoryFacts(default_branch=BRANCH, private=False)

    def head_of(self, token: str, key: str, branch: str) -> str:
        return HEAD

    def is_ancestor(self, token: str, key: str, commit: str, default_branch: str) -> bool:
        return commit == HEAD

    def tree_of_commit(self, token: str, key: str, commit: str) -> str:
        return _TREE_PREFIX

    def tree(self, token: str, key: str, sha: str, *, recursive: bool) -> list[TreeEntry]:
        folder = sha.removeprefix(_TREE_PREFIX)
        prefix = f"{folder}/" if folder else ""
        entries: dict[str, TreeEntry] = {}
        for path, data in self._files.items():
            if not path.startswith(prefix):
                continue
            parts = path.removeprefix(prefix).split("/")
            depth = len(parts) if recursive else 1
            for level in range(1, min(depth, len(parts)) + 1):
                relative = "/".join(parts[:level])
                if level == len(parts):
                    entries[relative] = TreeEntry(
                        relative, REGULAR_FILE_MODE, "blob", _blob_sha(data), len(data)
                    )
                else:
                    tree_sha = _TREE_PREFIX + prefix + relative
                    entries[relative] = TreeEntry(relative, _DIRECTORY_MODE, "tree", tree_sha, None)
        return [entries[name] for name in sorted(entries)]

    def blob(self, token: str, key: str, sha: str) -> bytes:
        return self._blobs[sha]


@dataclass(frozen=True, slots=True)
class _BedRow:
    """The pack job's row, as far as its reader reads one."""

    candidates_from: str
    path_candidates: list[dict[str, Any]] = field(default_factory=list)
    requested_paths: list[dict[str, Any]] = field(default_factory=list)
    files_read: dict[str, Any] | None = None
    read_commit: str | None = None


@dataclass(frozen=True, slots=True)
class BedPack:
    """One round's pack: what was sent, as the job stores it, and what the listing says."""

    case_id: str
    folder: str
    round_number: int
    files: tuple[PackFile, ...]
    frames: tuple[dict[str, Any], ...]
    left_out: tuple[LeftOut, ...]
    unmatched: int
    top_left_out: bool
    files_read: Mapping[str, Any]
    # The asked-for names this round that were refused or matched no file (FR15b).
    asked_refused: tuple[str, ...] = ()

    def stored_files(self) -> list[dict[str, Any]]:
        """The pack rows the AI job reads: reference, path, whole text, lines sent."""
        return [
            {
                "ref": each.ref,
                "path": each.path,
                "content": each.content,
                "window": None if each.window is None else list(each.window),
                "role": each.role,
            }
            for each in self.files
        ]


def stack_candidates(frames: Iterable[Frame]) -> list[dict[str, Any]]:
    """Each frame as the console's brief leaves it: the path the stack printed, its line."""
    return [{"from": FROM_STACK, "path": f.runtime_path, "line": f.line} for f in frames]


def _reader(case: Case, repository: LocalRepository) -> PackReader:
    return PackReader(repository, "bed", "bed/fixture", case.service.folder)


def _context(
    case: Case, pack_files: list[dict[str, Any]], pack_frames: list[dict[str, Any]]
) -> PackContext:
    return PackContext(
        path_in_production=case.service.deployed_at or None,
        github_user_id=None,
        pack_files=pack_files,
        pack_frames=pack_frames,
    )


def _without_imports(reading: PackReading) -> None:
    """The bed's arm only: the files added as imports dropped, the rest numbered again."""
    builder = reading.builder
    kept = [each for each in builder.files if each.role != "imported"]
    builder.files = [replace(each, ref=f"file_{n}") for n, each in enumerate(kept, start=1)]
    builder.sent_bytes = sum(each.sent_bytes for each in builder.files)


def _pack(case: Case, reading: PackReading, round_number: int, asked_names: int) -> BedPack:
    builder = reading.builder
    asked_left = tuple(e.reason for e in builder.left_out if e.path in reading.asked)
    # A later round's reader counts only asked-for names as matching no file.
    unmatched_asked = builder.unmatched if asked_names else 0
    return BedPack(
        case_id=case.id,
        folder=case.service.folder,
        round_number=round_number,
        files=tuple(builder.files),
        frames=tuple(builder.frames()),
        left_out=tuple(builder.left_out),
        unmatched=builder.unmatched,
        top_left_out=reading.top_left_out,
        files_read=files_read_of(reading, round_number=round_number, earlier=None),
        asked_refused=asked_left + ("matched_no_file",) * unmatched_asked,
    )


def pack_for(
    case: Case,
    *,
    arm: Arm,
    forged: Sequence[Frame] = (),
    repository: LocalRepository | None = None,
) -> BedPack:
    """The first round's pack for `case`, with any forged frames after its own (A1)."""
    local = repository or LocalRepository()
    row = _BedRow(
        candidates_from=FROM_STACK, path_candidates=stack_candidates((*case.frames, *forged))
    )
    reading = _reader(case, local).read(row, NO_OWNER_LISTS, _context(case, [], []))
    if not has_imports(arm):
        _without_imports(reading)
    return _pack(case, reading, first_round(arm), asked_names=0)


def next_round(
    case: Case,
    previous: BedPack,
    asked: Sequence[AskedFile],
    *,
    repository: LocalRepository | None = None,
) -> BedPack:
    """The pack job's later round: the pack as it stood, then each asked-for name (T3.19)."""
    local = repository or LocalRepository()
    row = _BedRow(
        candidates_from=FROM_MODEL,
        requested_paths=[each.stored() for each in asked],
        files_read=dict(previous.files_read),
    )
    context = _context(case, previous.stored_files(), [dict(each) for each in previous.frames])
    reading = _reader(case, local).read(row, NO_OWNER_LISTS, context)
    return _pack(case, reading, previous.round_number + 1, asked_names=len(asked))
