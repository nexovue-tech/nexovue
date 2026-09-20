"""Every Postgres query against a tenant-scoped table binds the tenant.

`test_tenant_chokepoint_structure.py` does this for ClickHouse, and derives its
table list from the migrations so a table added next month is guarded the day it
is created. Postgres had no equivalent: `checks`, `integrations` and
`deploy_events` carry `org_id` and are filtered by hand, with nothing to catch
the next table that forgets.

The model list comes from the models module rather than from a list kept here,
for the same reason — a list a human maintains alongside a schema will drift.

This is structural, not behavioural. It cannot prove a filter is *correct*; it
proves a query against a tenant-scoped model does not simply omit the tenant,
which is the mistake that actually happens.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

from nexovue_api.data.postgres import models

API_SOURCE: Final = Path(models.__file__).resolve().parents[3]
TENANT_COLUMN: Final = "org_id"

# Reads that are deliberately not tenant-scoped, each with the reason it is safe.
# A name here is a decision, so adding one should be uncomfortable.
EXEMPT: Final = {
    # The webhook caller is unauthenticated and has proved nothing yet. The
    # project id in the path is all it has said and the signature is what proves
    # it; the organization is then read off the secret that matched, never off
    # the request. Scoping this by org_id would require trusting the caller for
    # the very value the signature exists to establish.
    "live_webhook_secrets",
    # Resolves an ingest key to its tenant. It is what *establishes* the tenant,
    # so it cannot be scoped by one.
    "find_live_key_by_prefix",
    # Asks whether this project has already stored this delivery. Reached only
    # after the signature proved the caller holds the project's secret, so the
    # project id is established rather than claimed — and the organization is
    # derived from that same secret moments earlier.
    "delivery_already_recorded",
    # Signup uniqueness, both of them. An address and a key prefix are unique
    # across the whole system, not within a tenant, and scoping either would let
    # two organizations register the same one.
    "email_is_taken",
    "key_prefix_is_taken",
    # The public browser key is unique system-wide for the same reason a key
    # prefix is: the receiver resolves a browser request by this value alone, so
    # two tenants holding one would make that lookup ambiguous.
    "site_key_is_taken",
    # Resolves a processor's subscription reference to its tenant, exactly as
    # `find_live_key_by_prefix` resolves an ingest key. It is what *establishes*
    # the organization, so it cannot be scoped by one — and asking the webhook
    # to name the organization is the arrangement deliberately removed.
    "subscription_by_processor_ref",
    # A heartbeat ping resolves its check by the token's hash alone, as an ingest
    # key resolves by its prefix: the token is what *establishes* the tenant, and
    # the caller is a cron job with no session to say which one.
    "record_ping_by_token_hash",
    # A set-password link (an invite, F26) resolves its person by the link's
    # hash alone: nobody is signed in, and the link is what *establishes* the
    # tenant, as a heartbeat token does.
    "find_by_set_password_link",
    # An invite must say when an address belongs to another organization (D1),
    # and addresses are unique system-wide — `email_is_taken`'s reason.
    "find_by_email",
    # The platform's organization search (F31, ADR-018): nexovue's operator
    # searches every tenant by design, behind the platform gate — the role plus a
    # re-authentication no older than thirty minutes. Every read about one
    # organization after the search names it.
    "search_organizations",
    # A website payment buys one organization, and its processor id is unique
    # system-wide — `key_prefix_is_taken`'s reason.
    "payment_is_recorded",
    # nexovue's own operator, looked up by address at boot
    # (`docs/changes/super-admin-in-the-console/`). It belongs to the platform's
    # reserved organization, which this is what creates — so there is no tenant
    # to scope by yet, and scoping by the one it is about to make would be a
    # filter that can only ever match what it just wrote.
    "seed_super_admin",
    # An API token's hash (F36), exactly as an ingest key's prefix: it is what a
    # caller presents *before* any organization is known, so it establishes the
    # tenant rather than being filtered by one. The row it returns is refused
    # unless it is live, and every read after it is scoped by `reader.org_id`.
    "find_by_hash",
}


def _tenant_scoped_models() -> set[str]:
    """Every model class declaring an org_id column."""
    scoped = set()
    for name in dir(models):
        candidate = getattr(models, name)
        table = getattr(candidate, "__table__", None)
        if table is not None and TENANT_COLUMN in table.c:
            scoped.add(name)
    return scoped


SCOPED_MODELS: Final = _tenant_scoped_models()


def _query_functions() -> list[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Every function in the API that builds a statement."""
    found = []
    for path in sorted(API_SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found.append((path, node))
    return found


# The three ways a statement against a table is built. `insert` is absent on
# purpose: a row being written carries its tenant as a value, not as a filter.
STATEMENT_BUILDERS: Final = {"select", "delete", "update"}


def _models_queried_in(node: ast.AST) -> set[str]:
    """Models this function builds a select/delete/update against.

    Naming a model is not enough — `session.add(check)` names one and needs no
    filter. What matters is a statement that *reads or changes* rows, because
    that is what can reach another tenant's.
    """
    queried: set[str] = set()
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        builder = inner.func
        if not isinstance(builder, ast.Name) or builder.id not in STATEMENT_BUILDERS:
            continue
        for argument in inner.args:
            queried |= _model_names_in(argument)
    return queried


def _model_names_in(node: ast.AST) -> set[str]:
    """Model classes named in a statement builder's arguments.

    Covers both `select(Check)` and `select(Check.id)`.
    """
    found = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name) and inner.id in SCOPED_MODELS:
            found.add(inner.id)
        elif (
            isinstance(inner, ast.Attribute)
            and isinstance(inner.value, ast.Name)
            and inner.value.id in SCOPED_MODELS
        ):
            found.add(inner.value.id)
    return found


