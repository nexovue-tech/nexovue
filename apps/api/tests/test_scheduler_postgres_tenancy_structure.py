"""Every scheduler Postgres statement on a tenant's table binds the tenant (F6 design,
revision 2.1, item 8; *Security, tenancy, and privacy*).

`test_postgres_tenancy_structure.py` holds the API's statements to this, but it scans
`apps/api/src` alone, and only statements built with SQLAlchemy's `select`. The
scheduler writes its statements as `text()`, so until this suite they bound `org_id`
by review only. `test_tenant_chokepoint_structure.py` does the same job for its
ClickHouse reads, and lives here for the same reason this does: the models say which
tables carry a tenant.

Three things are held:

1. a statement that reads, changes or deletes a tenant's rows binds `org_id` to a
   parameter, and not one a caller can switch off with `:org_id IS NULL OR`;
2. a statement that inserts a tenant's row names `org_id` among its columns;
3. a table the scheduler names without an `org_id` column is install-wide by a
   recorded decision, not by forgetting.

The exceptions are named, each with its reason: the statements that must see every
tenant at once, and the install-wide tables. The scheduler's statements and the
shared platform's, which the scheduler runs too, are both read.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from nexovue_api.data.postgres import models
from nexovue_api.data.postgres.base import Base

_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
SCHEDULER_SOURCE: Final = _REPOSITORY_ROOT / "apps" / "scheduler" / "src" / "nexovue_scheduler"
PLATFORM_SOURCE: Final = _REPOSITORY_ROOT / "packages" / "py-platform" / "src" / "nexovue_platform"
POSTGRES_MIGRATIONS: Final = _REPOSITORY_ROOT / "apps" / "api" / "migrations" / "versions"
TENANT_COLUMN: Final = "org_id"


@dataclass(frozen=True, slots=True)
class Decision:
    """A name the rules exempt, and why it is safe."""

    name: str
    reason: str


# Views carry no model. Each is listed with what it exposes, so it is guarded as the
# tables it reads are.
TENANT_VIEWS: Final = (
    Decision(
        "project_retention",
        "one row per live project, with its `org_id`: a tenant's rows like `projects`",
    ),
)

# Tables without `org_id` that the scheduler reads or writes. Each describes nexovue
# itself or the tenant as a whole, never one tenant's data inside it.
INSTALL_WIDE_TABLES: Final = (
    Decision(
        "intake_refusals",
        "the minutes nexovue's receiver could not hand to Kafka, per receiver. It names no "
        "customer, and every project's detection waits on it alike (design, 'When a minute "
        "is settled')",
    ),
    Decision(
        "detection_events",
        "nexovue's own intake failures, for its operator: a guard that engaged or a hold "
        "that reached its limit. No customer's data, only that all data was delayed",
    ),
    Decision(
        "detection_sweep",
        "one row: when the minute sweep last ran and the minute it handed out, the "
        "operator's 'detection stalled' clock (design 2.1, As built Step 7)",
    ),
    Decision(
        "alert_evaluation_status",
        "one row per sweep kind: when the sweep last finished, for the Alerts screen. It "
        "is the scheduler's own heartbeat, the same for every tenant",
    ),
    Decision(
        "usage_ingest_offsets",
        "how far the usage meter has counted each Kafka partition; a partition holds "
        "every tenant's records, so no tenant owns its offset",
    ),
    Decision(
        "organizations",
        "the tenant itself, keyed by `id`: the statements that read it name the "
        "organization by that key, or are the erasure finding deleted ones (ADR-014)",
    ),
)


@dataclass(frozen=True, slots=True)
class CrossTenantStatement:
    """A statement that must see every tenant at once, by module and by the constant
    or function that holds it, and why it may."""

    module: str
    owner: str
    reason: str


# Per statement, never per module or per table: a second statement reading the same
# table across tenants is a new decision, and has to be made here by name. Every row
# these return carries its own `org_id`, and what is written for it is scoped by that.
_SWEEP: Final = "a sweep over every organization's rows, as the scheduler's work is"


def _across(module: str, owner: str, reason: str) -> CrossTenantStatement:
    return CrossTenantStatement(f"nexovue_scheduler.{module}", owner, reason)


CROSS_TENANT_STATEMENTS: Final = (
    # Detection (F6).
    _across(
        "detection.projects",
        "_ACTIVE",
        "the coordinator's claim: which projects of every organization detection judges "
        "this minute, so each is handed its own job (design, 'Where it runs'). `:org_id` "
        "narrows it to one organization for a test, and is null for the sweep",
    ),
    _across(
        "detection.projects",
        "_NO_LONGER_EXPECTED",
        "the stop sweep's clock (D34) for every project detection works on, by `_ACTIVE`'s claim",
    ),
    _across(
        "detection.projects",
        "_NEWLY_EXPECTED",
        "the stop sweep's clock (D34) for every project detection works on, by `_ACTIVE`'s "
        "claim; each row it writes carries the project's own `org_id`",
    ),
    _across(
        "detection.notify",
        "_NEWLY_STOPPED",
        f"{_SWEEP}: whose detection stopped, so its owners are told (D24)",
    ),
    _across(
        "detection.holds",
        "_VOTES",
        "the install-wide guard counts organizations losing traffic together, which no one "
        "organization's rows can show (design 2.1, item 1). It reads only when each lost it",
    ),
    _across(
        "detection.store",
        "_PROJECT_BINDING",
        "what establishes the tenant: the project job is given a project id and reads its "
        "organization from the project's own row, never from the caller (design 2.1, m6)",
    ),
    # The job queue and its metrics.
    _across("jobs.queue", "_CLAIM", "a worker claims due jobs of every organization"),
    _across("jobs.queue", "_FINISH", "a claimed job, by its id and this worker's lease"),
    _across("jobs.queue", "_RETRY", "a claimed job, by its id and this worker's lease"),
    _across("jobs.queue", "_SWEEP_SPENT", f"{_SWEEP}: expired and spent jobs"),
    _across("metrics", "_JOBS_READY", "an install-wide gauge: jobs ready per queue"),
    _across("metrics", "_ALERTS_WAITING", "an install-wide gauge: notifications waiting"),
    _across(
        "metrics",
        "_DETECTION_PROJECTS",
        "an install-wide gauge: projects whose detection is behind",
    ),
    # Alerting (F21).
    _across("alerts.outbox", "_CLAIMABLE_CHANNELS", f"{_SWEEP}: the delivery claim"),
    _across("alerts.outbox", "_PRUNE_ATTEMPTS", f"{_SWEEP}: delivery attempts past keeping"),
    _across("alerts.health", "_TO_REPORT", f"{_SWEEP}: channels newly failing"),
    _across("alerts.incidents", "_DUE_REPEATS", f"{_SWEEP}: incidents due a repeat"),
    _across("alerts.rules", "_RULES_OF_KINDS", f"{_SWEEP}: the rules a sweep evaluates"),
    # Synthetic checks and pollers: beat schedules every organization's.
    _across("checks", "_ENABLED_CHECKS", f"{_SWEEP}: every enabled check, for beat"),
    _across("checks", "_VARIABLES", "the variables of the projects `_ENABLED_CHECKS` returned"),
    _across("checks", "_SECRETS", "the secrets of the projects `_ENABLED_CHECKS` returned"),
    _across("checks", "_FILES", "the files of the checks `_ENABLED_CHECKS` returned"),
    _across("checks", "_WINDOWS", "the maintenance of the projects `_ENABLED_CHECKS` returned"),
    _across("probes.tasks", "_TAKE_UNATTACHED", f"{_SWEEP}: uploads never attached to a check"),
    _across("pollers.integrations", "_ACTIVE_INTEGRATIONS", f"{_SWEEP}: every active connector"),
    _across(
        "pollers.integrations",
        "_RECORD_SUCCESS",
        "a connector by its own id, from `_ACTIVE_INTEGRATIONS`; the poll that reports it "
        "carries no organization to bind",
    ),
    _across(
        "pollers.integrations",
        "_RECORD_FAILURE",
        "a connector by its own id, from `_ACTIVE_INTEGRATIONS`; the poll that reports it "
        "carries no organization to bind",
    ),
    # Billing and erasure.
    _across("billing", "_END_LAPSED_GRACE", f"{_SWEEP}: subscriptions whose grace ended"),
    _across(
        "erasure.owed",
        "_OWED",
        "ADR-014: the organizations deleted and not yet erased, found by their certificate, "
        "an audit row with no organization",
    ),
    _across("erasure.owed", "_OVERDUE", "ADR-014: how many erasures are overdue, install-wide"),
)


def _relations_with_a_tenant() -> frozenset[str]:
    tables = {table.name for table in Base.metadata.tables.values() if TENANT_COLUMN in table.c}
    return frozenset(tables | {view.name for view in TENANT_VIEWS})


def _relations_without_a_tenant() -> frozenset[str]:
    return frozenset(
        table.name for table in Base.metadata.tables.values() if TENANT_COLUMN not in table.c
    )


TENANT_RELATIONS: Final = _relations_with_a_tenant()
TENANTLESS_RELATIONS: Final = _relations_without_a_tenant()
POSTGRES_RELATIONS: Final = TENANT_RELATIONS | TENANTLESS_RELATIONS

_RELATION_NAMES: Final = "|".join(sorted(POSTGRES_RELATIONS, key=len, reverse=True))
# Upper case only: the repository writes SQL keywords in capitals, and prose that
# says "from findings" must not read as a statement.
_READS_OR_CHANGES: Final = re.compile(
    rf"\b(?:FROM|JOIN|UPDATE|DELETE\s+FROM)\s+({_RELATION_NAMES})\b(?!\s*\()"
)
_INSERTS: Final = re.compile(rf"\bINSERT\s+INTO\s+({_RELATION_NAMES})\b")
# A whole statement. A fragment such as `NOT EXISTS (SELECT … WHERE inner.org_id =
# outer.org_id)` is held by the statement it is embedded in, which is read whole.
_STARTS_A_STATEMENT: Final = re.compile(r"\s*\(?\s*(?:SELECT|INSERT|UPDATE|DELETE|WITH)\b")
# `org_id = :p`, `x.org_id = CAST(:p AS uuid)`, `org_id = ANY(:p)`, `org_id IN (:p)`.
_BINDS_THE_TENANT: Final = re.compile(
    r"\b(?:\w+\.)?org_id\s*(?:=\s*ANY\s*\(|=|IN\s*\()\s*(?:CAST\s*\(\s*)?:(\w+)"
)
# `:p IS NULL OR`, or `CAST(:p AS uuid) IS NULL OR`: a filter the caller can switch off.
_OPTIONAL_PARAMETER: Final = re.compile(
    r"(?:CAST\s*\(\s*)?:(\w+)(?:\s+AS\s+\w+(?:\[\])?\s*\))?\s+IS\s+NULL\s+OR\b"
)


@dataclass(frozen=True, slots=True)
class Statement:
    """A string in the source that names a Postgres table."""

    module: str
    owner: str
    line: int
    text: str

    def __str__(self) -> str:
        return f"{self.module}:{self.line} ({self.owner})"

    @property
    def reads_or_changes(self) -> frozenset[str]:
        return frozenset(_READS_OR_CHANGES.findall(self.text)) & TENANT_RELATIONS

    @property
    def inserts(self) -> frozenset[str]:
        return frozenset(_INSERTS.findall(self.text)) & TENANT_RELATIONS

    @property
    def names(self) -> frozenset[str]:
        return frozenset(_READS_OR_CHANGES.findall(self.text)) | frozenset(
            _INSERTS.findall(self.text)
        )


def binds_the_tenant(text: str) -> bool:
    """Whether a statement binds `org_id` to a parameter no caller can switch off."""
    optional = set(_OPTIONAL_PARAMETER.findall(text))
    return any(parameter not in optional for parameter in _BINDS_THE_TENANT.findall(text))


def violations(statement: Statement) -> list[str]:
    """What a statement does to a tenant's rows without saying which tenant."""
    found = []
    if statement.reads_or_changes and not binds_the_tenant(statement.text):
        found.append(
            f"{statement} reads or changes {sorted(statement.reads_or_changes)} "
            f"without binding {TENANT_COLUMN}"
        )
    if statement.inserts and TENANT_COLUMN not in statement.text:
        found.append(
            f"{statement} inserts into {sorted(statement.inserts)} without {TENANT_COLUMN}"
        )
    return found


