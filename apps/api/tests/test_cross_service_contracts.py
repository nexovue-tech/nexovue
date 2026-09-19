"""Names that cross a service boundary are restated, so they are asserted equal.

Three constants and a table list are written down twice on purpose. The API and
the scheduler deploy separately, and `pollers/integrations.py` gives the reason
an import would be worse: *"a scheduler that cannot start because a
control-plane package moved is a scheduler running nobody's polls."* The Go
worker cannot import Python at all.

`test_synthetics.py` states what makes that safe rather than merely deliberate:

    Restating a name is how two services drift apart; this is the assertion that
    turns a rename into a failing test instead of an empty chart.

Door 2 and door 4b's metric names already have that assertion. This file adds
the two boundaries that did not: the integration auth mode, restated on
2026-09-06 with a comment saying a change must change both and nothing enforcing
it; and the ClickHouse table names, where the producer is Go and the failure is
the same shape with a longer fuse.

**Empty telemetry is the failure mode**, which is why this matters more than it
looks: a renamed metric does not raise, it returns no rows, and a screen that
was working goes blank with nothing in any log.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final

import pytest

from nexovue_api import auth

API_SOURCE: Final = Path(auth.__file__).resolve().parents[1]
REPOSITORY_ROOT: Final = API_SOURCE.parents[3]
CLICKHOUSE_MIGRATIONS: Final = REPOSITORY_ROOT / "deploy" / "clickhouse" / "migrations"

_CREATES: Final = re.compile(
    r"CREATE\s+(?:MATERIALIZED\s+VIEW|TABLE)\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.IGNORECASE
)
# `FROM <table>` inside a SQL string. Case-sensitive and searched only within
# string constants that contain SELECT, because a case-insensitive sweep of the
# whole file matches `from __future__ import`, `from dataclasses import` and the
# word "from" in a docstring — which it did on the first run.
_READS: Final = re.compile(r"\bFROM\s+([a-z_][a-z0-9_]*)")


def _tables_the_migrations_create() -> set[str]:
    return {
        match.group(1)
        for migration in CLICKHOUSE_MIGRATIONS.glob("*.up.sql")
        for match in _CREATES.finditer(migration.read_text(encoding="utf-8"))
    }


def _query_modules() -> list[Path]:
    return sorted(API_SOURCE.glob("*/queries.py"))


def _literal_text(node: ast.expr) -> str:
    """The fixed text of a string node, f-strings included.

    **f-strings are the reason this is not one line.** Most query constants here
    are f-strings that interpolate a column expression or a shared predicate, and
    an `ast.Constant` check misses them entirely — it matches only the fragment
    before the first `{...}`, which held SELECT but not the FROM clause, so this
    check silently examined less than it claimed on its first run.

    The interpolated parts are dropped rather than resolved: a table name is
    never one, and a `{tenant_id:String}` placeholder is ClickHouse's parameter
    syntax rather than Python's.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    if isinstance(node, ast.JoinedStr):
        return "".join(_literal_text(part) for part in node.values)
    return ""