def _tenant_attributes_in_statements(node: ast.AST) -> set[str]:
    """Column names read off a model inside a select/delete/update.

    Scoped to the statement rather than the whole function, so that an `org_id`
    used only for a log line or passed through to another call does not count as
    a filter.
    """
    columns: set[str] = set()
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        builder = inner.func
        # A builder, or the `.where(...)` hanging off one — the filter lives on
        # the chain, not on the call that started it.
        starts_statement = isinstance(builder, ast.Name) and builder.id in STATEMENT_BUILDERS
        filters_statement = isinstance(builder, ast.Attribute) and builder.attr in {
            "where",
            "filter",
            "values",
        }
        if starts_statement or filters_statement:
            columns |= _columns_in(inner)
    return columns


def _columns_in(node: ast.AST) -> set[str]:
    return {
        inner.attr
        for inner in ast.walk(node)
        if isinstance(inner, ast.Attribute)
        and isinstance(inner.value, ast.Name)
        and inner.value.id in SCOPED_MODELS
    }


def test_the_models_this_check_guards_are_the_ones_that_carry_a_tenant() -> None:
    """Guards the check itself: an empty set would pass everything below."""
    assert SCOPED_MODELS, "no tenant-scoped models found; this test is not testing what it thinks"
    # The three M4 tables, named so a rename is visible here rather than silent.
    assert {"Check", "Integration", "DeployEvent"} <= SCOPED_MODELS


def test_the_source_this_check_walks_is_the_one_that_ships() -> None:
    assert (API_SOURCE / "nexovue_api" / "main.py").is_file(), f"no API source at {API_SOURCE}"


@pytest.mark.parametrize(
    "path,function", _query_functions(), ids=lambda value: getattr(value, "name", "")
)
def test_a_query_against_a_tenant_scoped_model_binds_the_tenant(
    path: Path, function: ast.FunctionDef | ast.AsyncFunctionDef
) -> None:
    """A function that names a tenant-scoped model must also name org_id.

    Coarse on purpose. It cannot tell a correct filter from a wrong one, but the
    mistake that happens is a query with no tenant predicate at all — and that
    is exactly what this catches.
    """
    used = _models_queried_in(function)
    if not used or function.name in EXEMPT:
        return

    # The column has to appear inside the statement, not merely in the
    # signature. A first version accepted any function that *took* an org_id,
    # which passed happily for a query that took one and ignored it — the
    # negative control caught it doing exactly that.
    mentions_tenant = TENANT_COLUMN in _tenant_attributes_in_statements(function)
    assert mentions_tenant, (
        f"{path.name}:{function.name} builds a statement against {sorted(used)} "
        f"without naming {TENANT_COLUMN}. Another tenant's rows would be returned. "
        f"If it is genuinely unscoped, add it to EXEMPT with the reason."
    )