# ------------------------------------------------------------------ the source


@dataclass(frozen=True, slots=True)
class SourceModule:
    dotted_name: str
    tree: ast.Module


def _modules_under(source: Path, package: str) -> list[SourceModule]:
    modules = []
    for path in sorted(source.rglob("*.py")):
        relative = path.relative_to(source).with_suffix("")
        parts = [package, *(part for part in relative.parts if part != "__init__")]
        modules.append(
            SourceModule(".".join(parts), ast.parse(path.read_text(encoding="utf-8"), str(path)))
        )
    return modules


SCHEDULER_MODULES: Final = _modules_under(SCHEDULER_SOURCE, "nexovue_scheduler") + _modules_under(
    PLATFORM_SOURCE, "nexovue_platform"
)


def _docstrings(tree: ast.AST) -> set[int]:
    """Docstrings are prose: they name tables they are about, and are no statement."""
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, holders) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def _module_strings(tree: ast.Module) -> dict[str, ast.expr]:
    """Module-level names bound to a string, so an f-string that embeds one is read whole."""
    found: dict[str, ast.expr] = {}
    for statement in tree.body:
        value = statement.value if isinstance(statement, ast.Assign | ast.AnnAssign) else None
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
            if isinstance(statement, ast.AnnAssign)
            else []
        )
        if isinstance(value, ast.Constant | ast.JoinedStr):
            found.update({target.id: value for target in targets if isinstance(target, ast.Name)})
    return found


