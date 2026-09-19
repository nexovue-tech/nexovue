"""The chokepoint as a property of the source, not of the queries written so far.

Every other isolation test asks "does today's code leak?". These ask "can
tomorrow's?" — they read `apps/api/src` and fail on the *shape* of a change,
so a new screen added in a hurry cannot reach ClickHouse around the repository
without this suite naming the file that did it.

Four things are held:

1. no statement naming a telemetry table omits the tenant binding;
2. no such statement is assembled from anything but its own module constants;
3. nothing outside the chokepoint and its wiring holds the raw driver;
4. the repository is constructed in exactly one place — the session dependency.

The table names come from the ClickHouse migrations rather than a list kept
here, so a table added next month is guarded the day it is created.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from nexovue_api.data.clickhouse.base import TENANT_BINDING

_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
API_SOURCE: Final = _REPOSITORY_ROOT / "apps" / "api" / "src" / "nexovue_api"
CLICKHOUSE_MIGRATIONS: Final = _REPOSITORY_ROOT / "deploy" / "clickhouse" / "migrations"

_CREATES_A_TABLE: Final = re.compile(
    r"CREATE\s+(?:TABLE|MATERIALIZED\s+VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
    re.IGNORECASE,
)

# The name of the repository class, spelled once so a rename fails loudly here.
REPOSITORY_CLASS: Final = "TenantScopedRepository"

# Modules that may legitimately hold the raw driver: the chokepoint itself, the
# lifespan that opens and closes the pool, the dependency that hands it over,
# and the readiness probe that pings it. Everything else must go through the
# repository. Adding a module here is a deliberate decision, not a formality.
MODULES_ALLOWED_THE_RAW_CLIENT: Final = frozenset(
    {
        "data.clickhouse.base",
        "deps",
        "lifespan",
        "health.checks",
        "auth.dependencies",
        # The deletion endpoint's wiring, and the only place a `TenantScopedPurger`
        # is built. It runs no statement: it takes the client from the lifespan and
        # hands it to the purger, which binds the tenant exactly as the repository
        # does for reads. `organizations.service` deliberately never sees a client.
        "organizations.dependencies",
    }
)

# Where the repository may be built. A second site would be a second answer to
# "which tenant is this?", and only one of them would come from the session.
REPOSITORY_CONSTRUCTION_SITE: Final = ("auth.dependencies", "get_tenant_repository")

_RAW_CLIENT_NAMES: Final = frozenset(
    {"clickhouse_connect", "AsyncClient", "ClickHouseClient", "get_clickhouse", "clickhouse"}
)


@dataclass(frozen=True, slots=True)
class SourceModule:
    """One module of the API, parsed once."""

    dotted_name: str
    path: Path
    tree: ast.Module

    def __str__(self) -> str:
        return self.dotted_name


def _api_modules() -> list[SourceModule]:
    return _modules_under(API_SOURCE)


def _modules_under(source: Path) -> list[SourceModule]:
    modules: list[SourceModule] = []
    for path in sorted(source.rglob("*.py")):
        relative = path.relative_to(source).with_suffix("")
        parts = [part for part in relative.parts if part != "__init__"]
        modules.append(
            SourceModule(
                dotted_name=".".join(parts),
                path=path,
                tree=ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
            )
        )
    return modules


API_MODULES: Final = _api_modules()

# F21, ADR-012: the scheduler reads the telemetry store too, for the service
# conditions, and is held to the same three rules. Its chokepoint is
# `alerts.telemetry`, the one module allowed the raw driver.
SCHEDULER_SOURCE: Final = _REPOSITORY_ROOT / "apps" / "scheduler" / "src" / "nexovue_scheduler"
SCHEDULER_MODULES: Final = _modules_under(SCHEDULER_SOURCE)
SCHEDULER_MODULES_ALLOWED_THE_RAW_CLIENT: Final = frozenset({"alerts.telemetry"})


def _telemetry_tables() -> frozenset[str]:
    """Every table and view the ClickHouse migrations create."""
    names = {
        match.group(1).lower()
        for migration in CLICKHOUSE_MIGRATIONS.glob("*.up.sql")
        for match in _CREATES_A_TABLE.finditer(migration.read_text(encoding="utf-8"))
    }
    return frozenset(names)


TELEMETRY_TABLES: Final = _telemetry_tables()

# `ALTER TABLE <t> DELETE` is the third way to name a telemetry table, and until
# 2026-09-08 this pattern could not see it: a mutation has neither FROM nor JOIN,
# so **the one statement that can empty the store was invisible to the guard that
# exists to watch statements**. Found while building the purge path (C6).
_READS_A_TELEMETRY_TABLE: Final = re.compile(
    r"\b(?:FROM|JOIN|ALTER\s+TABLE)\s+(" + "|".join(sorted(TELEMETRY_TABLES)) + r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SqlLiteral:
    """A string in the source that reads a telemetry table."""

    module: str
    line: int
    text: str
    interpolations: tuple[ast.expr, ...]

    def __str__(self) -> str:
        return f"{self.module}:{self.line}"


def _static_text(node: ast.Constant | ast.JoinedStr) -> str:
    """The part of a string literal that is fixed at import time.

    An f-string's interpolations are left out: what is being searched for is
    the statement's own words, and `{{tenant_id:String}}` has already become
    `{tenant_id:String}` by the time the parser is done with it.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    return "".join(
        part.value
        for part in node.values
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
    )


