"""The fixed lists rung 2b's rules read (design, *Rung 2b*, G5 and G6; plan T2b.3).

Example code is made to be copied, with no file and no reviewer behind it, so what rung 3
shows as a flag refuses here (D68). **Every list is a guess the bed measures** (T2b.10):
too short refuses honest examples, too long lets a dependency or a command through.

- **Standard libraries:** the top-level names an example may import, per language. A name
  not listed refuses, local and relative modules included, since an example must not
  invent the customer's own modules.
- **Process starts and dynamic code:** written per language as exact names, not rung 3's
  `exec\\w*(` flag pattern, which would refuse a database cursor's `execute(`. All
  languages' lists apply to every example, so a mislabelled block is still read.
- **Shell and package managers:** words and commands, over prose and code alike.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

from nexovue_platform.fixes.records import GeneralLanguage

# --------------------------------------------------------- standard libraries

_PYTHON: Final = frozenset(
    {
        "abc", "argparse", "asyncio", "base64", "bisect", "collections", "concurrent",
        "contextlib", "contextvars", "copy", "csv", "dataclasses", "datetime", "decimal",
        "enum", "errno", "functools", "hashlib", "heapq", "hmac", "html", "http", "io",
        "ipaddress", "itertools", "json", "logging", "math", "numbers", "operator", "os",
        "pathlib", "queue", "random", "re", "secrets", "select", "signal", "sqlite3",
        "statistics", "string", "struct", "sys", "tempfile", "textwrap", "threading",
        "time", "timeit", "traceback", "types", "typing", "unicodedata", "urllib", "uuid",
        "warnings", "weakref", "zlib", "zoneinfo",
    }
)  # fmt: skip
# Node's built-in modules, written bare or with `node:`.
_NODE: Final = frozenset(
    {
        "assert", "async_hooks", "buffer", "crypto", "dns", "events", "fs", "fs/promises",
        "http", "https", "net", "os", "path", "perf_hooks", "querystring", "readline",
        "stream", "string_decoder", "timers", "timers/promises", "url", "util", "zlib",
    }
)  # fmt: skip
# Java and Kotlin: package prefixes, matched on whole segments.
_JAVA: Final = ("java", "javax")
_KOTLIN: Final = ("java", "javax", "kotlin")
_GO: Final = frozenset(
    {
        "bufio", "bytes", "context", "crypto", "database", "encoding", "errors", "fmt",
        "hash", "io", "log", "math", "net", "os", "path", "reflect", "regexp", "sort",
        "strconv", "strings", "sync", "time", "unicode",
    }
)  # fmt: skip
_RUBY: Final = frozenset(
    {
        "base64", "bigdecimal", "csv", "date", "digest", "json", "logger", "net",
        "securerandom", "set", "time", "timeout", "uri", "yaml",
    }
)  # fmt: skip
# PHP: the global classes an example may `use`; any `require` or `include` loads a file.
_PHP: Final = frozenset(
    {
        "DateInterval", "DateTime", "DateTimeImmutable", "Exception", "InvalidArgumentException",
        "JsonException", "LogicException", "PDO", "PDOException", "RuntimeException",
        "Throwable", "TypeError",
    }
)  # fmt: skip
_CSHARP: Final = ("System",)
_RUST: Final = ("std", "core", "alloc")

# ------------------------------------------------------------- import forms

_PYTHON_IMPORT: Final = re.compile(r"^\s*(?:from\s+(\.*[\w.]*)\s+import\b|import\s+([\w., ]+))")
_JS_FROM: Final = re.compile(r"""(?:^|\s)(?:import|export)\b[^'"`]*?\bfrom\s*['"`]([^'"`]+)['"`]""")
_JS_BARE: Final = re.compile(r"""^\s*import\s*['"`]([^'"`]+)['"`]""")
_JS_CALL: Final = re.compile(r"""\b(?:require|import)\s*\(\s*['"`]?([^'"`)]*)""")
_JVM_IMPORT: Final = re.compile(r"^\s*import\s+(?:static\s+)?([\w.*]+)")
_GO_SINGLE: Final = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"')
_GO_BLOCK: Final = re.compile(r"\bimport\s*\(([^)]*)\)", re.DOTALL)
_GO_QUOTED: Final = re.compile(r'"([^"]+)"')
_RUBY_REQUIRE: Final = re.compile(
    r"""\b(require|require_relative|load)\b\s*\(?\s*['"]?([^'"\s)]*)"""
)
_PHP_USE: Final = re.compile(r"^\s*use\s+\\?([\w\\]+)")
_PHP_LOAD: Final = re.compile(r"\b(?:require|require_once|include|include_once)\b")
_CSHARP_USING: Final = re.compile(r"^\s*(?:global\s+)?using\s+(?:static\s+)?([\w.]+)\s*;")
_RUST_USE: Final = re.compile(r"^\s*(?:pub\s+)?use\s+:{0,2}(\w+)")
_RUST_CRATE: Final = re.compile(r"^\s*extern\s+crate\b")


def _python(code: str) -> list[str]:
    found: list[str] = []
    for line in code.splitlines():
        match = _PYTHON_IMPORT.match(line)
        if match is None:
            continue
        if match.group(1) is not None:
            found.append(match.group(1))
        else:
            found += [part.split(" as ")[0].strip() for part in match.group(2).split(",")]
    return [name for name in found if _top(name, ".") not in _PYTHON]


def _javascript(code: str) -> list[str]:
    names = [m.group(1) for m in _JS_FROM.finditer(code)]
    names += [m.group(1) for line in code.splitlines() if (m := _JS_BARE.match(line))]
    names += [m.group(1) for m in _JS_CALL.finditer(code)]
    return [name for name in names if name.removeprefix("node:") not in _NODE]


def _prefixed(names: list[str], allowed: tuple[str, ...]) -> list[str]:
    return [name for name in names if _top(name, ".") not in allowed]


def _jvm(allowed: tuple[str, ...]) -> Callable[[str], list[str]]:
    def unlisted(code: str) -> list[str]:
        return _prefixed(
            [m.group(1) for line in code.splitlines() if (m := _JVM_IMPORT.match(line))],
            allowed,
        )

    return unlisted


def _go(code: str) -> list[str]:
    names = [m.group(1) for line in code.splitlines() if (m := _GO_SINGLE.match(line))]
    for block in _GO_BLOCK.finditer(code):
        names += _GO_QUOTED.findall(block.group(1))
    return [name for name in names if _top(name, "/") not in _GO]


def _ruby(code: str) -> list[str]:
    unlisted = []
    for match in _RUBY_REQUIRE.finditer(code):
        kind, name = match.groups()
        if kind != "require" or _top(name, "/") not in _RUBY:
            unlisted.append(name or kind)
    return unlisted


def _php(code: str) -> list[str]:
    names = [m.group(1) for line in code.splitlines() if (m := _PHP_USE.match(line))]
    unlisted = [name for name in names if name not in _PHP]
    return unlisted + [m.group(0) for m in _PHP_LOAD.finditer(code)]


def _csharp(code: str) -> list[str]:
    return _prefixed(
        [m.group(1) for line in code.splitlines() if (m := _CSHARP_USING.match(line))],
        _CSHARP,
    )


def _rust(code: str) -> list[str]:
    unlisted = [line for line in code.splitlines() if _RUST_CRATE.match(line)]
    names = [m.group(1) for line in code.splitlines() if (m := _RUST_USE.match(line))]
    return unlisted + [name for name in names if name not in _RUST]


def _top(name: str, separator: str) -> str:
    return name.split(separator)[0]


_UNLISTED_IMPORTS: Final[dict[str, Callable[[str], list[str]]]] = {
    "python": _python,
    "javascript": _javascript,
    "typescript": _javascript,
    "java": _jvm(_JAVA),
    "kotlin": _jvm(_KOTLIN),
    "go": _go,
    "ruby": _ruby,
    "php": _php,
    "csharp": _csharp,
    "rust": _rust,
}


def unlisted_imports(code: str, language: GeneralLanguage) -> list[str]:
    """Each module `code` imports that is not on `language`'s standard-library list (G6)."""
    return _UNLISTED_IMPORTS[language](code)


# ------------------------------------------------- process starts, dynamic code

PROCESS_AND_DYNAMIC_CODE: Final = (
    # Python.
    r"\bsubprocess\b",
    r"\bos\.(?:system|popen|exec\w*|spawn\w*|fork\w*)\b",
    r"\bpty\.spawn\b",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\bcompile\s*\(",
    r"\b__import__\s*\(",
    r"\bimportlib\b",
    r"\bpickle\.loads?\b",
    r"\bmarshal\.loads?\b",
    # JavaScript and TypeScript.
    r"\bchild_process\b",
    r"\bnew\s+Function\b",
    r"\bFunction\s*\(",
    r"\bvm\.run\w*",
    r"\bspawn(?:Sync)?\s*\(",
    r"\bexecSync\s*\(",
    r"\bexecFile\w*\s*\(",
    # Java and Kotlin.
    r"\bRuntime\.getRuntime\b",
    r"\bProcessBuilder\b",
    r"\bScriptEngine\w*",
    # Go.
    r"\bexec\.Command\w*",
    r"\bsyscall\.(?:Exec|ForkExec)\b",
    # Ruby.
    r"\bsystem\s*\(",
    r"\bKernel\.(?:exec|system|spawn)\b",
    r"\bIO\.popen\b",
    r"\bOpen3\b",
    r"\binstance_eval\b",
    r"\bclass_eval\b",
    r"%x[({\[]",
    # PHP.
    r"\b(?:shell_exec|passthru|proc_open|popen|pcntl_exec)\s*\(",
    r"\bcreate_function\s*\(",
    # C#.
    r"\bProcess\.Start\b",
    r"\bProcessStartInfo\b",
    # Rust.
    r"\bCommand::new\b",
    # A shell's backquotes, in the languages that run them (Ruby, PHP, Perl-like).
    r"`[^`\n]*`",
)
_PROCESS: Final = re.compile("|".join(PROCESS_AND_DYNAMIC_CODE))
# Backquotes are template strings in JavaScript and TypeScript, so there they are allowed.
_PROCESS_WITHOUT_BACKQUOTES: Final = re.compile("|".join(PROCESS_AND_DYNAMIC_CODE[:-1]))
_TEMPLATE_STRING_LANGUAGES: Final = frozenset({"javascript", "typescript", "go", "kotlin"})


def process_or_dynamic_code(code: str, language: GeneralLanguage) -> str | None:
    """The first process start or code-from-text call in `code`, or None (G5)."""
    pattern = _PROCESS_WITHOUT_BACKQUOTES if language in _TEMPLATE_STRING_LANGUAGES else _PROCESS
    found = pattern.search(code)
    return None if found is None else found.group(0)


# ------------------------------------------------------ shell, package managers

SHELL_WORDS: Final = (
    r"\bcurl\b",
    r"\bwget\b",
    r"\bsudo\b",
    r"\bchmod\b",
    r"\bchown\b",
    r"\bbash\b",
    r"\bsh\s+-c\b",
    r"\bpowershell\b",
    r"\biex\b",
    r"\bInvoke-WebRequest\b",
    r"\brm\s+-",
    r"\|\s*(?:sudo\s+)?(?:ba)?sh\b",
)
_SHELL: Final = re.compile("|".join(SHELL_WORDS), re.IGNORECASE)
# A prompt line: `$ ` and a command word.
_PROMPT_LINE: Final = re.compile(r"^\s*\$\s+[A-Za-z]", re.MULTILINE)


def shell_command(text: str) -> str | None:
    """The first shell command in `text`, or None (G5, over prose and code)."""
    found = _SHELL.search(text) or _PROMPT_LINE.search(text)
    return None if found is None else found.group(0).strip()


# Bare words where the name is only a tool; the command form where the word is also
# English ("a gem", "a bundle", "an apt name").
PACKAGE_MANAGERS: Final = (
    r"\bpip3?\b",
    r"\bpipx\b",
    r"\bnpm\b",
    r"\bnpx\b",
    r"\byarn\b",
    r"\bpnpm\b",
    r"\bmvn\b",
    r"\bgradle\b",
    r"\bbrew\b",
    r"\bcomposer\b",
    r"\bapt-get\b",
    r"\bapt\s+(?:add|install|update|upgrade|remove)\b",
    r"\bgo\s+(?:get|install)\b",
    r"\bcargo\s+(?:add|install)\b",
    r"\bgem\s+(?:install|update)\b",
    r"\bbundle\s+(?:add|install|exec|update)\b",
    r"\bdotnet\s+add\b",
    r"\bnuget\b",
    r"\bconda\b",
    r"\bpoetry\s+add\b",
    r"\buv\s+(?:add|pip)\b",
)
# Install, download and upgrade, as words and their plain forms.
SETUP_WORDS: Final = r"\b(?:install|download|upgrade)(?:s|ed|ing|ation)?\b"
_SETUP: Final = re.compile("|".join((SETUP_WORDS, *PACKAGE_MANAGERS)), re.IGNORECASE)


def setup_step(text: str) -> str | None:
    """The first install, download or upgrade word or package-manager command, or None."""
    found = _SETUP.search(text)
    return None if found is None else found.group(0)