def _text(node: ast.expr, strings: dict[str, ast.expr], depth: int = 0) -> str:
    """A literal's words, with any module string it embeds read in its place."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    if not isinstance(node, ast.JoinedStr) or depth > len(strings):
        return ""
    parts = []
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            parts.append(part.value)
        elif (
            isinstance(part, ast.FormattedValue)
            and isinstance(part.value, ast.Name)
            and part.value.id in strings
        ):
            parts.append(_text(strings[part.value.id], strings, depth + 1))
    return "".join(parts)


def _owners(tree: ast.Module) -> dict[int, str]:
    """Each node's owner: its innermost function, or the module constant it is bound to."""
    owner: dict[int, str] = {}

    def claim(node: ast.AST, name: str) -> None:
        for inner in ast.walk(node):
            owner[id(inner)] = name
        for inner in ast.walk(node):
            if isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef) and inner is not node:
                for nested in ast.walk(inner):
                    owner[id(nested)] = inner.name

    for statement in tree.body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            claim(statement, statement.name)
        elif isinstance(statement, ast.Assign) and isinstance(statement.targets[0], ast.Name):
            claim(statement, statement.targets[0].id)
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            claim(statement, statement.target.id)
    return owner


def statements_in(modules: Iterable[SourceModule]) -> list[Statement]:
    found = []
    for module in modules:
        prose, strings, owners = (
            _docstrings(module.tree),
            _module_strings(module.tree),
            _owners(module.tree),
        )
        # An f-string's fixed parts are read with it, never as statements of their own.
        parts = {
            id(part)
            for node in ast.walk(module.tree)
            if isinstance(node, ast.JoinedStr)
            for part in node.values
        }
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Constant | ast.JoinedStr) or id(node) in prose | parts:
                continue
            words = _text(node, strings)
            if _STARTS_A_STATEMENT.match(words) and (
                _READS_OR_CHANGES.search(words) or _INSERTS.search(words)
            ):
                found.append(
                    Statement(
                        module.dotted_name, owners.get(id(node), "<module>"), node.lineno, words
                    )
                )
    return found