def _interpolations(node: ast.Constant | ast.JoinedStr) -> tuple[ast.expr, ...]:
    if isinstance(node, ast.Constant):
        return ()
    return tuple(part.value for part in node.values if isinstance(part, ast.FormattedValue))


def _docstrings(tree: ast.AST) -> set[int]:
    """The id of every docstring node in a module.

    Docstrings are prose, not statements, and this file's own explanations name
    the tables they are about. Widening the pattern to `ALTER TABLE` made that
    matter immediately: `purge/service.py` opens by explaining why
    `ALTER TABLE spans DELETE` is not one statement, and the guard read the
    sentence as SQL missing a tenant binding.
    """
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, holders) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _telemetry_sql(modules: list[SourceModule] | None = None) -> list[SqlLiteral]:
    """Every string in the given source — the API's by default — that reads a
    telemetry table."""
    found: list[SqlLiteral] = []
    for module in API_MODULES if modules is None else modules:
        prose = _docstrings(module.tree)
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Constant | ast.JoinedStr):
                continue
            if id(node) in prose:
                continue
            text = _static_text(node)
            if _READS_A_TELEMETRY_TABLE.search(text):
                found.append(
                    SqlLiteral(
                        module=module.dotted_name,
                        line=node.lineno,
                        text=text,
                        interpolations=_interpolations(node),
                    )
                )
    return found


def _module_level_names(tree: ast.Module) -> frozenset[str]:
    """Names bound at module scope — the only values a statement may embed."""
    names: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            names.update(target.id for target in statement.targets if isinstance(target, ast.Name))
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            names.add(statement.target.id)
    return frozenset(names)


