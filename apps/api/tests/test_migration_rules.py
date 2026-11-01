"""Rules every Alembic migration is held to (group 9, C6; `docs/engineering/database.md`).

- **A lock timeout.** A migration waiting on a lock behind a long transaction
  holds its own lock request at the head of the queue, and every write to that
  table waits behind it — a write outage that looks like a hang. With
  `lock_timeout` it fails instead, and can be run again.
- **An index on a table that already holds rows is built concurrently.** A plain
  `CREATE INDEX`, or a unique or primary key constraint, which builds one, blocks
  every write for as long as the build takes. Read from each migration's source:
  an index is built plainly only on a table the same migration creates.
- **A downgrade that would drop records that exist nowhere else refuses.** Going
  back past it is a restore, never `alembic downgrade`. Whether a column holds
  such records is a judgement no test can make, so the revisions are listed.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

VERSIONS: Final = Path(__file__).resolve().parents[1] / "migrations" / "versions"
ENV: Final = VERSIONS.parent / "env.py"
# 0020, 0022 and 0026 drop billing records; 0041 findings, with people's acknowledgements
# and false-alarm reasons, and rules people wrote; 0044 the PagerDuty alert each delivered
# notification went to; 0046 the services a closed project finding was about.
ROLL_FORWARD_ONLY: Final = ("0020", "0022", "0026", "0041", "0044", "0046")
# The concurrent-index rule came with group 9, after 0039; earlier revisions ran before it.
FIRST_HELD_TO_CONCURRENT_INDEXES: Final = "0040"

# Alembic operations that build an index, and the argument naming their table.
_INDEX_BUILDERS: Final = {"create_index": 1, "create_unique_constraint": 1, "create_primary_key": 1}
# Raw SQL that builds an index without CONCURRENTLY, or a key constraint without USING INDEX.
_PLAIN_INDEX_SQL: Final = re.compile(
    r"CREATE\s+(UNIQUE\s+)?INDEX\s+(?!CONCURRENTLY)"
    r"|ADD\s+(CONSTRAINT\s+\w+\s+)?(UNIQUE|PRIMARY\s+KEY)\s*\(",
    re.IGNORECASE,
)


def _revision(number: str) -> ModuleType:
    [path] = VERSIONS.glob(f"{number}_*.py")
    spec = importlib.util.spec_from_file_location(f"revision_{number}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _held_to_concurrent_indexes() -> list[Path]:
    return [
        path
        for path in sorted(VERSIONS.glob("[0-9]*_*.py"))
        if path.name[:4] >= FIRST_HELD_TO_CONCURRENT_INDEXES
    ]


def _op_calls(tree: ast.AST) -> Iterator[tuple[str, ast.Call]]:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
        ):
            yield node.func.attr, node


def _table(call: ast.Call, position: int) -> str:
    argument = next(
        (keyword.value for keyword in call.keywords if keyword.arg == "table_name"),
        call.args[position] if len(call.args) > position else None,
    )
    assert isinstance(argument, ast.Constant) and isinstance(argument.value, str), (
        f"line {call.lineno}: name the table as a literal, so this rule can see it"
    )
    return argument.value


def _is_true(call: ast.Call, keyword_name: str) -> bool:
    return any(
        keyword.arg == keyword_name
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in call.keywords
    )


def _plain_index_builds(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = list(_op_calls(tree))
    created = {_table(call, 0) for name, call in calls if name == "create_table"}
    found = []
    for name, call in calls:
        if name in _INDEX_BUILDERS:
            table = _table(call, _INDEX_BUILDERS[name])
            concurrent = name == "create_index" and _is_true(call, "postgresql_concurrently")
            if table not in created and not concurrent:
                found.append(f"line {call.lineno}: {name} on {table}")
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _PLAIN_INDEX_SQL.search(node.value)
        ):
            found.append(f"line {node.lineno}: {node.value.strip()[:60]}")
    return found


def test_every_migration_runs_with_a_lock_timeout() -> None:
    source = ENV.read_text(encoding="utf-8")

    assert "SET lock_timeout" in source


@pytest.mark.parametrize("path", _held_to_concurrent_indexes(), ids=lambda path: path.name[:4])
def test_an_index_on_a_table_with_rows_is_built_concurrently(path: Path) -> None:
    assert _plain_index_builds(path) == []


@pytest.mark.parametrize("number", ROLL_FORWARD_ONLY)
def test_a_downgrade_that_drops_records_found_nowhere_else_refuses(number: str) -> None:
    with pytest.raises(RuntimeError, match="roll forward only"):
        _revision(number).downgrade()