SCHEDULER_STATEMENTS: Final = statements_in(SCHEDULER_MODULES)


def _exempt(statement: Statement) -> bool:
    return any(
        (each.module, each.owner) == (statement.module, statement.owner)
        for each in CROSS_TENANT_STATEMENTS
    )


# ------------------------------------------------------------------ the checks


def test_the_source_this_check_walks_is_the_one_that_ships() -> None:
    """A check that walks nothing passes everything."""
    assert SCHEDULER_SOURCE.is_dir(), f"no scheduler source at {SCHEDULER_SOURCE}"
    assert PLATFORM_SOURCE.is_dir(), f"no platform source at {PLATFORM_SOURCE}"
    modules = {statement.module for statement in SCHEDULER_STATEMENTS}
    assert "nexovue_scheduler.detection.project_job" in modules
    assert "nexovue_platform.detection" in modules


def test_the_tables_this_check_guards_are_the_ones_that_carry_a_tenant() -> None:
    """An empty set would pass every statement below."""
    # Through the models module, which is what registers every table on `Base`.
    assert models.Finding.__tablename__ in TENANT_RELATIONS
    assert {"findings", "detection_state", "detection_project_runs", "checks"} <= TENANT_RELATIONS
    assert {"intake_refusals", "detection_events"} <= TENANTLESS_RELATIONS


def test_no_scheduler_statement_on_a_tenant_s_table_omits_the_tenant() -> None:
    found = [
        problem
        for statement in SCHEDULER_STATEMENTS
        if not _exempt(statement)
        for problem in violations(statement)
    ]
    assert not found, (
        "these scheduler statements reach another tenant's rows. Bind org_id, or name the "
        f"statement in CROSS_TENANT_STATEMENTS with the reason it must see every tenant: {found}"
    )


