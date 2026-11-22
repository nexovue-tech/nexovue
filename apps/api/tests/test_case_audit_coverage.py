"""Every write route of F22 audits what it does, or says why it does not (F22 plan T8.8;
AC77).

Structural, in the shape `test_authorization_coverage.py` established: it reads the routers,
so a route added next month is judged the day it is written. For each mutating handler in
F22's routers the test finds the audit entries its work can reach (the handler, and every
function it calls by name inside `findings/` and `connections/`), and holds them to one of
two tables below:

- `AUDITED`: the entries the route must write. A route whose entry is deleted from the code
  fails here.
- `UNAUDITED`: a route that writes none, with the reason `design.md` (*Audit*) gives. A route
  that starts writing one fails here too, so the reason cannot go stale.

A route in neither table fails. So do two more rules that hold for every writer of a case's
state, the API's and the scheduler's alike (*Scheduler writers are included*): the function
that resolves, reopens or starts a case writes its audit row beside it, and the audit module
joins the caller's transaction and never commits one of its own, so the act and its record
commit together or not at all.

What this cannot tell: that the entry says the right thing, or that it is written in the same
transaction as an act it does not sit beside. `test_findings_cases_live.py` and
`test_findings_links_live.py` read the rows back for each act.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Final

import pytest
from environment import TEST_ENVIRONMENT
from fastapi.routing import APIRoute

import nexovue_api
import nexovue_scheduler
from nexovue_api.audit import service as audit
from nexovue_api.settings import Settings

API_ROOT: Final = Path(nexovue_api.__file__).resolve().parent
SCHEDULER_ROOT: Final = Path(nexovue_scheduler.__file__).resolve().parent

# F22's routers. The F5 routes beside them (`connections/fix/`, `findings/fixes/`) have their
# own audit suite, `test_fix_audit_live.py`.
ROUTER_FILES: Final = (
    API_ROOT / "findings" / "router.py",
    API_ROOT / "connections" / "router.py",
    API_ROOT / "connections" / "test_router.py",
)
# The packages whose functions a handler's work can reach, F5's code excluded.
REACHABLE_PACKAGES: Final = ("findings", "connections")
MUTATING_METHODS: Final = frozenset({"post", "patch", "put", "delete"})

# What F22 writes, as the audit module names it: a prefix says an entry belongs to F22's
# vocabulary, so an unaudited route reaching one is found out.
F22_AUDIT_PREFIXES: Final = ("CASE_", "LINK_", "CONNECTION_", "SERVICE_REPOSITORY_")
F22_AUDIT_WRITERS: Final = frozenset(
    {"record_case_action", "record_connection_event", "record_place_event"}
    | {"record_service_repository_changed"}
)

# Handler -> the audit entries its work must reach. Where the table says a name in capitals,
# it is the audit module's constant for the stored action; the rest are its writers.
AUDITED: Final[dict[str, frozenset[str]]] = {
    # PATCH /findings/{id}/case: start, assign, resolve, reopen.
    "change_case": frozenset({"CASE_STARTED", "CASE_ASSIGNED", "CASE_RESOLVED", "CASE_REOPENED"}),
    # POST .../notes: the note is on the timeline only (FD9), but a note on a finding with no
    # case starts one, and starting a case is audited. `design.md` says "none" for this row
    # and means the note; the case it can start is the entry.
    "add_case_note": frozenset({"CASE_STARTED"}),
    "delete_case_note": frozenset({"CASE_NOTE_DELETED"}),
    # The three actions F6 had, now case acts too (BR6, FR3): their own entry, and the
    # case's when they start or end one.
    "acknowledge_finding": frozenset({"record_finding_acknowledged", "CASE_STARTED"}),
    "mark_false_alarm": frozenset({"record_finding_false_alarm", "CASE_RESOLVED"}),
    "accept_new_normal": frozenset({"record_finding_new_normal", "CASE_RESOLVED"}),
    # POST .../links: a created issue (data leaves to a third party), or a reference.
    "create_issue": frozenset({"LINK_ISSUE_REQUESTED", "LINK_ADDED"}),
    "promote_issue": frozenset({"LINK_PROMOTED"}),
    "remove_issue": frozenset({"LINK_REMOVED"}),
    "start_github": frozenset({"CONNECTION_CREATED"}),
    "replace_credential": frozenset({"CONNECTION_CREDENTIAL_REPLACED"}),
    "disconnect": frozenset({"CONNECTION_DELETED"}),
    "change_place": frozenset(
        {"CONNECTION_PUBLIC_REPOSITORY_ALLOWED", "CONNECTION_PUBLIC_REPOSITORY_WITHDRAWN"}
    ),
    "map_service_repository": frozenset({"record_service_repository_changed"}),
    "unmap_service_repository": frozenset({"record_service_repository_changed"}),
    # F3's two routes that share this router; their own suites judge the entries' words.
    "rate_explanation": frozenset({"record_explanation_rated"}),
    "refresh_explanation_wording": frozenset({"record_ai_event"}),
}

UNAUDITED: Final[dict[str, str]] = {
    "retry_issue": "it repeats a recorded act (the click) and changes no permission or data",
    "receive_connection_webhook": "it changes no record but when to read next (ADR-035)",
    "complete_github": "it carries out a setup whose start is recorded (`connection.created`)",
    "refresh_github": "it reads which repositories the App reaches; no person's choice changes",
    "test_connection": "it reads: it makes a tracker answer, and records nothing a person did",
    "read_explanation_again": "F3: it reads the finding's facts again and costs no tokens (FR41)",
}

# The stored actions `design.md` (*Audit*) names, so a renamed constant is a decision.
STORED_ACTIONS: Final = {
    "CASE_STARTED": "case.started",
    "CASE_ASSIGNED": "case.assigned",
    "CASE_RESOLVED": "case.resolved",
    "CASE_REOPENED": "case.reopened",
    "CASE_NOTE_DELETED": "note.deleted",
    "LINK_ISSUE_REQUESTED": "link.issue_requested",
    "LINK_ADDED": "link.added",
    "LINK_PROMOTED": "link.promoted",
    "LINK_REMOVED": "link.removed",
    "CONNECTION_CREATED": "connection.created",
    "CONNECTION_CREDENTIAL_REPLACED": "connection.credential_replaced",
    "CONNECTION_DELETED": "connection.deleted",
    "CONNECTION_PUBLIC_REPOSITORY_ALLOWED": "connection.public_repository_allowed",
    "CONNECTION_PUBLIC_REPOSITORY_WITHDRAWN": "connection.public_repository_withdrawn",
}


# ---------------------------------------------------------------------- reading the code

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _is_fix_code(path: Path) -> bool:
    relative = path.relative_to(API_ROOT).parts
    return any(part.startswith("fix") for part in relative) or path.stem.startswith("fix")


def _functions(path: Path) -> Iterator[FunctionNode]:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def _index() -> dict[str, list[FunctionNode]]:
    """Every function in F22's packages by its simple name: a call by name reaches all that
    share it, which can only over-report what a route reaches."""
    index: dict[str, list[FunctionNode]] = {}
    for package in REACHABLE_PACKAGES:
        for path in sorted((API_ROOT / package).rglob("*.py")):
            if not _is_fix_code(path):
                for function in _functions(path):
                    index.setdefault(function.name, []).append(function)
    return index


def _called_names(function: FunctionNode) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def _audit_names(function: FunctionNode) -> set[str]:
    """What the function takes from the audit module: `audit.CASE_STARTED`, `audit.record_x`."""
    return {
        node.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "audit"
    }


def _reached(handler: str, index: dict[str, list[FunctionNode]]) -> set[str]:
    reached: set[str] = set()
    seen: set[str] = set()
    pending = [handler]
    while pending:
        name = pending.pop()
        if name in seen or name not in index:
            continue
        seen.add(name)
        for function in index[name]:
            reached |= _audit_names(function)
            pending.extend(_called_names(function))
    return reached


def _write_handlers() -> dict[str, str]:
    """Each mutating handler in F22's routers: its name, and the route as the file says it."""
    found: dict[str, str] = {}
    for path in ROUTER_FILES:
        for function in _functions(path):
            for decorator in function.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr in MUTATING_METHODS
                ):
                    route = ast.unparse(decorator.args[0]) if decorator.args else "''"
                    assert function.name not in found, f"two handlers named {function.name}"
                    found[function.name] = f"{decorator.func.attr.upper()} {route} ({path.name})"
    return found


