"""Choosing what goes into a suggestion's pack of files (spec FR12, FR12a, FR13, FR13a;
design, *Picking files*; plan T3.10).

Pure: the pack job lists the folder's tree and fetches blobs; this decides, file by file,
what may be fetched and what is kept. **Under the caps, stack files come first, then
imports, then tests** (AC81), because the job offers them in that order and a file is
admitted only while the caps hold.

A file is left out, and listed by name with its reason, only when it exists in the tree
(AC12g): a stack string matching no file is a count, never a stored path. A secret-shaped
file is dropped before anything is stored (FR13), and a file too large to send whole is
sent as a window around the stack's lines, with its whole text kept for K3 (D48).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from nexovue_platform.fixes.diff import FILE_MODES
from nexovue_platform.fixes.policy import NO_OWNER_LISTS, OwnerLists, never_sent_reason
from nexovue_platform.fixes.records import (
    MAX_FILES,
    MAX_SENT_BYTES,
    WHOLE_FILE_MAX_BYTES,
    WINDOWED_FILE_MAX_BYTES,
)
from nexovue_platform.fixes.secrets import secret_in

Role = Literal["stack", "named", "imported", "asked", "test"]
# Why an existing file was left out: the lists' own reasons (`policy.never_sent_reason`)
# and the pack's. Every value has a sentence in the API's file list.
PACK_LEFT_OUT_REASONS: Final = (
    "not_a_regular_file",
    "not_text",
    "secret_pattern",
    "too_large",
    "no_line_for_window",
    "over_file_cap",
    "over_byte_cap",
)
LEFT_OUT_REASONS: Final = (
    "secret_or_key",
    "ci",
    "agent_instructions",
    "excluded_by_owner",
    "not_source",
    "configuration",
    *PACK_LEFT_OUT_REASONS,
)
REF_PREFIX: Final = "file_"


@dataclass(frozen=True, slots=True)
class TreeFile:
    """One file in the listed folder, by its repository path, as GitHub's tree names it."""

    path: str
    mode: str
    sha: str
    size: int | None = None


@dataclass(frozen=True, slots=True)
class PackFile:
    ref: str
    path: str
    sha: str
    mode: str
    role: Role
    why: dict[str, Any]
    content: str
    # The 1-based, inclusive lines sent, or None when the whole file is sent.
    window: tuple[int, int] | None
    sent_bytes: int


@dataclass(frozen=True, slots=True)
class LeftOut:
    path: str
    reason: str
    bytes: int | None = None


@dataclass(slots=True)
class _Pending:
    role: Role
    why: dict[str, Any]
    lines: tuple[int, ...]
    file: TreeFile