def _enclosing_functions(tree: ast.Module) -> dict[ast.AST, str]:
    """Every node mapped to the function it sits in, for naming a call site."""
    owner: dict[ast.AST, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for descendant in ast.walk(node):
                owner.setdefault(descendant, node.name)
    return owner


# ------------------------------------------------------------------ the checks


def test_the_tables_this_check_guards_are_the_ones_the_migrations_create() -> None:
    """A moved migration directory must break this, not silently guard nothing."""
    assert CLICKHOUSE_MIGRATIONS.is_dir(), f"no ClickHouse migrations at {CLICKHOUSE_MIGRATIONS}"
    assert "spans" in TELEMETRY_TABLES
    assert "span_trace_lookup" in TELEMETRY_TABLES


def test_the_api_source_this_check_walks_is_the_one_that_ships() -> None:
    """A check that walks nothing passes everything."""
    assert API_SOURCE.is_dir(), f"no API source at {API_SOURCE}"
    assert _telemetry_sql(), "no telemetry SQL found — this suite would prove nothing"


def test_no_statement_reading_a_telemetry_table_omits_the_tenant_binding() -> None:
    """A new screen's query must carry the binding, wherever someone puts it."""
    unscoped = [str(sql) for sql in _telemetry_sql() if TENANT_BINDING not in sql.text]

    assert not unscoped, (
        f"these statements read a telemetry table without {TENANT_BINDING}: {unscoped}"
    )


def test_no_statement_reading_a_telemetry_table_is_built_from_a_runtime_value() -> None:
    """SQL may embed its own module's constants and nothing else.

    Interpolating anything a caller supplied is how a parameterised query stops
    being one, and the tenant filter is the first thing such a query loses.
    """
    constants_by_module = {
        module.dotted_name: _module_level_names(module.tree) for module in API_MODULES
    }

    assembled = [
        f"{sql} embeds {ast.unparse(expression)}"
        for sql in _telemetry_sql()
        for expression in sql.interpolations
        if not (
            isinstance(expression, ast.Name) and expression.id in constants_by_module[sql.module]
        )
    ]

    assert not assembled, f"SQL assembled from a runtime value: {assembled}"


def test_nothing_outside_the_chokepoint_and_its_wiring_holds_the_raw_driver() -> None:
    """The repository is only a guarantee while it is the only way in."""
    offenders: list[str] = []
    for module in API_MODULES:
        if module.dotted_name in MODULES_ALLOWED_THE_RAW_CLIENT:
            continue
        for node in ast.walk(module.tree):
            reference = _raw_client_reference(node)
            if reference is not None:
                line, name = reference
                offenders.append(f"{module}:{line} references {name}")

    assert not offenders, f"the raw ClickHouse client escaped the chokepoint: {offenders}"


def _names_clickhouse(node: ast.AST) -> bool:
    """Whether an attribute's qualifier is the ClickHouse client rather than
    some other library that happens to share a class name."""
    if isinstance(node, ast.Name):
        return "clickhouse" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return "clickhouse" in node.attr.lower() or _names_clickhouse(node.value)
    return False


def _raw_client_reference(node: ast.AST) -> tuple[int, str] | None:
    """The line and the forbidden identifier this node names, if it names one."""
    if isinstance(node, ast.Name) and node.id in _RAW_CLIENT_NAMES:
        return node.lineno, node.id
    if isinstance(node, ast.Attribute) and node.attr in _RAW_CLIENT_NAMES:
        # Qualified, so the qualifier decides. `AsyncClient` is not a name
        # ClickHouse owns — httpx has one too, and matching the attribute alone
        # flagged `httpx.AsyncClient` in the mail sender, which touches no
        # store. A false positive here is worse than it looks: the obvious way
        # to silence it is an allowlist entry, and that would exempt a module
        # from the real check as well.
        if _names_clickhouse(node.value):
            return node.lineno, node.attr
        return None
    if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("clickhouse_connect"):
        return node.lineno, str(node.module)
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.startswith("clickhouse_connect"):
                return node.lineno, alias.name
    return None


def test_only_the_session_dependency_constructs_a_tenant_scoped_repository() -> None:
    """A router that could build its own could build one for another tenant."""
    sites = set()
    for module in API_MODULES:
        owners = _enclosing_functions(module.tree)
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Call) and _called_name(node.func) == REPOSITORY_CLASS:
                sites.add((module.dotted_name, owners.get(node, "<module scope>")))

    assert sites == {REPOSITORY_CONSTRUCTION_SITE}, (
        f"{REPOSITORY_CLASS} is constructed outside the session dependency: {sorted(sites)}"
    )


def _called_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


# ------------------------------------------------------------ the scheduler (F21)


def test_the_scheduler_source_this_check_walks_is_the_one_that_ships() -> None:
    assert SCHEDULER_SOURCE.is_dir(), f"no scheduler source at {SCHEDULER_SOURCE}"
    assert _telemetry_sql(SCHEDULER_MODULES), "no scheduler telemetry SQL found"


def test_no_scheduler_statement_reading_a_telemetry_table_omits_the_tenant_binding() -> None:
    unscoped = [
        str(sql) for sql in _telemetry_sql(SCHEDULER_MODULES) if TENANT_BINDING not in sql.text
    ]
    assert not unscoped, (
        f"these scheduler statements read a telemetry table without {TENANT_BINDING}: {unscoped}"
    )


def test_no_scheduler_statement_reading_a_telemetry_table_is_built_from_a_runtime_value() -> None:
    constants_by_module = {
        module.dotted_name: _module_level_names(module.tree) for module in SCHEDULER_MODULES
    }
    assembled = [
        f"{sql} embeds {ast.unparse(expression)}"
        for sql in _telemetry_sql(SCHEDULER_MODULES)
        for expression in sql.interpolations
        if not (
            isinstance(expression, ast.Name) and expression.id in constants_by_module[sql.module]
        )
    ]
    assert not assembled, f"scheduler SQL assembled from a runtime value: {assembled}"


def test_nothing_in_the_scheduler_outside_its_chokepoint_holds_the_raw_driver() -> None:
    offenders: list[str] = []
    for module in SCHEDULER_MODULES:
        if module.dotted_name in SCHEDULER_MODULES_ALLOWED_THE_RAW_CLIENT:
            continue
        for node in ast.walk(module.tree):
            reference = _raw_client_reference(node)
            if reference is not None:
                line, name = reference
                offenders.append(f"{module}:{line} references {name}")
    assert not offenders, (
        f"the raw ClickHouse client escaped the scheduler's chokepoint: {offenders}"
    )
