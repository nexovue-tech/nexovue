"""One hop of imports from a stack file (spec FR12a, D73, D76; plan T3.18).

Plain line parsing, no parser library (D76): an import line is the plainest thing a source
file holds, and a missed import only means a file is not offered. Pure: it takes a file's
path and text and the folder's tree, and returns the tree's files that file imports.

**Never a library and never outside the listed folder:** every path returned is in `tree`,
which is the folder's own listing. A relative import that climbs out of the folder is
dropped. An imported file's own imports are never read here; the caller asks once.

- **Python:** `import a.b`, `from a.b import c`, `from . import x`, `from ..p import y`, and a
  parenthesised list across lines; tried as `a/b.py`, `a/b/__init__.py`, and for `from`
  imports each name as `a/b/c.py`, from the folder root and then beside the importing file.
- **JavaScript and TypeScript:** `import ... from '<p>'`, `import '<p>'`, `export ... from
  '<p>'` and `require('<p>')`, only for `./` and `../`; tried as written, with each
  extension, then as `<p>/index.*`. `tsconfig.json` aliases are not read.
- **Go:** single and grouped imports under the module path the folder's `go.mod` names;
  that package's `.go` files, never `_test.go`, at most `GO_FILES_PER_IMPORT` each.
- **Java and Kotlin:** `import a.b.C` as `a/b/C.java` or `.kt`, by the frames' suffix match.
  Wildcards and `static` imports are skipped.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from typing import Final

from nexovue_platform.fixes.matching import TreeIndex
from nexovue_platform.fixes.records import GO_FILES_PER_IMPORT

_PY_IMPORT: Final = re.compile(r"^\s*import\s+([\w.]+(?:\s*,\s*[\w.]+)*)")
_PY_FROM: Final = re.compile(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+(.+)$")
_JS_SPECIFIER: Final = re.compile(
    r"""(?:^\s*(?:import|export)\b[^'"]*?\bfrom\s*|^\s*import\s*|\brequire\s*\(\s*)"""
    r"""(['"])(\.{1,2}/[^'"]*)\1"""
)
_GO_SINGLE: Final = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"')
_GO_GROUP_START: Final = re.compile(r"^\s*import\s*\(\s*$")
_GO_GROUP_LINE: Final = re.compile(r'^\s*(?:[\w.]+\s+)?"([^"]+)"')
_GO_MODULE: Final = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)
_JVM_IMPORT: Final = re.compile(r"^\s*import\s+(?!static\b)([\w.]+)\s*;?\s*$")
_JS_EXTENSIONS: Final = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_IDENTIFIER: Final = re.compile(r"\w+")


def go_module_of(go_mod: str) -> str | None:
    """The module path a `go.mod` names (read to resolve imports, never sent)."""
    found = _GO_MODULE.search(go_mod)
    return found.group(1) if found else None


def imported_paths(
    path: str, text: str, tree: TreeIndex, *, go_module: str | None = None
) -> list[str]:
    """The files in `tree` that `path` imports, in the order its lines name them."""
    extension = path.rsplit(".", 1)[-1].casefold() if "." in path else ""
    if extension == "py":
        found = _python(path, text, tree)
    elif extension in ("js", "jsx", "mjs", "cjs", "ts", "tsx"):
        found = _javascript(path, text, tree)
    elif extension == "go":
        found = _go(text, tree, go_module)
    elif extension in ("java", "kt"):
        found = _jvm(text, tree)
    else:
        found = []
    return [each for each in dict.fromkeys(found) if each != path]


# ----------------------------------------------------------------- Python


def _python_statements(text: str) -> Iterable[str]:
    """Each import statement on one line, a parenthesised list joined."""
    lines = iter(text.splitlines())
    for line in lines:
        statement = line.split("#", 1)[0]
        if "(" in statement and re.match(r"^\s*from\s", statement) and ")" not in statement:
            for more in lines:
                statement += " " + more.split("#", 1)[0]
                if ")" in more:
                    break
        yield statement.replace("(", " ").replace(")", " ")