# --------------------------------------------------------------------------- the tests


def _mounted_routes(routes: Iterable[Any]) -> Iterator[APIRoute]:
    """Every API route, through the wrappers `include_router` leaves in `app.routes`."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from _mounted_routes(route.original_router.routes)


def test_the_scan_sees_the_same_write_routes_the_application_serves() -> None:
    """The scan is only worth its verdict if it misses no route: every mutating route the
    application mounts from F22's router files is one the files' decorators show."""
    from nexovue_api.main import create_app

    app = create_app(Settings(**TEST_ENVIRONMENT))  # type: ignore[arg-type]
    router_modules = {
        f"nexovue_api.{path.relative_to(API_ROOT).with_suffix('').as_posix().replace('/', '.')}"
        for path in ROUTER_FILES
    }
    mounted = {
        route.endpoint.__name__
        for route in _mounted_routes(app.routes)
        if {method.lower() for method in route.methods or ()} & MUTATING_METHODS
        and route.endpoint.__module__ in router_modules
    }
    assert mounted == set(_write_handlers())


def test_every_write_route_is_audited_or_says_why_not() -> None:
    handlers = _write_handlers()
    undecided = sorted(set(handlers) - set(AUDITED) - set(UNAUDITED))
    assert not undecided, "a write route with no audit entry and no reason (AC77): " + ", ".join(
        f"{name} = {handlers[name]}" for name in undecided
    )
    both = sorted(set(AUDITED) & set(UNAUDITED))
    assert not both, f"in both tables: {both}"
    stale = sorted((set(AUDITED) | set(UNAUDITED)) - set(handlers))
    assert not stale, f"named here and no longer a route: {stale}"


@pytest.mark.parametrize("handler", sorted(AUDITED))
def test_an_audited_route_reaches_each_entry_it_must_write(handler: str) -> None:
    missing = AUDITED[handler] - _reached(handler, _index())
    assert not missing, f"{handler} no longer writes: {sorted(missing)}"


@pytest.mark.parametrize("handler", sorted(UNAUDITED))
def test_an_unaudited_route_reaches_none_of_f22s_entries(handler: str) -> None:
    reached = {
        name
        for name in _reached(handler, _index())
        if name.startswith(F22_AUDIT_PREFIXES) or name in F22_AUDIT_WRITERS
    }
    assert not reached, (
        f"{handler} writes {sorted(reached)}: say so in AUDITED ({UNAUDITED[handler]})"
    )


@pytest.mark.parametrize(("constant", "stored"), sorted(STORED_ACTIONS.items()))
def test_each_stored_action_is_the_name_the_design_gives(constant: str, stored: str) -> None:
    assert getattr(audit, constant) == stored


def test_the_audit_module_joins_the_callers_transaction_and_never_ends_one() -> None:
    """An entry commits with its act or not at all: the module only adds to the session it is
    given (its own opening rule)."""
    source = ast.parse((API_ROOT / "audit" / "service.py").read_text(encoding="utf-8"))
    ending = {"commit", "rollback", "begin", "begin_nested", "close"}
    found = sorted(
        {
            node.func.attr
            for node in ast.walk(source)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ending
        }
    )
    assert not found, f"audit/service.py calls {found}"


# What changes a case's state, as the shared statements and the events name it.
CASE_STATE_WRITERS: Final = frozenset(
    {"RESOLVE_CASE_SQL", "REOPEN_CASE_SQL", "case_started", "case_resolved", "case_reopened"}
)
# What writes a case's audit row: the API's helper and writers, and the scheduler's.
AUDIT_WRITERS: Final = frozenset({"_audit", "record_case_action", "record_audit"})


def _changes_case_state(function: FunctionNode) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and node.id in CASE_STATE_WRITERS:
            return True
        if isinstance(node, ast.Constant) and node.value in CASE_STATE_WRITERS:
            return True
    return False


def _writes_audit(function: FunctionNode) -> bool:
    return bool(_called_names(function) & AUDIT_WRITERS) or any(
        isinstance(node, ast.Name) and node.id == "RECORD_CASE_AUDIT_SQL"
        for node in ast.walk(function)
    )


def _case_writers(
    root: Path, *, packages: tuple[str, ...] | None
) -> list[tuple[Path, FunctionNode]]:
    paths = (
        sorted(root.rglob("*.py"))
        if packages is None
        else [path for package in packages for path in sorted((root / package).rglob("*.py"))]
    )
    return [
        (path, function)
        for path in paths
        if not path.is_relative_to(root / "audit")
        for function in _functions(path)
        if _changes_case_state(function)
    ]


def test_a_function_that_starts_resolves_or_reopens_a_case_writes_its_audit_row() -> None:
    """The API's writers and the scheduler's (the tracker sync, the sweep, the shared ending):
    each function that changes a case's state also writes the audit row, in its own
    transaction, naming nobody when the system acted."""
    writers = _case_writers(API_ROOT, packages=("findings",)) + _case_writers(
        SCHEDULER_ROOT, packages=None
    )
    assert len(writers) >= 4, "the scan found too few case writers to be reading the right code"
    unaudited = sorted(
        f"{path.name}:{function.name}" for path, function in writers if not _writes_audit(function)
    )
    assert not unaudited, f"changes a case's state and writes no audit row: {unaudited}"
