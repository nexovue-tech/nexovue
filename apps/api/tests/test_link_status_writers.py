"""AC88: only the click route produces a `creating` link (F22 plan T6.1).

A `creating` link is the one thing that makes `scheduler-connections` write a new issue
into a customer's tracker, so the only way to one must be a person's click on a preview
(FR35, AC83). A promotion makes a reference `promoting`, never `creating`.

Structural, over every writer of that status in the API, the scheduler and the shared
package: an ORM keyword or attribute set to it, and every SQL text that names it outside
a condition. It cannot see a value built at run time; nothing builds one today.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import nexovue_platform

from nexovue_api import auth

REPOSITORY: Final = Path(auth.__file__).resolve().parents[5]
SOURCES: Final = (
    REPOSITORY / "apps" / "api" / "src",
    REPOSITORY / "apps" / "scheduler" / "src",
    Path(nexovue_platform.__file__).resolve().parent,
)
STATUS: Final = "creating"
STATUS_NAME: Final = "CREATING"
# The one writer, by `path:function` from its source root.
CLICK_ROUTE: Final = "nexovue_api/findings/links.py:create"

# `'creating'` as a condition: after WHERE, AND, OR or WHEN, or in an IN list.
_SQL_STATUS: Final = re.compile(r"'creating'")
_CONDITION_BEFORE: Final = re.compile(
    r"(\bWHERE\b|\bAND\b|\bOR\b|\bWHEN\b|\bIN\s*\([^)]*|<>|!=|\bNOT\s+IN\s*\([^)]*)"
    r"[^,;]*$",
    re.IGNORECASE,
)


def _modules() -> Iterator[tuple[str, ast.Module]]:
    for root in SOURCES:
        for path in sorted(root.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if STATUS in source or STATUS_NAME in source:
                relative = path.relative_to(root.parent if root.name != "src" else root)
                yield relative.as_posix(), ast.parse(source, filename=str(path))


def _is_status(node: ast.AST) -> bool:
    return (isinstance(node, ast.Constant) and node.value == STATUS) or (
        isinstance(node, ast.Name) and node.id == STATUS_NAME
    )


def _functions(tree: ast.Module) -> Iterator[tuple[str, ast.AST]]:
    """Each node with the name of the function it sits in ("" at module level)."""

    def walk(node: ast.AST, inside: str) -> Iterator[tuple[str, ast.AST]]:
        for child in ast.iter_child_nodes(node):
            name = (
                child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else inside
            )
            yield name, child
            yield from walk(child, name)

    yield from walk(tree, "")


def _sql_writes(text: str) -> bool:
    """Whether a SQL text sets the status, rather than only testing for it."""
    for found in _SQL_STATUS.finditer(text):
        before = text[: found.start()]
        tail = before[before.upper().rfind("SET") :] if "SET" in before.upper() else before
        if not _CONDITION_BEFORE.search(tail):
            return True
    return False


def _writers() -> list[str]:
    found: list[str] = []
    for path, tree in _modules():
        for function, node in _functions(tree):
            where = f"{path}:{function}"
            if isinstance(node, ast.keyword) and node.arg == "sync_status":
                if _is_status(node.value):
                    found.append(where)
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                sets_status = any(
                    isinstance(target, ast.Attribute) and target.attr == "sync_status"
                    for target in targets
                )
                if sets_status and node.value is not None and _is_status(node.value):
                    found.append(where)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "finding_links" in node.value
                and _sql_writes(node.value)
            ):
                found.append(where)
    return found


def test_only_the_click_route_writes_a_creating_link() -> None:
    assert all(root.is_dir() for root in SOURCES), "a source root moved; the scan saw nothing"
    assert _writers() == [CLICK_ROUTE]


def test_the_sql_reading_tells_a_write_from_a_condition() -> None:
    """The scanner's own limits, held: a write is seen, a condition is not."""
    assert _sql_writes("UPDATE finding_links SET sync_status = 'creating' WHERE id = :id")
    assert _sql_writes("INSERT INTO finding_links (sync_status) VALUES ('creating')")
    assert not _sql_writes(
        "UPDATE finding_links SET failures = 0 WHERE id = :id AND sync_status = 'creating'"
    )
    assert not _sql_writes(
        "UPDATE finding_links SET sync_status = CASE WHEN sync_status IN ('creating', "
        "'promoting') THEN sync_status ELSE 'waiting' END"
    )
