"""A stored suggestion, read back without its files (spec FR16 to FR19, AC19, AC55, AC56;
design, *The flow*, step 6, *Display facts (D53)*, *Tamper signals*; plan T4.7, T4.8).

The pack is gone by the time anyone reads a suggestion, and the AI room could have written
anything into `edits` (D18). So every read, and the sweep when it first records the row,
**believes nothing stored**:

- each edit's shape and display facts are checked; the hunks are rebuilt from them, and a
  fact that disagrees with itself or the old text shows no code;
- the rules that need no file are run again: K1 (a file in `files_read`, which only
  `scheduler-connections` writes, inside the row's folder), K2, K5, and K7 to K10 on every
  added line, and FR16's prose checks on each `why`, with its evidence ids;
- the flags come from the rebuilt lines, and new letters from the stored facts.

Pure: the API, the sweep and the check run all call it.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, cast

from nexovue_platform.evidence.aliases import AliasBook
from nexovue_platform.fixes.checks import (
    EDIT_KEYS,
    MAX_ADDED_BYTES,
    MAX_CHANGED_LINES,
    MAX_FILES,
    Flag,
    flag_lists_cover,
    line_breaks_without_file,
    line_flags,
)
from nexovue_platform.fixes.diff import (
    DISPLAY_FACT_KEYS,
    Hunk,
    StoredEdit,
    derived_hunks,
    facts_have_shape,
    split_lines,
)
from nexovue_platform.fixes.policy import NO_OWNER_LISTS, OwnerLists, is_never_changed
from nexovue_platform.fixes.prose import check_why, evidence_ids_known

_STORED_KEYS: Final = EDIT_KEYS | frozenset(DISPLAY_FACT_KEYS)

Failure = Literal["shape", "K1", "K2", "K5", "K7", "K8", "K9", "K10", "prose"]


@dataclass(frozen=True, slots=True)
class StoredFile:
    """One changed file as a read shows it: its path from `files_read`, and its hunks
    rebuilt from the display facts, or None when context was too long to store."""

    ref: str
    path: str
    hunks: tuple[Hunk, ...] | None
    added: int
    removed: int
    edit_count: int


@dataclass(frozen=True, slots=True)
class StoredWhy:
    ref: str
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StoredProposal:
    files: tuple[StoredFile, ...]
    flags: tuple[Flag, ...]
    whys: tuple[StoredWhy, ...]
    uncovered: tuple[str, ...] = field(default=())

    @property
    def changed_lines(self) -> int:
        return sum(each.added + each.removed for each in self.files)


@dataclass(frozen=True, slots=True)
class StoredRefusal:
    """Why a stored suggestion shows no code. Never shown as such: a read says nothing
    more than that it cannot be shown, and the sweep turns it into the tamper signal."""

    failure: Failure


def read_stored(
    edits: object,
    files_read: Mapping[str, Any] | None,
    *,
    folder: str | None,
    owner: OwnerLists = NO_OWNER_LISTS,
    allowed_evidence: Collection[str],
    aliases: AliasBook | None,
) -> StoredProposal | StoredRefusal:
    """A stored `edits` list checked again, whole, against `files_read` (D18)."""
    if not isinstance(edits, list) or not edits:
        return StoredRefusal("shape")
    listed = _listed(files_read)
    by_ref: dict[str, list[StoredEdit]] = {}
    whys: list[StoredWhy] = []
    for item in edits:
        if not _edit_shape(item):
            return StoredRefusal("shape")
        ref = item["file_ref"]
        path = listed.get(ref)
        if path is None or not _inside(path, folder or ""):
            return StoredRefusal("K1")
        if is_never_changed(path, owner):
            return StoredRefusal("K2")
        verdict = check_why(item["why"], aliases)
        if verdict.refusal is not None or not evidence_ids_known(
            item["evidence_ids"], allowed_evidence
        ):
            return StoredRefusal("prose")
        whys.append(StoredWhy(ref, verdict.text, tuple(item["evidence_ids"])))
        by_ref.setdefault(ref, []).append(
            StoredEdit(
                old_text=item["old_text"],
                new_text=item["new_text"],
                facts={key: item[key] for key in DISPLAY_FACT_KEYS},
            )
        )
    files: list[StoredFile] = []
    flags: list[Flag] = []
    added_bytes = 0
    for ref, stored in by_ref.items():
        built = _built_file(ref, listed[ref], stored)
        if isinstance(built, StoredRefusal):
            return built
        changed, added_lines, removed_lines = built
        refusal = _line_refusal(added_lines)
        if refusal is not None:
            return refusal
        letters = {n for edit in stored for n in edit.facts["new_letter_lines"]}
        flags += line_flags(ref, changed.path, added_lines, removed_lines, new_letter_lines=letters)
        added_bytes += sum(len(line.encode("utf-8")) for _, line in added_lines)
        files.append(changed)
    proposal = StoredProposal(
        files=tuple(files),
        flags=tuple(flags),
        whys=tuple(whys),
        uncovered=tuple(each.path for each in files if not flag_lists_cover(each.path)),
    )
    if (
        len(files) > MAX_FILES
        or proposal.changed_lines > MAX_CHANGED_LINES
        or added_bytes > MAX_ADDED_BYTES
    ):
        return StoredRefusal("K5")
    return proposal


def _listed(files_read: Mapping[str, Any] | None) -> dict[str, str]:
    """`ref` to path, from the listing `scheduler-connections` alone writes."""
    found: dict[str, str] = {}
    for each in (files_read or {}).get("files") or []:
        if isinstance(each, Mapping) and isinstance(each.get("ref"), str):
            path = each.get("path")
            if isinstance(path, str):
                found[each["ref"]] = path
    return found


def _inside(path: str, folder: str) -> bool:
    folder = folder.strip("/")
    return not folder or path == folder or path.startswith(folder + "/")


def _edit_shape(item: object) -> bool:
    if not isinstance(item, Mapping) or set(item) != _STORED_KEYS:
        return False
    texts = (item["file_ref"], item["old_text"], item["new_text"], item["why"])
    evidence = item["evidence_ids"]
    return (
        all(isinstance(text, str) for text in texts)
        and item["old_text"] != item["new_text"]
        and isinstance(evidence, list)
        and all(isinstance(each, str) for each in evidence)
    )


def _built_file(
    ref: str, path: str, stored: Sequence[StoredEdit]
) -> tuple[StoredFile, list[tuple[int, str]], list[tuple[int, str]]] | StoredRefusal:
    """The file's hunks from its facts. Facts of the wrong shape, or that disagree, show no
    code (AC19's read half). Context left out for size shows the file without hunks, and
    its added and removed lines then come from the edits' text alone."""
    if not all(facts_have_shape(edit.facts) for edit in stored):
        return StoredRefusal("shape")
    if any(edit.facts["context_before"] is None for edit in stored):
        added = [(0, _bare(line)) for e in stored for line in split_lines(e.new_text)]
        removed = [(0, _bare(line)) for e in stored for line in split_lines(e.old_text)]
        return (
            StoredFile(ref, path, None, len(added), len(removed), len(stored)),
            added,
            removed,
        )
    hunks = derived_hunks(stored)
    if hunks is None:
        return StoredRefusal("shape")
    added, removed = numbered_lines(hunks)
    return (
        StoredFile(ref, path, tuple(hunks), len(added), len(removed), len(stored)),
        added,
        removed,
    )


def numbered_lines(hunks: Sequence[Hunk]) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Each added line with its new line number, and each removed line with its old one."""
    added: list[tuple[int, str]] = []
    removed: list[tuple[int, str]] = []
    for hunk in hunks:
        old, new = hunk.old_start, hunk.new_start
        for marker, line in hunk.lines:
            if marker == "+":
                added.append((new, _bare(line)))
                new += 1
            elif marker == "-":
                removed.append((old, _bare(line)))
                old += 1
            else:
                old, new = old + 1, new + 1
    return added, removed


def _bare(line: str) -> str:
    return line.removesuffix("\n").removesuffix("\r")


def _line_refusal(added: Sequence[tuple[int, str]]) -> StoredRefusal | None:
    for _, line in added:
        for rule, _detail in line_breaks_without_file(line):
            return StoredRefusal(cast(Failure, rule))
    return None