def _sql_strings(module: Path) -> list[str]:
    """Every string in the module that is a SQL statement.

    Parsed rather than grepped: a module is Python, and only its string literals
    are SQL. Anything holding SELECT is treated as a statement, which is coarse
    and errs toward checking more.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    literals = (node for node in ast.walk(tree) if isinstance(node, ast.Constant | ast.JoinedStr))
    texts = (_literal_text(node) for node in literals)
    return [text for text in texts if "SELECT" in text]


# ------------------------------------------------------- API <-> scheduler


def test_the_integration_auth_mode_is_the_one_the_scheduler_accepts() -> None:
    """The gap this file was written for.

    `integrations/service.py` refuses a connector whose `auth` is not the mode
    the poller can use, and restates that mode rather than importing it. Without
    this, the API could go on accepting a mode the scheduler rejects — a
    connector created successfully and then failing on every poll, which is
    exactly the delayed failure the refusal exists to prevent.
    """
    from nexovue_api.integrations.service import SUPPORTED_POSTGRES_AUTH
    from nexovue_scheduler.pollers.postgres_connect import AUTH_RDS_IAM

    assert SUPPORTED_POSTGRES_AUTH == AUTH_RDS_IAM


def test_the_integration_kind_is_one_the_scheduler_polls() -> None:
    """A kind the scheduler has no poller for is a connector nothing runs."""
    from nexovue_api.integrations.service import KIND_POSTGRES as API_KIND_POSTGRES
    from nexovue_scheduler.pollers.integrations import KIND_CLOUDWATCH, KIND_POSTGRES

    assert API_KIND_POSTGRES == KIND_POSTGRES
    # Named so a reader sees both kinds are accounted for, not only the one
    # this module happens to validate.
    assert KIND_CLOUDWATCH == "cloudwatch"


# ------------------------------------------------------- scheduler <-> browser SDK

# The array literal in `pagecontext.ts`, read as source because TypeScript is the
# one producer here that no Python test can import.
_TYPESCRIPT_LIST: Final = re.compile(
    r"export const SENSITIVE_QUERY_PARAMETERS = \[(?P<body>.*?)\]", re.DOTALL
)
_QUOTED: Final = re.compile(r'"([^"]+)"')


def test_the_sensitive_query_parameters_are_the_same_list_in_both_languages() -> None:
    """One list lives in two languages, and neither can import the other.

    The failure it prevents is quiet in the way this file's header describes: a
    parameter added on the SDK side and not on the probe's is a signature that
    keeps being recorded, with nothing anywhere reporting a difference.
    """
    from nexovue_scheduler.probes.urls import SENSITIVE_QUERY_PARAMETERS

    source = (REPOSITORY_ROOT / "packages" / "browser-sdk" / "src" / "pagecontext.ts").read_text(
        encoding="utf-8"
    )
    declaration = _TYPESCRIPT_LIST.search(source)
    assert declaration, "no SENSITIVE_QUERY_PARAMETERS array in pagecontext.ts"

    assert _QUOTED.findall(declaration.group("body")) == list(SENSITIVE_QUERY_PARAMETERS)


# ------------------------------------------------------- API <-> the Go worker's config grammar

WORKER_PIPELINE: Final = REPOSITORY_ROOT / "apps" / "workers" / "internal" / "pipeline"
MASK_RULES_GO: Final = WORKER_PIPELINE / "mask" / "rules.go"
SAMPLE_POLICY_GO: Final = WORKER_PIPELINE / "sample" / "policy.go"

# The `json:"..."` tags on a Go struct, which are the document keys it accepts.
_STRUCT: Final = re.compile(r"type (?P<name>\w+) struct \{(?P<body>.*?)\n\}", re.DOTALL)
_JSON_TAG: Final = re.compile(r'json:"(?P<key>[^",]+)"')

# `RuleCredentialKeys = "credential_keys"` — the family names, read from the
# constants rather than from `DefaultRuleNames`, because the switch in
# `setFamily` is keyed on these and it is that switch which decides what is
# accepted.
_RULE_CONSTANT: Final = re.compile(r'^\s*Rule\w+\s*=\s*"(?P<name>[a-z_]+)"', re.MULTILINE)


def _document_keys(source: Path, struct: str) -> list[str]:
    """The JSON keys a Go struct binds, in declaration order."""
    for match in _STRUCT.finditer(source.read_text(encoding="utf-8")):
        if match.group("name") == struct:
            return _JSON_TAG.findall(match.group("body"))
    raise AssertionError(f"no `type {struct} struct` in {source.name}")


def test_the_masking_document_keys_are_the_ones_the_worker_binds() -> None:
    """One grammar lives in two languages, and neither can import the other.

    The API refuses a document the worker would reject, which is the whole point
    of validating on write: `refresh.go` falls back to defaults that name no
    custom keys on a cold start, so a bad document stored today is a project that
    silently stops masking at the next restart.

    A key accepted here and unknown there is that hole with a form in front of
    it — the document stores, the worker ignores the line, and the customer
    believes an attribute is being masked.
    """
    from nexovue_api.projects.config import MASKING_DOCUMENT_KEYS

    assert set(_document_keys(MASK_RULES_GO, "projectConfig")) == set(MASKING_DOCUMENT_KEYS)


def test_the_masking_rule_names_are_the_ones_the_worker_switches_on() -> None:
    """`setFamily` already makes an unknown name an error, so the API's list must
    be neither wider — a rule it accepts and the worker refuses, which stores a
    document that stops the project masking at all — nor narrower, which would
    refuse a rule the product ships."""
    from nexovue_api.projects.config import MASKING_RULE_NAMES

    names = _RULE_CONSTANT.findall(MASK_RULES_GO.read_text(encoding="utf-8"))
    assert names, "no Rule* constants found in mask/rules.go"
    assert set(names) == set(MASKING_RULE_NAMES)


def test_the_sampling_document_keys_are_the_ones_the_worker_binds() -> None:
    """Sampling's failure is quieter than masking's rather than milder: an
    out-of-range percentage makes `PolicyFromJSON` return an error, the refresher
    logs it and keeps the deployment default, and the project stores a tenth of
    what it asked to store with nothing on any screen saying so."""
    from nexovue_api.projects.config import SAMPLING_DOCUMENT_KEYS

    assert set(_document_keys(SAMPLE_POLICY_GO, "projectConfig")) == set(SAMPLING_DOCUMENT_KEYS)


def test_the_worker_sources_are_where_this_check_thinks_they_are() -> None:
    """A moved file must break this, not silently check nothing — the same guard
    the ClickHouse migrations carry below."""
    assert MASK_RULES_GO.is_file(), f"no masking grammar at {MASK_RULES_GO}"
    assert SAMPLE_POLICY_GO.is_file(), f"no sampling grammar at {SAMPLE_POLICY_GO}"


# ------------------------------------------------------- API <-> ClickHouse


@pytest.mark.parametrize("module", _query_modules(), ids=lambda p: p.parent.name)
def test_every_table_a_query_reads_is_one_the_migrations_create(module: Path) -> None:
    """The Go worker writes these tables and Python reads them.

    Neither language can import the other, so the migrations are the only shared
    definition. A table renamed there and followed in the worker but not here
    produces a query against nothing — and ClickHouse's error never reaches a
    screen, which shows an empty window instead.
    """
    created = _tables_the_migrations_create()

    read = {
        match.group(1).lower()
        for statement in _sql_strings(module)
        for match in _READS.finditer(statement)
    }
    # `FROM input(...)` is ClickHouse's insert-select form, not a table, and
    # `system.*` is the server's own catalogue rather than ours.
    read -= {"input", "system"}

    missing = sorted(name for name in read if name not in created)
    assert not missing, (
        f"{module.parent.name}/queries.py reads {missing}, which the ClickHouse migrations "
        f"do not create. A renamed table returns no rows rather than an error, so the screen "
        f"empties silently."
    )


# ------------------------------------------------------- the shared package


def test_the_shared_package_reaches_neither_service_nor_a_database() -> None:
    """`py-platform` depends on both services, so it must depend on neither.

    Two rules, and the second is the one this test exists for. Importing
    `nexovue_api` or `nexovue_scheduler` from here would make the dependency
    circular and reintroduce exactly what `pollers/integrations.py` avoids — a
    scheduler that cannot start because a control-plane package moved.

    And it must hold no database access, because moving these modules out of
    `apps/api/src` moved them out of `test_postgres_tenancy_structure.py`'s
    scope: that check parametrizes over every function under the API's source
    tree, and fourteen parametrized cases disappeared with them. None of them
    queried anything, but nothing would notice if a future one did. This is the
    boundary that replaces it.
    """
    platform_source = REPOSITORY_ROOT / "packages" / "py-platform" / "src" / "nexovue_platform"
    assert platform_source.is_dir(), f"no shared package at {platform_source}"

    forbidden = ("nexovue_api", "nexovue_scheduler", "sqlalchemy", "asyncpg", "clickhouse")
    offending: list[str] = []
    for module in platform_source.rglob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offending += [
                f"{module.name}: {name}"
                for name in names
                if any(name.startswith(bad) for bad in forbidden)
            ]

    assert not offending, (
        f"the shared package imports {offending}. It is depended on by both services, so it may "
        f"depend on neither — and it holds no database access, which is what keeps it outside "
        f"the tenancy guard safely."
    )


def test_the_migrations_are_where_this_check_thinks_they_are() -> None:
    """A moved directory must break this, not silently check nothing.

    The same guard `test_tenant_chokepoint_structure.py` carries, for the same
    reason: a glob over a path that does not exist returns an empty set, and
    every assertion above it then passes.
    """
    assert CLICKHOUSE_MIGRATIONS.is_dir(), f"no ClickHouse migrations at {CLICKHOUSE_MIGRATIONS}"
    assert _tables_the_migrations_create(), "the migrations directory yielded no tables"
    assert _query_modules(), "no queries.py modules were found to check"


# ------------------------------------------------------- API -> the probe task (ADR-011)


def test_run_now_sends_exactly_the_arguments_the_probe_task_accepts() -> None:
    """A renamed keyword would fail Run now on the worker, and the console would wait
    for a result that never comes."""
    import inspect
    import os

    for name, value in {
        "ENV": "development",
        "POSTGRES_DSN": "postgresql://u:p@localhost:5432/d",
        "REDIS_URL": "redis://localhost:6379/1",
        "KAFKA_BROKERS": "localhost:9092",
    }.items():
        os.environ.setdefault(name, value)

    from nexovue_platform.checks.task import RUN_CHECK_TASK, CheckDefinition, run_now_arguments

    from nexovue_scheduler.probes import tasks

    definition = CheckDefinition(
        check_id=None,
        org_id="o",
        project_id="p",
        name="n",
        type="tcp",
        config={"type": "tcp", "host": "example.com", "port": 443},
        interval_seconds=60,
        locations=["local"],
    )
    sent = set(run_now_arguments(definition, "local", "request"))
    accepted = {
        parameter.name
        for parameter in inspect.signature(tasks.run_check.run).parameters.values()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    }

    assert tasks.run_check.name == RUN_CHECK_TASK
    assert sent <= accepted
    assert {
        parameter.name
        for parameter in inspect.signature(tasks.run_check.run).parameters.values()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
        and parameter.default is inspect.Parameter.empty
    } <= sent


def test_send_test_sends_exactly_the_arguments_the_deliveries_task_accepts() -> None:
    """F21. The same contract as Run now's: a renamed keyword would leave the console
    waiting for an outcome the worker never writes."""
    import inspect
    import os

    for name, value in {
        "ENV": "development",
        "POSTGRES_DSN": "postgresql://u:p@localhost:5432/d",
        "REDIS_URL": "redis://localhost:6379/1",
        "KAFKA_BROKERS": "localhost:9092",
    }.items():
        os.environ.setdefault(name, value)

    from nexovue_platform.alerts.tasks import SEND_TEST_TASK, send_test_arguments

    from nexovue_scheduler.alerts import tasks

    sent = set(send_test_arguments(org_id="o", channel_id="c", request_id="r"))
    keyword_only = [
        parameter
        for parameter in inspect.signature(tasks.send_test.run).parameters.values()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    ]

    assert tasks.send_test.name == SEND_TEST_TASK
    assert sent == {parameter.name for parameter in keyword_only}
