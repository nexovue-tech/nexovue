"""Matching an error's stack paths to files in a repository's tree (spec FR12, AC12b,
AC12e; design, *Picking files*).

Pure: the pack job lists the folder's tree and asks this which file each stack path names.
**Every stack path is attacker-writable** (A1), so a match must be a real file inside the
listed folder, and an ambiguous match is dropped, never guessed.

- With a **path in production**, a frame under that prefix maps exactly: `/app/main.py`
  with `/app` is `main.py`, so a one-segment path can match.
- Otherwise the **longest path suffix** that names exactly one file wins, with at least two
  segments unless the frame has no directory: `/app/src/checkout/pay.py` finds
  `src/checkout/pay.py`, and `/x/site/api.py` does not match on `api.py` alone.
- **Build folders** (`dist`, `build`, `out`, `.next`) also try the source extensions, so
  `dist/checkout/pay.js` finds `src/checkout/pay.ts`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

BUILD_FOLDERS: Final = frozenset({"dist", "build", "out", ".next"})
COMPILED_EXTENSIONS: Final = (".js", ".mjs", ".cjs", ".jsx")
SOURCE_FOR_COMPILED: Final = (".ts", ".tsx", ".jsx", ".js")
# Two segments, unless the frame names a bare file: one segment alone is too common.
MIN_SUFFIX_SEGMENTS: Final = 2


@dataclass(frozen=True, slots=True)
class Match:
    """The file a stack path names, relative to the listed folder, or why none."""

    path: str | None
    ambiguous: bool = False


NO_MATCH: Final = Match(path=None)


def segments_of(path: str) -> list[str]:
    """A path's segments, with any drive, scheme or `.` segment dropped."""
    cleaned = path.replace("\\", "/")
    for scheme in ("file://", "webpack:///", "webpack://", "app:///"):
        if cleaned.startswith(scheme):
            cleaned = cleaned[len(scheme) :]
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if parts and len(parts[0]) == 2 and parts[0][1] == ":":
        parts = parts[1:]
    return parts


class TreeIndex:
    """A folder's files, relative to the folder, indexed by file name."""

    def __init__(self, paths: Iterable[str]) -> None:
        self.paths: frozenset[str] = frozenset(paths)
        by_name: dict[str, list[str]] = {}
        for path in self.paths:
            by_name.setdefault(path.rsplit("/", 1)[-1], []).append(path)
        self._by_name = by_name

    def __contains__(self, path: object) -> bool:
        return path in self.paths

    def ending_with(self, suffix: Sequence[str]) -> list[str]:
        """Files whose last segments are exactly `suffix`."""
        wanted = "/".join(suffix)
        return sorted(
            path
            for path in self._by_name.get(suffix[-1], [])
            if path == wanted or path.endswith("/" + wanted)
        )


def match_path(raw: str, tree: TreeIndex, *, path_in_production: str | None = None) -> Match:
    """The one file `raw` names in `tree`, by the rules above."""
    parts = segments_of(raw)
    if not parts or any(part == ".." for part in parts):
        return NO_MATCH
    if path_in_production is not None:
        exact = _under_production_path(parts, path_in_production, tree)
        if exact is not None:
            return exact
    found = _by_suffix(parts, tree)
    if found.path is not None or found.ambiguous:
        return found
    return _by_compiled(parts, tree)


def _under_production_path(parts: list[str], prefix: str, tree: TreeIndex) -> Match | None:
    prefix_parts = segments_of(prefix)
    if not prefix_parts or parts[: len(prefix_parts)] != prefix_parts:
        return None
    rest = "/".join(parts[len(prefix_parts) :])
    return Match(path=rest) if rest in tree else None


def _by_suffix(parts: list[str], tree: TreeIndex) -> Match:
    shortest = 1 if len(parts) == 1 else MIN_SUFFIX_SEGMENTS
    for length in range(len(parts), shortest - 1, -1):
        found = tree.ending_with(parts[-length:])
        if len(found) == 1:
            return Match(path=found[0])
        if len(found) > 1:
            return Match(path=None, ambiguous=True)
    return NO_MATCH


def _by_compiled(parts: list[str], tree: TreeIndex) -> Match:
    """A compiled frame under a build folder, tried as its source."""
    build = [index for index, part in enumerate(parts) if part in BUILD_FOLDERS]
    name = parts[-1]
    if not build or not name.endswith(COMPILED_EXTENSIONS):
        return NO_MATCH
    rest = parts[build[-1] + 1 :]
    stem = rest[-1].rsplit(".", 1)[0]
    shortest = min(MIN_SUFFIX_SEGMENTS, len(rest))
    for extension in SOURCE_FOR_COMPILED:
        renamed = [*rest[:-1], stem + extension]
        for length in range(len(renamed), shortest - 1, -1):
            found = tree.ending_with(renamed[-length:])
            if len(found) == 1:
                return Match(path=found[0])
            if len(found) > 1:
                return Match(path=None, ambiguous=True)
    return NO_MATCH


def conventional_test_file(path: str, tree: TreeIndex) -> str | None:
    """The conventional test file for `path` (FR12), when the tree holds exactly one:
    `test_x.py`, `x_test.py`, `x.test.ts`, `x.spec.ts`, `x_test.go`, `XTest.java`."""
    folder, _, name = path.rpartition("/")
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem:
        return None
    names = {
        "py": [f"test_{stem}.py", f"{stem}_test.py"],
        "go": [f"{stem}_test.go"],
        "java": [f"{stem}Test.java"],
        "kt": [f"{stem}Test.kt"],
    }.get(extension, [f"{stem}.test.{extension}", f"{stem}.spec.{extension}"])
    # Beside the file, or under a folder whose name says test, anywhere in the listed tree.
    found = [
        each
        for candidate in names
        for each in tree.ending_with([candidate])
        if each.rpartition("/")[0] == folder
        or any("test" in segment.casefold() for segment in each.split("/")[:-1])
    ]
    return found[0] if len(found) == 1 else None
