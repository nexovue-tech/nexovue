"""F28's words and its reads of stored spans (plan T4.7).

**AC37.** No response, screen or setup document of F28 may say or imply that a release
caused anything. This searches what can reach a person: every Python string literal in
F28's modules except docstrings, the TypeScript of its screens with comments taken out,
and the setup page whole. A search catches words, not an implied cause, so review still
reads for the rest.

**AC34, BR6.** No comparison may read `spans` or `logs` for a rate or a latency: sampling
keeps every error and about one healthy trace in ten. Only `ERRORS_BY_VERSION_SQL`
(which errors each version had) and `ENVIRONMENTS_SQL` (which labels appear) may read
them, and neither may read a duration.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[3]

FORBIDDEN: Final = (
    "caused by",
    "because of this release",
    "regression from",
    "bad release",
    "due to",
    "introduced by",
    "triggered",
    "responsible for",
    "after deploying",
    "impact",
)

PYTHON: Final = (
    ROOT / "apps/api/src/nexovue_api/releases",
    ROOT / "apps/api/src/nexovue_api/deploys",
    ROOT / "packages/py-platform/src/nexovue_platform/releases.py",
    ROOT / "apps/scheduler/src/nexovue_scheduler/releases",
)
SCREENS: Final = (
    ROOT / "apps/web/src/features/releases",
    ROOT / "apps/web/src/shared/releases",
    ROOT / "apps/web/src/shared/deploy-markers.ts",
    ROOT / "apps/web/src/shared/components/ChartReleases.tsx",
    ROOT / "apps/web/src/shared/components/ReleaseHoverBox.tsx",
    ROOT / "apps/web/src/features/projects/DeploysSection.tsx",
    ROOT / "apps/web/src/features/projects/deploys-api.ts",
)
SETUP_PAGE: Final = ROOT / "docs/product/setup/deploys.md"

# Where F28 states its own SQL. `releases/queries.py` takes the errors statement from
# the errors list's module, so it is named here as one F28 reads.
F28_STATEMENT_MODULES: Final = (
    ROOT / "apps/api/src/nexovue_api/releases/queries.py",
    *sorted((ROOT / "apps/scheduler/src/nexovue_scheduler/releases").glob("*.py")),
)
ERRORS_MODULE: Final = ROOT / "apps/api/src/nexovue_api/errorgroups/queries.py"
ALLOWED_RAW_READS: Final = frozenset({"ERRORS_BY_VERSION_SQL", "ENVIRONMENTS_SQL"})
READS_RAW_ROWS: Final = re.compile(r"\bFROM\s+(spans|logs)\b", re.IGNORECASE)
# What a rate or a latency would need from a stored span.
LATENCY_OR_RATE: Final = re.compile(r"duration_ms|quantile|avg\(|/\s*count", re.IGNORECASE)
_COMMENTS: Final = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)


def _files(paths: tuple[Path, ...], suffixes: tuple[str, ...]) -> Iterator[Path]:
    for path in paths:
        found = [path] if path.is_file() else sorted(path.rglob("*"))
        for each in found:
            if each.suffix in suffixes and ".test." not in each.name:
                yield each


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _python_strings(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text())
    skipped = _docstrings(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skipped
        ):
            yield node.value


def _hits(text: str) -> list[str]:
    lowered = text.lower()
    return [word for word in FORBIDDEN if word in lowered]


def test_the_python_words_never_name_a_cause() -> None:
    """AC37: responses and the strings that build them."""
    found = {
        f"{path.relative_to(ROOT)}: {hits}"
        for path in _files(PYTHON, (".py",))
        for text in _python_strings(path)
        if (hits := _hits(text))
    }
    assert not found, sorted(found)


def test_the_screens_never_name_a_cause() -> None:
    """AC37: every F28 screen's text, comments aside."""
    found = {
        f"{path.relative_to(ROOT)}: {hits}"
        for path in _files(SCREENS, (".ts", ".tsx"))
        if (hits := _hits(_COMMENTS.sub("", path.read_text())))
    }
    assert not found, sorted(found)


def test_the_setup_page_never_names_a_cause() -> None:
    assert not _hits(SETUP_PAGE.read_text())


def test_the_search_finds_a_forbidden_word() -> None:
    """The search itself is held: a word in a string is found, one in a docstring is not."""
    source = '"""Never caused by anything."""\nWORDS = "a regression from v2"\n'
    tree = ast.parse(source)
    skipped = _docstrings(tree)
    strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skipped
    ]
    assert [hit for text in strings for hit in _hits(text)] == ["regression from"]


def _module_statements(path: Path) -> dict[str, str]:
    """Each module-level name bound to a string, with the string it holds."""
    tree = ast.parse(path.read_text())
    found = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if value is None:
            continue
        text = ast.unparse(value)
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = text
    return found


def test_only_the_two_named_statements_read_stored_spans_or_logs() -> None:
    """AC34: nothing else in F28 reads raw rows."""
    readers = {
        f"{path.relative_to(ROOT)}:{name}"
        for path in F28_STATEMENT_MODULES
        for name, text in _module_statements(path).items()
        if READS_RAW_ROWS.search(text)
    }
    assert readers <= {
        f"{path.relative_to(ROOT)}:{name}"
        for path in F28_STATEMENT_MODULES
        for name in ALLOWED_RAW_READS
    }, sorted(readers)
    imported = {
        alias.name
        for node in ast.walk(ast.parse(F28_STATEMENT_MODULES[0].read_text()))
        if isinstance(node, ast.ImportFrom) and node.module == "nexovue_api.errorgroups.queries"
        for alias in node.names
    }
    assert imported & {name for name in _module_statements(ERRORS_MODULE) if "SQL" in name} == {
        "ERRORS_BY_VERSION_SQL"
    }


def test_neither_raw_read_measures_a_rate_or_a_latency() -> None:
    """AC34, BR6: presence and group counts only."""
    from nexovue_api.errorgroups.queries import ERRORS_BY_VERSION_SQL
    from nexovue_api.releases.queries import ENVIRONMENTS_SQL

    for statement in (ERRORS_BY_VERSION_SQL, ENVIRONMENTS_SQL):
        assert not LATENCY_OR_RATE.search(statement), statement