def _python(path: str, text: str, tree: TreeIndex) -> list[str]:
    here = posixpath.dirname(path)
    found: list[str] = []
    for statement in _python_statements(text):
        if imported := _PY_IMPORT.match(statement):
            for module in re.split(r"\s*,\s*", imported.group(1)):
                found += _python_module(module.split(" as ")[0].strip(), here, tree)
        elif loaded := _PY_FROM.match(statement):
            dots, module, names = loaded.groups()
            base = _relative_base(here, len(dots)) if dots else None
            if dots and base is None:
                continue
            for name in _IDENTIFIER.findall(re.sub(r"\bas\s+\w+", "", names)):
                found += _python_from(module, name, base, here, tree)
    return found


def _relative_base(here: str, dots: int) -> str | None:
    """The folder `from .` (one dot) or `from ..` names; None when it leaves the folder."""
    base = here
    for _ in range(dots - 1):
        if not base:
            return None
        base = posixpath.dirname(base)
    return base


def _module_files(module_path: str, tree: TreeIndex) -> list[str]:
    return [each for each in (f"{module_path}.py", f"{module_path}/__init__.py") if each in tree]


def _python_module(module: str, here: str, tree: TreeIndex) -> list[str]:
    relative = module.replace(".", "/")
    for root in ("", here):
        found = _module_files(posixpath.join(root, relative) if root else relative, tree)
        if found:
            return found[:1]
    return []


def _python_from(module: str, name: str, base: str | None, here: str, tree: TreeIndex) -> list[str]:
    relative = module.replace(".", "/")
    roots = [base] if base is not None else ["", here]
    for root in roots:
        stem = posixpath.join(root, relative) if relative else root
        # `name` as a module of its own first, then as a name inside `module`'s file.
        found = _module_files(posixpath.join(stem, name) if stem else name, tree)
        if not found and relative:
            found = _module_files(stem, tree)
        if found:
            return found[:1]
    return []


# ----------------------------------------------- JavaScript and TypeScript


def _javascript(path: str, text: str, tree: TreeIndex) -> list[str]:
    here = posixpath.dirname(path)
    found: list[str] = []
    for line in text.splitlines():
        for match in _JS_SPECIFIER.finditer(line):
            # Only a path in the folder's own listing can be named, so one climbing out
            # of the folder matches nothing (AC80).
            joined = posixpath.normpath(posixpath.join(here, match.group(2)))
            target = _first_in_tree(_javascript_tries(joined), tree)
            if target is not None:
                found.append(target)
    return found


def _javascript_tries(joined: str) -> list[str]:
    return [
        joined,
        *(joined + extension for extension in _JS_EXTENSIONS),
        *(f"{joined}/index{extension}" for extension in _JS_EXTENSIONS),
    ]


def _first_in_tree(tries: Iterable[str], tree: TreeIndex) -> str | None:
    return next((each for each in tries if each in tree), None)


# --------------------------------------------------------------------- Go


def _go(text: str, tree: TreeIndex, module: str | None) -> list[str]:
    if module is None:
        return []
    found: list[str] = []
    grouped = False
    for line in text.splitlines():
        imported = None
        if grouped:
            if line.strip().startswith(")"):
                grouped = False
                continue
            match = _GO_GROUP_LINE.match(line)
            imported = match.group(1) if match else None
        elif _GO_GROUP_START.match(line):
            grouped = True
        elif match := _GO_SINGLE.match(line):
            imported = match.group(1)
        if imported is not None and imported.startswith(module + "/"):
            found += _go_package(imported.removeprefix(module + "/"), tree)
    return found


def _go_package(package: str, tree: TreeIndex) -> list[str]:
    files = sorted(
        each
        for each in tree.paths
        if posixpath.dirname(each) == package
        and each.endswith(".go")
        and not each.endswith("_test.go")
    )
    return files[:GO_FILES_PER_IMPORT]


# ---------------------------------------------------------- Java and Kotlin


def _jvm(text: str, tree: TreeIndex) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        match = _JVM_IMPORT.match(line)
        if match is None or match.group(1).endswith(".*"):
            continue
        parts = match.group(1).split(".")
        for extension in (".java", ".kt"):
            suffix = [*parts[:-1], parts[-1] + extension]
            hits = tree.ending_with(suffix)
            if len(hits) == 1:
                found.append(hits[0])
                break
    return found
