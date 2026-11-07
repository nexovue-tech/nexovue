"""Which modules a module of this repository may import, read from source and never run.

The no-AI structure tests walk from a root package through every module of this
repository it imports, and deny a named list of AI modules and packages (F6 AC27, F19
FR33, F20 D38). The walk lives here, not in one suite, so each feature's root uses the
same walk and the same list (foundation section 1; F19 D55).

It reads source with `ast` and never imports it, so it also sees imports inside
functions and under `TYPE_CHECKING`, and a module whose dependency is not installed.

**What counts as an AI module.** A search of the repository on 2026-11-01 found no LLM
provider SDK among its dependencies, and no AI on/off switch. It found two AI-shaped
modules of its own: `nexovue_api.assistant`, the MCP server AI assistants read nexovue
through, built on the `mcp` SDK, and `nexovue_api.llm_observability`, which reads
customers' model-call spans. So the walk denies a named list: those two modules, the
`mcp` SDK, and the known model-provider, LLM-framework and machine-learning packages.
A new AI module or provider SDK must be added to it when it is adopted.

**Its limit.** A provider reached without importing its SDK (plain HTTP, or boto3's
Bedrock client) is not visible here. `NETWORK_CLIENTS` lets a suite also refuse direct
network clients in a package that must not call out; the rest is a reviewer's catch.
"""

from __future__ import annotations

import ast
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Final

REPOSITORY: Final = Path(__file__).resolve().parents[4]
# Where this repository's importable Python lives; anything else is third-party or the stdlib.
SOURCE_ROOTS: Final = (
    REPOSITORY / "apps/scheduler/src",
    REPOSITORY / "packages/py-platform/src",
    REPOSITORY / "apps/api/src",
    REPOSITORY / "packages/contracts/gen/python",
)

REPOSITORY_AI_MODULES: Final = frozenset({"nexovue_api.assistant", "nexovue_api.llm_observability"})
AI_PACKAGES: Final = frozenset(
    {
        # Model providers' SDKs.
        "anthropic",
        "openai",
        "google.genai",
        "google.generativeai",
        "google.cloud.aiplatform",
        "vertexai",
        "mistralai",
        "cohere",
        "ollama",
        "groq",
        "together",
        "replicate",
        "huggingface_hub",
        # LLM frameworks and agent protocols.
        "mcp",
        "fastmcp",
        "langchain",
        "langchain_core",
        "langchain_community",
        "langchain_openai",
        "langchain_anthropic",
        "langgraph",
        "llama_index",
        "litellm",
        "instructor",
        "dspy",
        "pydantic_ai",
        "semantic_kernel",
        "tiktoken",
        # Machine learning: decision 0013 makes version 1 statistical.
        "transformers",
        "sentence_transformers",
        "torch",
        "tensorflow",
        "keras",
        "jax",
        "sklearn",
        "xgboost",
        "lightgbm",
    }
)
DENIED: Final = REPOSITORY_AI_MODULES | AI_PACKAGES
DYNAMIC_IMPORTERS: Final = frozenset({"import_module", "__import__"})


def module_file(name: str) -> Path | None:
    """The file a module name resolves to in this repository, or None if it lives elsewhere."""
    relative = Path(*name.split("."))
    for root in SOURCE_ROOTS:
        for candidate in (root / relative.with_suffix(".py"), root / relative / "__init__.py"):
            if candidate.is_file():
                return candidate
    return None


def imports_of(name: str, path: Path) -> set[str]:
    """Every module `path` may import: plain and relative imports, each `from x import y` as both
    `x` and `x.y` (y may be a submodule), and string-literal dynamic imports."""
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                base = ".".join([*anchor, base] if base else anchor)
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
        elif (
            isinstance(node, ast.Call)
            and _called_name(node.func) in DYNAMIC_IMPORTERS
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            found.add(str(node.args[0].value))
    return found


def _called_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


def _with_parents(name: str) -> Iterator[str]:
    """A module and every package above it: importing `a.b.c` runs `a` and `a.b` first."""
    parts = name.split(".")
    for end in range(1, len(parts) + 1):
        yield ".".join(parts[:end])


def denied(name: str) -> bool:
    return any(name == ai or name.startswith(f"{ai}.") for ai in DENIED)


def ai_imports_reachable_from(start: list[str]) -> list[str]:
    """Each AI import reachable from `start`, as the chain of modules that reaches it."""
    came_from: dict[str, str | None] = dict.fromkeys(start)
    queue = deque(start)
    reached: list[str] = []
    while queue:
        module = queue.popleft()
        path = module_file(module)
        if path is None:
            continue
        for imported in sorted(imports_of(module, path)):
            if denied(imported):
                chain = [imported, module]
                while (parent := came_from[chain[-1]]) is not None:
                    chain.append(parent)
                reached.append(" <- ".join(chain))
                continue
            for step in _with_parents(imported):
                if step not in came_from and module_file(step) is not None:
                    came_from[step] = module
                    queue.append(step)
    return reached


# Clients that reach another host directly. A package that must never call out, such
# as F19's, imports none of them itself (F19 AC96).
NETWORK_CLIENTS: Final = frozenset(
    {
        "httpx",
        "requests",
        "aiohttp",
        "urllib3",
        "urllib.request",
        "http.client",
        "pycurl",
        "socket",
        "websockets",
        "grpc",
        "boto3",
        "botocore",
    }
)


def package_modules(package: str) -> list[str]:
    """A package, the packages above it, and every module beneath it, subfolders too."""
    init = module_file(package)
    if init is None:
        return []
    root = init.parent
    names = [
        ".".join([package, *path.relative_to(root).with_suffix("").parts])
        for path in sorted(root.rglob("*.py"))
        if path.name != "__init__.py"
    ] + [
        ".".join([package, *path.parent.relative_to(root).parts])
        for path in sorted(root.rglob("__init__.py"))
        if path.parent != root
    ]
    return [*_with_parents(package), *sorted(names)]


def direct_imports_matching(modules: list[str], denied_names: frozenset[str]) -> list[str]:
    """Each module of `modules` that itself imports one of `denied_names`."""
    found = []
    for module in modules:
        path = module_file(module)
        if path is None:
            continue
        for imported in sorted(imports_of(module, path)):
            if any(imported == name or imported.startswith(f"{name}.") for name in denied_names):
                found.append(f"{imported} <- {module}")
    return found
