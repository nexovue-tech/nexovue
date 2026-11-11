"""A repository's git objects, for the simulated GitHub's git data routes (F5 plan T2.10).

Blobs, trees and commits are hashed as git hashes them, so a blob's id here is the id
F5's code computes (`nexovue_platform.fixes.diff.git_blob_id`) and the id a real
GitHub would return for the same bytes. Commits carry a fixed author and time, so a
test's shas are the same on every run. Refs, ancestry and the flat view of a tree are
what the routes need; nothing else of git is modelled.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Final

BLOB_MODE: Final = "100644"
EXECUTABLE_MODE: Final = "100755"
TREE_MODE: Final = "040000"
# A fixed author and time, so commit shas do not depend on when a test runs.
_SIGNATURE: Final = "Nexovue Fake <fake@example.invalid> 1700000000 +0000"


def object_id(kind: str, body: bytes) -> str:
    return hashlib.sha1(
        b"%s %d\0" % (kind.encode(), len(body)) + body, usedforsecurity=False
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class TreeEntry:
    name: str
    mode: str
    kind: str  # "blob" or "tree"
    sha: str


@dataclass(frozen=True, slots=True)
class Commit:
    sha: str
    tree: str
    parents: tuple[str, ...]
    message: str


@dataclass
class GitData:
    """One repository's objects and branches."""

    blobs: dict[str, bytes] = field(default_factory=dict)
    trees: dict[str, tuple[TreeEntry, ...]] = field(default_factory=dict)
    commits: dict[str, Commit] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------- writing

    def add_blob(self, data: bytes) -> str:
        sha = object_id("blob", data)
        self.blobs[sha] = data
        return sha

    def add_tree(self, entries: list[TreeEntry]) -> str:
        # Git orders entries by name, a tree's name as if it ended in `/`.
        ordered = sorted(entries, key=lambda e: e.name + ("/" if e.kind == "tree" else ""))
        body = b"".join(
            f"{e.mode.lstrip('0')} {e.name}".encode() + b"\0" + bytes.fromhex(e.sha)
            for e in ordered
        )
        sha = object_id("tree", body)
        self.trees[sha] = tuple(ordered)
        return sha

    def tree_from_paths(self, files: Mapping[str, tuple[str, str]]) -> str:
        """A nested tree from `path -> (mode, blob sha)`."""
        here: list[TreeEntry] = []
        folders: dict[str, dict[str, tuple[str, str]]] = {}
        for path, (mode, sha) in files.items():
            head, _, rest = path.partition("/")
            if rest:
                folders.setdefault(head, {})[rest] = (mode, sha)
            else:
                here.append(TreeEntry(head, mode, "blob", sha))
        for name, inner in folders.items():
            here.append(TreeEntry(name, TREE_MODE, "tree", self.tree_from_paths(inner)))
        return self.add_tree(here)

    def add_commit(self, tree: str, parents: tuple[str, ...], message: str) -> str:
        lines = [
            f"tree {tree}",
            *(f"parent {p}" for p in parents),
            f"author {_SIGNATURE}",
            f"committer {_SIGNATURE}",
            "",
            message,
        ]
        sha = object_id("commit", "\n".join(lines).encode())
        self.commits[sha] = Commit(sha, tree, parents, message)
        return sha

    def commit_files(
        self, branch: str, files: Mapping[str, str | tuple[str, str]], message: str
    ) -> str:
        """A commit on `branch` whose tree is the branch's with `files` changed, each
        `path -> text` or `path -> (mode, text)`; the branch moves to it."""
        flat = dict(self.flat(self.commits[self.refs[branch]].tree)) if branch in self.refs else {}
        for path, value in files.items():
            mode, text = value if isinstance(value, tuple) else (BLOB_MODE, value)
            flat[path] = (mode, self.add_blob(text.encode("utf-8")))
        parents = (self.refs[branch],) if branch in self.refs else ()
        sha = self.add_commit(self.tree_from_paths(flat), parents, message)
        self.refs[branch] = sha
        return sha

    # ------------------------------------------------------------- reading

    def walk(self, tree: str, prefix: str = "") -> Iterator[tuple[str, TreeEntry]]:
        """Every entry under `tree`, folders before what they hold, as GitHub lists them."""
        for entry in self.trees[tree]:
            path = f"{prefix}{entry.name}"
            yield path, entry
            if entry.kind == "tree":
                yield from self.walk(entry.sha, f"{path}/")

    def flat(self, tree: str) -> Iterator[tuple[str, tuple[str, str]]]:
        """Each file under `tree` as `path -> (mode, blob sha)`."""
        for path, entry in self.walk(tree):
            if entry.kind == "blob":
                yield path, (entry.mode, entry.sha)

    def ancestors(self, sha: str) -> list[str]:
        """`sha` and every commit before it, nearest first."""
        seen: list[str] = []
        queue = [sha]
        while queue:
            current = queue.pop(0)
            if current in seen or current not in self.commits:
                continue
            seen.append(current)
            queue.extend(self.commits[current].parents)
        return seen