def test_every_table_without_a_tenant_the_scheduler_names_is_install_wide_on_purpose() -> None:
    """A table the scheduler names with no `org_id` is either nexovue's own, by a
    decision recorded here, or a tenant's table that forgot its column."""
    declared = {each.name for each in INSTALL_WIDE_TABLES}
    undeclared = sorted(
        {
            f"{name} ({statement})"
            for statement in SCHEDULER_STATEMENTS
            for name in statement.names & TENANTLESS_RELATIONS
            if name not in declared
        }
    )
    assert not undeclared, (
        "the scheduler names tables with no org_id that are not declared install-wide: "
        f"{undeclared}"
    )


def test_every_install_wide_table_is_a_real_table_without_a_tenant_and_gives_its_reason() -> None:
    """A renamed table, or one that gained `org_id`, must break its exemption rather than
    leave it guarding nothing."""
    for each in INSTALL_WIDE_TABLES:
        assert each.name in TENANTLESS_RELATIONS, f"{each.name} is not a table without org_id"
        assert each.reason.strip(), f"{each.name} is install-wide without a recorded reason"


def test_every_install_wide_table_is_one_the_scheduler_names() -> None:
    """An exemption nothing uses would cover the next statement that forgets."""
    named = {name for statement in SCHEDULER_STATEMENTS for name in statement.names}
    unused = sorted(each.name for each in INSTALL_WIDE_TABLES if each.name not in named)
    assert not unused, f"no scheduler statement names these install-wide tables: {unused}"


def test_every_view_is_created_by_a_migration() -> None:
    created = " ".join(
        path.read_text(encoding="utf-8") for path in POSTGRES_MIGRATIONS.glob("*.py")
    )
    for view in TENANT_VIEWS:
        assert re.search(rf"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+{view.name}\b", created), (
            f"no migration creates the view {view.name}"
        )


def test_every_cross_tenant_exemption_still_names_an_unbound_statement() -> None:
    """An exemption whose statement moved, or now binds the tenant, must be removed,
    or it would silently cover the next statement written under its name."""
    for each in CROSS_TENANT_STATEMENTS:
        assert each.reason.strip(), f"{each.module}.{each.owner} is exempt without a reason"
        held = [
            statement
            for statement in SCHEDULER_STATEMENTS
            if (statement.module, statement.owner) == (each.module, each.owner)
        ]
        assert held, f"{each.module}.{each.owner} holds no statement any more"
        assert any(violations(statement) for statement in held), (
            f"{each.module}.{each.owner} binds the tenant now; remove its exemption"
        )


# ------------------------------------------------------------ the rule itself


def _statement(text: str) -> Statement:
    return Statement("nexovue_scheduler.example", "_EXAMPLE", 1, text)


def test_the_rule_refuses_a_read_with_no_tenant() -> None:
    """The negative control: what this suite exists to catch."""
    unbound = _statement("SELECT id FROM findings WHERE project_id = :project_id")
    assert violations(unbound)


def test_the_rule_refuses_a_tenant_filter_a_caller_can_switch_off() -> None:
    optional = _statement(
        "SELECT id FROM findings WHERE (CAST(:org_id AS uuid) IS NULL "
        "OR findings.org_id = CAST(:org_id AS uuid))"
    )
    assert violations(optional)


def test_the_rule_refuses_a_join_on_org_id_that_binds_no_tenant() -> None:
    joined = _statement(
        "SELECT 1 FROM findings JOIN detection_state AS s ON s.org_id = findings.org_id"
    )
    assert violations(joined)


def test_the_rule_refuses_an_insert_that_copies_rows_from_every_tenant() -> None:
    copied = _statement(
        "INSERT INTO finding_incidents (org_id, finding_id, incident_id) "
        "SELECT org_id, :finding_id, id FROM incidents"
    )
    assert violations(copied)


def test_the_rule_accepts_a_bound_read_and_an_insert_naming_its_tenant() -> None:
    for text in (
        "SELECT id FROM findings WHERE org_id = :org_id AND project_id = :project_id",
        "UPDATE detection_state SET signals = :s WHERE state.org_id = CAST(:org_id AS uuid)",
        "DELETE FROM findings WHERE org_id = ANY(:organizations)",
        "INSERT INTO findings (org_id, project_id) VALUES (:org_id, :project_id)",
    ):
        assert not violations(_statement(text)), text


def test_the_rule_ignores_prose_and_install_wide_tables() -> None:
    assert not violations(_statement("judged from findings the person closed"))
    assert not violations(_statement("SELECT minute FROM intake_refusals"))