@dataclass
class PackBuilder:
    """One pack being chosen. Paths are the repository's, as the lists match them
    (design, *Lists*: on the full repository path)."""

    tree: Mapping[str, TreeFile]
    owner: OwnerLists = NO_OWNER_LISTS
    files: list[PackFile] = field(default_factory=list)
    left_out: list[LeftOut] = field(default_factory=list)
    unmatched: int = 0
    ambiguous: int = 0
    sent_bytes: int = 0
    _pending: dict[str, _Pending] = field(default_factory=dict)
    _seen: set[str] = field(default_factory=set)
    _frames: list[tuple[str, int]] = field(default_factory=list)

    # ------------------------------------------------------------- choosing

    def keep(self, file: PackFile) -> None:
        """A file already in the pack from an earlier round (FR15b): kept as it was, and
        counted against the caps before anything asked for is considered."""
        self._seen.add(file.path)
        self.files.append(file)
        self.sent_bytes += file.sent_bytes

    def consider(
        self, path: str, role: Role, why: Mapping[str, Any], lines: Sequence[int] = ()
    ) -> bool:
        """Whether `path` should be fetched. A refusal of an existing file is listed."""
        file = self.tree.get(path)
        if file is None:
            self.unmatched += 1
            return False
        if path in self._seen:
            return False
        self._seen.add(path)
        refused = self._refusal(path, file, lines)
        if refused is not None:
            self.left_out.append(LeftOut(path, refused, file.size))
            return False
        self._pending[path] = _Pending(role, dict(why), tuple(lines), file)
        return True

    def note_frame(self, path: str, line: int | None) -> None:
        """A stack frame naming a matched file, kept as a number for FR15a."""
        if line is not None and line > 0:
            self._frames.append((path, line))

    def lines_for(self, path: str) -> tuple[int, ...]:
        return tuple(line for named, line in self._frames if named == path)

    def _refusal(self, path: str, file: TreeFile, lines: Sequence[int]) -> str | None:
        never = never_sent_reason(path, self.owner)
        if never is not None:
            return never
        if file.mode not in FILE_MODES:
            return "not_a_regular_file"
        if file.size is not None and file.size > WINDOWED_FILE_MAX_BYTES:
            return "too_large"
        if file.size is not None and file.size > WHOLE_FILE_MAX_BYTES and not lines:
            return "no_line_for_window"
        if len(self.files) >= MAX_FILES:
            return "over_file_cap"
        return None

    # ------------------------------------------------------------- keeping

    def admit(self, path: str, data: bytes) -> PackFile | None:
        """Keep a fetched file, or list why not. Scanned whole before anything is kept."""
        pending = self._pending.pop(path)
        size = len(data)
        refused, text = _text_of(data)
        if refused is None and secret_in(text) is not None:
            refused = "secret_pattern"
        window: tuple[int, int] | None = None
        sent = size
        if refused is None and size > WINDOWED_FILE_MAX_BYTES:
            refused = "too_large"
        elif refused is None and size > WHOLE_FILE_MAX_BYTES:
            lines = pending.lines or self.lines_for(path)
            window = _window(text, lines) if lines else None
            refused = None if window is not None else "no_line_for_window"
            sent = 0 if window is None else _window_bytes(text, window)
        if refused is None and len(self.files) >= MAX_FILES:
            refused = "over_file_cap"
        if refused is None and self.sent_bytes + sent > MAX_SENT_BYTES:
            refused = "over_byte_cap"
        if refused is not None:
            self.left_out.append(LeftOut(path, refused, size))
            return None
        kept = PackFile(
            ref=f"{REF_PREFIX}{len(self.files) + 1}",
            path=path,
            sha=pending.file.sha,
            mode=pending.file.mode,
            role=pending.role,
            why=pending.why,
            content=text,
            window=window,
            sent_bytes=sent,
        )
        self.files.append(kept)
        self.sent_bytes += sent
        return kept

    def frames(self) -> list[dict[str, int | str]]:
        """`[{ref, line}]` in stack order, numbers only (FR15a)."""
        refs = {file.path: file.ref for file in self.files}
        return [{"ref": refs[path], "line": line} for path, line in self._frames if path in refs]

    def left_out_named(self, path: str) -> bool:
        return any(each.path == path for each in self.left_out)


def _text_of(data: bytes) -> tuple[str | None, str]:
    """A file's text, or `not_text` for a NUL byte or bytes that are not UTF-8."""
    if b"\x00" in data:
        return "not_text", ""
    try:
        return None, data.decode("utf-8")
    except UnicodeDecodeError:
        return "not_text", ""


def _line_bytes(text: str) -> list[int]:
    return [len(line.encode("utf-8")) for line in text.splitlines(keepends=True)]


def _window(text: str, lines: Sequence[int]) -> tuple[int, int] | None:
    """The widest run of lines around the stack's lines that fits a whole file's cap."""
    sizes = _line_bytes(text)
    count = len(sizes)
    wanted = sorted({line for line in lines if 1 <= line <= count})
    if not wanted:
        return None
    low, high = wanted[0], wanted[-1]
    if sum(sizes[low - 1 : high]) > WHOLE_FILE_MAX_BYTES:
        low = high = wanted[0]
    if sizes[low - 1] > WHOLE_FILE_MAX_BYTES:
        return None
    total = sum(sizes[low - 1 : high])
    grew = True
    while grew:
        grew = False
        if high < count and total + sizes[high] <= WHOLE_FILE_MAX_BYTES:
            total += sizes[high]
            high += 1
            grew = True
        if low > 1 and total + sizes[low - 2] <= WHOLE_FILE_MAX_BYTES:
            total += sizes[low - 2]
            low -= 1
            grew = True
    return low, high


def _window_bytes(text: str, window: tuple[int, int]) -> int:
    return sum(_line_bytes(text)[window[0] - 1 : window[1]])


def files_digest(files: Sequence[tuple[str, str]]) -> str:
    """SHA-256 over each `(path, blob sha)` that would be sent, in path order, each field
    length-prefixed (design, `files_digest`). Send must carry it (FR13a, AC3b)."""
    digest = hashlib.sha256()
    for path, sha in sorted(files):
        for part in (path, sha):
            encoded = part.encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big") + encoded)
    return digest.hexdigest()


def owner_lists_hash(owner: OwnerLists) -> str:
    """Which owner's list a pack was chosen under, so a change is seen (design)."""
    document = json.dumps(
        {
            "excluded": sorted(owner.extra_excluded_paths),
            "allowed": sorted(owner.allowed_config_paths),
        },
        sort_keys=True,
    )
    return hashlib.sha256(document.encode()).hexdigest()
